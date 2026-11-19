"""State classification sensors: creation, live updates, removal and old NVRs."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.cookienvr.const import EVENT_STATE_CHANGE

from .conftest import get_device, wait_for
from .fake_nvr import FakeNVR, make_state_sensor

SHUTTER = "sensor.front_door_shutter"
SINCE_MS = (int(time.time()) - 600) * 1000


def _shutter(**fields) -> dict:
    return make_state_sensor("a1b2c3d4e5f6", "cam1", "Shutter", **fields)


def _open_state() -> dict:
    return {"state": "open", "since": SINCE_MS, "score": 0.93}


async def test_sensor_exists_from_the_snapshot(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(**_open_state(), candidate={"label": "moving", "count": 1})]
    entry = await load()
    state = hass.states.get(SHUTTER)
    assert state.state == "open"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["closed", "moving", "open"]
    assert state.attributes["score"] == 0.93
    assert state.attributes["since"] == datetime.fromtimestamp(SINCE_MS / 1000, UTC)
    assert state.attributes["candidate"] == "moving"
    assert state.attributes["problem"] is None
    assert state.attributes["friendly_name"] == "Front Door Shutter"

    registry = er.async_get(hass)
    item = registry.async_get(SHUTTER)
    assert item.unique_id == f"{entry.entry_id}_cam1_state_a1b2c3d4e5f6"
    assert item.device_id == get_device(hass, entry, "cam1").id


async def test_no_verified_state_is_unknown_not_unavailable(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(problem={"code": "untrained", "message": "還沒訓練"})]
    await load()
    state = hass.states.get(SHUTTER)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["problem"] == "untrained"
    assert state.attributes["score"] is None and state.attributes["since"] is None


async def test_state_sensor_message_updates_the_entity(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(**_open_state())]
    await load()
    nvr.push("state-sensor", _shutter(state="closed", since=SINCE_MS + 1000, score=0.97))
    await wait_for(hass, lambda: hass.states.get(SHUTTER).state == "closed")
    assert hass.states.get(SHUTTER).attributes["score"] == 0.97

    nvr.push("state-sensor", _shutter(problem={"code": "camera-off", "message": "攝影機沒有畫面"}))
    await wait_for(hass, lambda: hass.states.get(SHUTTER).state == STATE_UNKNOWN)
    assert hass.states.get(SHUTTER).attributes["problem"] == "camera-off"


async def test_new_sensor_appears_and_removed_sensor_goes(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    await load()
    assert hass.states.get(SHUTTER) is None
    nvr.push("state-sensor", _shutter(state="open", since=SINCE_MS, score=0.9))
    await wait_for(hass, lambda: hass.states.get(SHUTTER) is not None)
    assert hass.states.get(SHUTTER).state == "open"

    garage = make_state_sensor("0f0f0f0f0f0f", "cam2", "Door", classes=("shut", "ajar"), state="shut")
    nvr.push("state-sensor", garage)
    await wait_for(hass, lambda: hass.states.get("sensor.garage_door") is not None)
    assert hass.states.get("sensor.garage_door").attributes["options"] == ["shut", "ajar"]

    nvr.push("state-sensor-removed", {"id": "a1b2c3d4e5f6"})
    await wait_for(hass, lambda: hass.states.get(SHUTTER) is None)
    assert er.async_get(hass).async_get(SHUTTER) is None
    assert hass.states.get("sensor.garage_door") is not None


async def test_class_changes_update_options_and_drop_a_stale_state(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(**_open_state())]
    await load()
    renamed = _shutter(classes=("closed", "half", "open"), **_open_state())
    renamed["config"]["name"] = "Roller shutter"
    nvr.push("state-sensor", renamed)
    await wait_for(hass, lambda: hass.states.get(SHUTTER).attributes["options"] == ["closed", "half", "open"])
    assert hass.states.get(SHUTTER).attributes["friendly_name"] == "Front Door Roller shutter"
    assert hass.states.get(SHUTTER).state == "open"

    dropped = _shutter(classes=("closed", "ajar"), **_open_state())
    nvr.push("state-sensor", dropped)
    await wait_for(hass, lambda: hass.states.get(SHUTTER).attributes["options"] == ["closed", "ajar"])
    assert hass.states.get(SHUTTER).state == STATE_UNKNOWN


async def test_removing_the_camera_removes_its_sensors(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(**_open_state())]
    await load()
    nvr.cameras = [c for c in nvr.cameras if c["id"] != "cam1"]
    nvr.push("cameras", nvr.cameras)
    await wait_for(hass, lambda: hass.states.get(SHUTTER) is None)


async def test_poll_finds_sensors_added_while_the_stream_was_down(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await wait_for(hass, lambda: nvr.stream_count == 1)
    nvr.state_sensors = [_shutter(**_open_state())]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await wait_for(hass, lambda: hass.states.get(SHUTTER) is not None)
    assert hass.states.get(SHUTTER).state == "open"


async def test_sensor_follows_a_reconnect_snapshot(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(**_open_state())]
    await load()
    nvr.state_sensors = [_shutter(state="closed", since=SINCE_MS, score=0.9)]
    nvr.drop_streams()
    await wait_for(hass, lambda: hass.states.get(SHUTTER).state == "closed")


async def test_unavailable_when_the_nvr_is_gone(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(**_open_state())]
    await load()
    await nvr.stop()
    await wait_for(hass, lambda: hass.states.get(SHUTTER).state == STATE_UNAVAILABLE)
    await nvr.start()
    await wait_for(hass, lambda: hass.states.get(SHUTTER).state == "open", timeout=8)


async def test_nvr_without_state_classification_still_works(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.legacy = True
    await load()
    assert hass.states.get("camera.front_door") is not None
    assert hass.states.get(SHUTTER) is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.front_door_online").state == "on"


async def test_state_change_fires_an_event(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.state_sensors = [_shutter(**_open_state())]
    entry = await load()
    fired = async_capture_events(hass, EVENT_STATE_CHANGE)
    change = {
        "id": 42,
        "sensorId": "a1b2c3d4e5f6",
        "cameraId": "cam1",
        "at": SINCE_MS + 5000,
        "from": "open",
        "to": "closed",
        "score": 0.95,
        "thumbUrl": "/api/state-changes/42/thumb.jpg",
    }
    nvr.push("state-change", change)
    await wait_for(hass, lambda: len(fired) == 1)
    data = fired[0].data
    assert data["camera"] == "Front Door" and data["camera_id"] == "cam1"
    assert data["sensor"] == "Shutter" and data["sensor_id"] == "a1b2c3d4e5f6"
    assert data["from"] == "open" and data["to"] == "closed" and data["score"] == 0.95
    assert data["device_id"] == get_device(hass, entry, "cam1").id
    assert data["thumbnail_url"] == f"{nvr.url}/api/state-changes/42/thumb.jpg?token=cnvr_testtoken"
    assert data["changed_at"] == datetime.fromtimestamp((SINCE_MS + 5000) / 1000, UTC).isoformat()

    nvr.push("state-change", {**change, "id": 43, "from": None, "thumbUrl": None})
    await wait_for(hass, lambda: len(fired) == 2)
    assert fired[1].data["from"] is None and fired[1].data["thumbnail_url"] is None

    nvr.push("state-change", {**change, "id": 44, "cameraId": "gone"})
    nvr.push("motion", {"cameraId": "cam1", "active": True})
    await wait_for(hass, lambda: hass.states.get("binary_sensor.front_door_motion").state == "on")
    assert len(fired) == 2


async def test_sensor_keeps_one_entity_across_a_reload(
    hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry
) -> None:
    await wait_for(hass, lambda: nvr.stream_count == 1)
    nvr.push("state-sensor", _shutter(state="open", since=SINCE_MS, score=0.9))
    await wait_for(hass, lambda: hass.states.get(SHUTTER) is not None)
    nvr.state_sensors = [_shutter(state="open", since=SINCE_MS, score=0.9)]
    assert await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    ids = [e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), setup.entry_id)]
    assert ids.count(SHUTTER) == 1
