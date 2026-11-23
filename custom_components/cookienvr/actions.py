"""PTZ and export logic behind the services and the PTZ buttons."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import CookieNVRApiError, CookieNVRClient, CookieNVRConnectionError
from .const import DOMAIN, EXPORT_POLL_SECONDS, EXPORT_TIMEOUT
from .coordinator import CookieNVRCoordinator
from .models import Json, camera_name, preset_options

CONTINUOUS_RESEND_SECONDS = 0.5


def _invalid(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders)


def _api_error(err: CookieNVRApiError | CookieNVRConnectionError) -> HomeAssistantError:
    message = err.message if isinstance(err, CookieNVRApiError) else str(err)
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": message}
    )


def _ptz_caps(coordinator: CookieNVRCoordinator, camera_id: str) -> Json:
    ptz = coordinator.data.ptz.get(camera_id, {})
    if ptz.get("supported") is not True:
        raise _invalid("ptz_not_supported", camera=camera_name(coordinator.data.cameras[camera_id]))
    return ptz


async def ptz_move(
    coordinator: CookieNVRCoordinator,
    camera_id: str,
    *,
    pan: float = 0,
    tilt: float = 0,
    zoom: float = 0,
    mode: str | None = None,
    duration: float | None = None,
    speed: float | None = None,
) -> None:
    caps = _ptz_caps(coordinator, camera_id)
    if pan == 0 and tilt == 0 and zoom == 0:
        raise _invalid("ptz_no_direction")
    if zoom != 0 and not caps.get("zoom"):
        raise _invalid("ptz_no_zoom", camera=camera_name(coordinator.data.cameras[camera_id]))
    if mode is None:
        mode = "relative" if caps.get("relative") else "continuous"
    if not caps.get(mode):
        raise _invalid("ptz_mode_unsupported", mode=mode)

    command: Json = {"action": "move", "pan": pan, "tilt": tilt, "zoom": zoom, "mode": mode}
    if speed is not None:
        command["speed"] = speed
    client = coordinator.client
    try:
        if mode == "relative":
            await client.ptz_command(camera_id, command)
            return
        deadline = time.monotonic() + (duration if duration is not None else 0.5)
        try:
            while True:
                await client.ptz_command(camera_id, command)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(CONTINUOUS_RESEND_SECONDS, remaining))
        finally:
            await asyncio.shield(client.ptz_command(camera_id, {"action": "stop"}))
    except (CookieNVRApiError, CookieNVRConnectionError) as err:
        raise _api_error(err) from err


def _resolve_preset(ptz: Json, wanted: str) -> str:
    options = preset_options(ptz)
    if wanted in options:
        return options[wanted]
    if wanted in options.values():
        return wanted
    raise _invalid("preset_not_found", preset=wanted)


async def ptz_goto_preset(coordinator: CookieNVRCoordinator, camera_id: str, preset: str) -> None:
    token = _resolve_preset(_ptz_caps(coordinator, camera_id), preset)
    try:
        await coordinator.client.ptz_command(camera_id, {"action": "preset", "token": token})
    except (CookieNVRApiError, CookieNVRConnectionError) as err:
        raise _api_error(err) from err


async def ptz_manage_preset(
    coordinator: CookieNVRCoordinator, camera_id: str, action: str, preset: str | None, name: str | None
) -> None:
    ptz = _ptz_caps(coordinator, camera_id)
    if not coordinator.data.admin:
        raise _invalid("admin_required")
    client = coordinator.client
    try:
        if action == "save":
            if not name:
                raise _invalid("preset_name_required")
            token = _resolve_preset(ptz, preset) if preset else None
            result = await client.ptz_save_preset(camera_id, name, token)
        else:
            if not preset:
                raise _invalid("preset_required")
            result = await client.ptz_remove_preset(camera_id, _resolve_preset(ptz, preset))
    except (CookieNVRApiError, CookieNVRConnectionError) as err:
        raise _api_error(err) from err
    ptz["presets"] = result.get("presets", ptz.get("presets", []))
    coordinator.async_set_updated_data(coordinator.data)


def media_target(hass: HomeAssistant) -> tuple[str, Path]:
    """The media source id and directory exported clips go to."""
    dirs = hass.config.media_dirs
    if not dirs:
        raise _invalid("no_media_dir")
    source_id = "local" if "local" in dirs else next(iter(dirs))
    return source_id, Path(dirs[source_id]) / "cookienvr"


async def _export_to_media(
    hass: HomeAssistant,
    client: CookieNVRClient,
    create_job: Callable[[], Awaitable[Json]],
    filename: str | None,
) -> dict[str, str]:
    """Run an export job to completion, download it into the media folder and delete it on the NVR."""
    source_id, directory = media_target(hass)
    try:
        job = await create_job()
        async with asyncio.timeout(EXPORT_TIMEOUT):
            while job["state"] in ("queued", "running"):
                await asyncio.sleep(EXPORT_POLL_SECONDS)
                job = await client.get_export(job["id"])
        if job["state"] != "done":
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="export_failed",
                translation_placeholders={"error": job.get("error") or job["state"]},
            )
        name = Path(filename).name if filename else job["fileName"]
        if not name.lower().endswith(".mp4"):
            name += ".mp4"
        dest = directory / name
        await hass.async_add_executor_job(lambda: directory.mkdir(parents=True, exist_ok=True))
        await client.download_export(job["id"], dest)
        try:
            await client.delete_export(job["id"])
        except (CookieNVRApiError, CookieNVRConnectionError):
            pass  # it expires on its own
    except TimeoutError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="export_failed", translation_placeholders={"error": "timeout"}
        ) from err
    except (CookieNVRApiError, CookieNVRConnectionError) as err:
        raise _api_error(err) from err
    return {"path": str(dest), "media_content_id": f"media-source://media_source/{source_id}/cookienvr/{name}"}


async def export_clip(
    hass: HomeAssistant,
    coordinator: CookieNVRCoordinator,
    camera_id: str,
    start: datetime,
    end: datetime,
    filename: str | None,
) -> dict[str, str]:
    if end <= start:
        raise _invalid("export_bad_range")
    client = coordinator.client
    return await _export_to_media(
        hass,
        client,
        lambda: client.create_export(
            camera_id, int(start.timestamp() * 1000), int(end.timestamp() * 1000), hass.config.time_zone
        ),
        filename,
    )


async def export_event_clip(
    hass: HomeAssistant,
    coordinator: CookieNVRCoordinator,
    event_id: int,
    boxes: bool,
    filename: str | None,
) -> dict[str, str]:
    client = coordinator.client

    async def create_job() -> Json:
        try:
            return await client.create_event_clip(event_id, boxes, hass.config.time_zone)
        except CookieNVRApiError as err:
            if err.status != 404:
                raise
            # an NVR without the route answers http.notFound, a missing event answers events.notFound
            if err.code == "events.notFound":
                raise _invalid("event_not_found", event=str(event_id)) from err
            raise _invalid("event_clip_unsupported") from err

    return await _export_to_media(hass, client, create_job, filename)
