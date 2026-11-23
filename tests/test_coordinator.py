"""SSE handling, reconnecting, polling, availability and reauth."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.cookienvr.const import EVENT_COOKIENVR

from .conftest import get_device, wait_for
from .fake_nvr import FakeNVR, make_camera, make_event

T0 = (int(time.time()) - 3600) * 1000  # an hour ago, inside the poll window
MOTION = "binary_sensor.front_door_motion"
ONLINE = "binary_sensor.front_door_online"


async def _connected(hass: HomeAssistant, nvr: FakeNVR) -> None:
    await wait_for(hass, lambda: nvr.stream_count == 1)


async def test_motion_and_event_messages(hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry) -> None:
    await _connected(hass, nvr)
    fired = async_capture_events(hass, EVENT_COOKIENVR)
    assert hass.states.get(MOTION).state == STATE_OFF

    nvr.push("motion", {"cameraId": "cam1", "active": True})
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_ON)
    nvr.push("motion", {"cameraId": "cam1", "active": False})
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_OFF)

    start = make_event(7, "cam1", T0)
    nvr.push("event", {"phase": "start", "event": start})
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_ON)
    nvr.push("event", {"phase": "end", "event": {**start, "endMs": T0 + 30_000}})
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_OFF)

    assert [e.data["state"] for e in fired] == ["start", "end"]
    data = fired[0].data
    assert data["camera"] == "Front Door" and data["camera_id"] == "cam1" and data["event_id"] == 7
    assert data["labels"] == [] and data["score"] == 0.42
    assert data["thumbnail_url"] == f"{nvr.url}/api/events/7/thumb.jpg?boxes=1&token=cnvr_testtoken"
    assert data["thumbnail_raw_url"] == f"{nvr.url}/api/events/7/thumb.jpg?token=cnvr_testtoken"
    assert data["playback_url"] == f"{nvr.url}/#/recordings?camera=cam1"
    device = get_device(hass, setup, "cam1")
    assert data["device_id"] == device.id


async def test_camera_status_message_updates_online(hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry) -> None:
    await _connected(hass, nvr)
    assert hass.states.get(ONLINE).state == STATE_ON
    nvr.push("camera", {**make_camera("cam1", "Front Door"), "online": False})
    await wait_for(hass, lambda: hass.states.get(ONLINE).state == STATE_OFF)


async def test_camera_added_and_removed_without_reload(
    hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    await _connected(hass, nvr)
    ent = er.async_get(hass)
    assert hass.states.get("camera.garage")

    nvr.cameras.append(make_camera("cam3", "Backyard", 2))
    nvr.ptz["cam3"] = {"supported": False, "reason": "no-onvif"}
    nvr.push("cameras", nvr.cameras)
    await wait_for(hass, lambda: hass.states.get("camera.backyard") is not None)
    assert hass.states.get("binary_sensor.backyard_motion")
    assert get_device(hass, setup, "cam3")

    nvr.cameras = [c for c in nvr.cameras if c["id"] != "cam2"]
    nvr.push("cameras", nvr.cameras)
    await wait_for(hass, lambda: hass.states.get("camera.garage") is None)
    assert get_device(hass, setup, "cam2") is None
    assert not [e for e in ent.entities.values() if e.unique_id.startswith(f"{setup.entry_id}_cam2_")]
    assert "deprecated" not in caplog.text and "will stop working" not in caplog.text


async def test_camera_rename_follows(hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry) -> None:
    await _connected(hass, nvr)
    renamed = make_camera("cam1", "Porch")
    nvr.cameras[0] = renamed
    nvr.push("cameras", nvr.cameras)
    await wait_for(
        hass,
        lambda: get_device(hass, setup, "cam1").name == "Porch",
    )


async def test_reconnect_fetches_full_state_and_fires_missed_end(
    hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry
) -> None:
    await _connected(hass, nvr)
    fired = async_capture_events(hass, EVENT_COOKIENVR)
    start = make_event(9, "cam1", T0)
    nvr.events.append(start)
    nvr.push("event", {"phase": "start", "event": start})
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_ON)

    # the connection drops, the event ends while HA is not listening
    nvr.events[0] = {**start, "endMs": T0 + 60_000}
    nvr.drop_streams()
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_OFF)
    await _connected(hass, nvr)
    assert [e.data["state"] for e in fired] == ["start", "end"]
    assert hass.states.get("sensor.front_door_last_event").attributes["in_progress"] is False


async def test_nvr_down_marks_unavailable_then_recovers(
    hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry
) -> None:
    await _connected(hass, nvr)
    await nvr.stop()
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_UNAVAILABLE)
    assert hass.states.get("camera.front_door").state == STATE_UNAVAILABLE

    await nvr.start()
    await wait_for(hass, lambda: hass.states.get(MOTION).state == STATE_OFF, timeout=8)
    assert hass.states.get("camera.front_door").state != STATE_UNAVAILABLE


async def test_poll_every_minute_syncs_without_sse(hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry) -> None:
    await _connected(hass, nvr)
    nvr.cameras.append(make_camera("cam3", "Backyard", 2))
    nvr.ptz["cam3"] = {"supported": False, "reason": "no-onvif"}
    nvr.events.append(make_event(3, "cam3", T0))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await wait_for(hass, lambda: hass.states.get("camera.backyard") is not None)
    assert hass.states.get("binary_sensor.backyard_motion").state == STATE_ON


async def test_event_already_running_at_startup_is_not_announced(
    hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry
) -> None:
    nvr.events.append(make_event(4, "cam1", T0))
    fired = async_capture_events(hass, EVENT_COOKIENVR)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(hass, lambda: nvr.stream_count == 1)
    assert hass.states.get(MOTION).state == STATE_ON
    assert fired == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_token_revoked_while_running_starts_reauth(
    hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry
) -> None:
    await _connected(hass, nvr)
    nvr.token = "cnvr_revoked"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await wait_for(
        hass,
        lambda: any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()),
    )
    assert setup.state is ConfigEntryState.LOADED
    assert hass.states.get(MOTION).state == STATE_UNAVAILABLE


async def test_token_revoked_on_stream_starts_reauth(hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry) -> None:
    await _connected(hass, nvr)
    nvr.token = "cnvr_revoked"
    nvr.drop_streams()
    await wait_for(
        hass,
        lambda: any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()),
    )
