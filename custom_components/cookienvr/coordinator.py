"""Keeps NVR state current: a 60 s poll plus a push stream over SSE."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CookieNVRApiError,
    CookieNVRAuthError,
    CookieNVRClient,
    CookieNVRConnectionError,
)
from .const import DOMAIN, EVENT_COOKIENVR, LOGGER, POLL_INTERVAL, SSE_BACKOFF_MAX, SSE_BACKOFF_MIN
from .models import Json, NVRState, camera_name, event_labels

type CookieNVRConfigEntry = ConfigEntry[CookieNVRCoordinator]

RECENT_EVENTS_WINDOW = timedelta(days=7)
FIRED_MEMORY = 512


def camera_device_id(entry_id: str, camera_id: str) -> str:
    return f"{entry_id}:{camera_id}"


class CookieNVRCoordinator(DataUpdateCoordinator[NVRState]):
    """Owns the client, the state and the SSE task."""

    config_entry: CookieNVRConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CookieNVRConfigEntry,
        client: CookieNVRClient,
        info: Json,
    ) -> None:
        super().__init__(hass, LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_INTERVAL)
        self.client = client
        self.info = info
        self.image_updated: dict[str, datetime] = {}
        self._ptz_signature: dict[str, str] = {}
        self._fired: dict[tuple[int, str], None] = {}
        self._sse_task: asyncio.Task | None = None

    @property
    def state(self) -> NVRState:
        return self.data

    # ---- polling -------------------------------------------------------

    async def _async_update_data(self) -> NVRState:
        previous = self.data
        try:
            me = await self.client.me()
            cameras = await self.client.cameras()
            since = int((datetime.now(UTC) - RECENT_EVENTS_WINDOW).timestamp() * 1000)
            events = await self.client.events(since)
            admin = me.get("role") == "admin"
            system = None
            if admin:
                try:
                    system = await self.client.system()
                except CookieNVRApiError as err:
                    if err.status != 403:
                        raise
                    admin = False
        except CookieNVRAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (CookieNVRConnectionError, CookieNVRApiError) as err:
            raise UpdateFailed(f"Error talking to CookieNVR: {err}") from err

        state = previous or NVRState()
        state.admin = admin
        state.username = str(me.get("username", ""))
        state.system = system
        if system is not None:
            state.workers = {w["id"]: w for w in system.get("workers", [])}
        else:
            state.workers = {}
        self._replace_cameras(state, cameras)

        latest: dict[str, Json] = {}
        finished: dict[int, Json] = {}
        active: list[Json] = []
        for event in events:  # ascending by start time
            latest[event["cameraId"]] = event
            if event.get("endMs") is None:
                active.append(event)
            else:
                finished[event["id"]] = event
        for camera_id, event in latest.items():
            if camera_id in state.cameras:
                state.last_event[camera_id] = event
        for event in events:
            _note_labels(state, event, datetime.fromtimestamp(event["startMs"] / 1000, UTC), replace=False)
        self._reconcile_active(state, active, finished, silent=previous is None)
        await self._refresh_ptz(state)
        return state

    def _replace_cameras(self, state: NVRState, cameras: list[Json]) -> None:
        state.cameras = {c["id"]: c for c in sorted(cameras, key=lambda c: c.get("position", 0))}
        for mapping in (state.active, state.last_event, state.ptz, self.image_updated, self._ptz_signature):
            for gone in set(mapping) - set(state.cameras):
                del mapping[gone]
        state.motion &= set(state.cameras)

    async def _refresh_ptz(self, state: NVRState) -> None:
        targets: list[str] = []
        for camera_id, camera in state.cameras.items():
            signature = json.dumps(camera.get("config", {}).get("connection", {}), sort_keys=True)
            known = state.ptz.get(camera_id)
            if known is None or known.get("supported") or self._ptz_signature.get(camera_id) != signature:
                targets.append(camera_id)
                self._ptz_signature[camera_id] = signature
        if not targets:
            return
        results = await asyncio.gather(*(self.client.ptz_info(c) for c in targets), return_exceptions=True)
        for camera_id, result in zip(targets, results, strict=True):
            if isinstance(result, CookieNVRAuthError):
                raise ConfigEntryAuthFailed(str(result)) from result
            if isinstance(result, BaseException):
                LOGGER.debug("PTZ info for %s unavailable: %s", camera_id, result)
                # keep the last answer; a camera we never reached is treated as unsupported for now
                state.ptz.setdefault(camera_id, {"supported": False, "reason": "unreachable"})
                self._ptz_signature.pop(camera_id, None)
            else:
                state.ptz[camera_id] = result

    # ---- events --------------------------------------------------------

    def _reconcile_active(
        self, state: NVRState, active: list[Json], finished: dict[int, Json], *, silent: bool = False
    ) -> None:
        """Make `state.active` equal to `active`, firing start/end for whatever changed unseen.

        `silent` is for the first load: events already running when HA starts are not news.
        """
        new_by_camera: dict[str, dict[int, Json]] = {}
        for event in active:
            if event["cameraId"] in state.cameras:
                new_by_camera.setdefault(event["cameraId"], {})[event["id"]] = event
        old = {e["id"]: e for events in state.active.values() for e in events.values()}
        new = {i: e for events in new_by_camera.values() for i, e in events.items()}
        now = datetime.now(UTC)
        for event_id, event in new.items():
            _note_labels(state, event, now)
            if event_id in old:
                # labels confirmed while the stream was down
                self._fire("update", event, event_labels(event) - event_labels(old[event_id]))
            elif silent:
                self._fired[(event_id, "start")] = None
            else:
                self._fire("start", event)
        for event_id, event in old.items():
            if event_id not in new:
                ended = finished.get(event_id, event)
                if ended.get("endMs") is None:
                    ended = {**ended, "endMs": int(datetime.now(UTC).timestamp() * 1000)}
                state.last_event[ended["cameraId"]] = ended
                self._fire("end", ended)
        state.active = new_by_camera

    def _fire(self, phase: str, event: Json, new_labels: set[str] | None = None) -> None:
        """Fire `cookienvr_event`. `update` fires only when object classes were added, once per set of labels."""
        if phase == "update":
            if not new_labels:
                return
            key = (event["id"], "update:" + ",".join(sorted(event_labels(event))))
        else:
            key = (event["id"], phase)
            new_labels = event_labels(event) if phase == "start" else set()
        if key in self._fired:
            return
        self._fired[key] = None
        while len(self._fired) > FIRED_MEMORY:
            del self._fired[next(iter(self._fired))]
        camera_id = event["cameraId"]
        camera = self.data.cameras.get(camera_id) if self.data else None
        entry = self.config_entry
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, camera_device_id(entry.entry_id, camera_id)), entry.entry_id
        )
        self.hass.bus.async_fire(
            EVENT_COOKIENVR,
            {
                "camera": camera_name(camera) if camera else camera_id,
                "camera_id": camera_id,
                "device_id": device.id if device else None,
                "event_id": event["id"],
                "state": phase,
                "score": event.get("score"),
                "labels": list(event.get("labels") or []),
                "new_labels": sorted(new_labels),
                "thumbnail_url": (
                    self.client.token_url(f"/api/events/{event['id']}/thumb.jpg") if event.get("hasThumb") else None
                ),
                "playback_url": f"{self.client.base_url}/#/recordings?camera={camera_id}",
            },
        )

    # ---- SSE -----------------------------------------------------------

    def start_stream(self) -> None:
        self._sse_task = self.config_entry.async_create_background_task(
            self.hass, self._run_stream(), f"{DOMAIN} event stream"
        )

    async def async_shutdown(self) -> None:
        if self._sse_task is not None:
            self._sse_task.cancel()
            await asyncio.gather(self._sse_task, return_exceptions=True)
            self._sse_task = None
        await super().async_shutdown()

    async def _run_stream(self) -> None:
        backoff = SSE_BACKOFF_MIN
        failures = 0
        while True:
            try:
                async for kind, payload in self.client.stream():
                    if kind == "snapshot":
                        backoff, failures = SSE_BACKOFF_MIN, 0
                    await self._handle_message(kind, payload)
                raise CookieNVRConnectionError("stream closed")
            except asyncio.CancelledError:
                raise
            except CookieNVRAuthError:
                self.config_entry.async_start_reauth(self.hass)
                return
            except (CookieNVRConnectionError, CookieNVRApiError) as err:
                failures += 1
                LOGGER.debug("Event stream lost (%s), retrying in %ss", err, backoff)
                # one dropped connection is normal; a failed reconnect means the NVR is gone
                if failures >= 2:
                    self.async_set_update_error(UpdateFailed(f"CookieNVR stream unavailable: {err}"))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, SSE_BACKOFF_MAX)

    async def _handle_message(self, kind: str, payload: Any) -> None:
        state = self.data
        if state is None:
            return
        refresh = False
        if kind == "snapshot":
            self._replace_cameras(state, payload.get("cameras", []))
            workers = payload.get("workers")
            if workers is not None:
                state.workers = {w["id"]: w for w in workers}
            state.motion = set()
            self._reconcile_active(state, payload.get("events", []), {})
            refresh = True  # snapshot has no history, system usage or PTZ details
        elif kind == "cameras":
            known = set(state.cameras)
            self._replace_cameras(state, payload)
            refresh = bool(set(state.cameras) - known)
        elif kind == "camera":
            if payload["id"] in state.cameras:
                state.cameras[payload["id"]] = payload
            else:
                refresh = True
        elif kind == "worker":
            if payload.get("state") == "stopped":
                state.workers.pop(payload["id"], None)
            else:
                state.workers[payload["id"]] = payload
        elif kind == "motion":
            (state.motion.add if payload.get("active") else state.motion.discard)(payload["cameraId"])
        elif kind == "event":
            self._apply_event(state, payload["phase"], payload["event"])
        else:
            return
        self.async_set_updated_data(state)
        if refresh:
            await self.async_refresh()

    def _apply_event(self, state: NVRState, phase: str, event: Json) -> None:
        camera_id = event["cameraId"]
        if camera_id not in state.cameras:
            return
        now = datetime.now(UTC)
        state.last_event[camera_id] = event
        self.image_updated[camera_id] = now
        _note_labels(state, event, now)
        events = state.active.setdefault(camera_id, {})
        known = events.get(event["id"])
        if phase == "update" and event.get("endMs") is not None:
            phase = "end"
        if phase == "update" and known is None:
            # the start went by while we were away
            phase = "start"
        if phase in ("start", "update"):
            events[event["id"]] = event
        else:
            events.pop(event["id"], None)
        if not events:
            del state.active[camera_id]
        self._fire(phase, event, event_labels(event) - event_labels(known) if phase == "update" else None)

    # ---- device registry -----------------------------------------------

    @callback
    def sync_devices(self) -> None:
        """Remove devices of cameras deleted on the NVR and follow renames."""
        if not self.last_update_success or not self.data:
            return
        entry = self.config_entry
        registry = dr.async_get(self.hass)
        wanted = {camera_device_id(entry.entry_id, cid): camera_name(cam) for cid, cam in self.data.cameras.items()}
        prefix = f"{entry.entry_id}:"
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            ident = next((i for d, i in device.identifiers if d == DOMAIN and i.startswith(prefix)), None)
            if ident is None:
                continue
            if ident not in wanted:
                registry.async_remove_device(device.id)
            elif device.name != wanted[ident]:
                registry.async_update_device(device.id, name=wanted[ident])


def _note_labels(state: NVRState, event: Json, at: datetime, *, replace: bool = True) -> None:
    """Remember which event last carried each label, and when it was first seen in that event."""
    seen = state.label_seen.setdefault(event["cameraId"], {})
    for label in event_labels(event):
        current = seen.get(label)
        if current is not None and (current["event_id"] == event["id"] or not replace and current["at"] >= at):
            continue
        seen[label] = {"event_id": event["id"], "at": at}
