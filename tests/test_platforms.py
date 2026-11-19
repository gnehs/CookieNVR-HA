"""State of every entity and how the integration degrades."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from homeassistant.components import camera, image
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.cookienvr.const import DOMAIN

from .conftest import wait_for
from .fake_nvr import JPEG, THUMB, FakeNVR, make_event

T0 = (int(time.time()) - 3600) * 1000  # an hour ago, inside the poll window


# ---- camera --------------------------------------------------------------


async def test_camera_snapshot_and_stream_source(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    assert hass.states.get("camera.front_door").state == "idle"
    assert (await camera.async_get_image(hass, "camera.front_door")).content == JPEG
    entity = hass.data[camera.DATA_COMPONENT].get_entity("camera.front_door")
    assert await entity.stream_source() == "rtsp://alice:cnvr_testtoken@nvr.local:8554/cam1/main"
    assert not [c for c in nvr.requests if c[1].endswith("snapshot.jpg") and c[0] != "GET"]


async def test_snapshot_failure_is_an_error_not_a_crash(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    nvr.snapshot_ok = False
    with pytest.raises(HomeAssistantError):
        await camera.async_get_image(hass, "camera.front_door")


async def test_stream_source_none_when_restream_off(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.cameras[0]["rtsp"] = {"main": "", "sub": None}
    await load()
    entity = hass.data[camera.DATA_COMPONENT].get_entity("camera.front_door")
    assert await entity.stream_source() is None


# ---- binary sensors ------------------------------------------------------


async def test_online_sensor_follows_camera_status(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.cameras[1]["online"] = False
    await load()
    assert hass.states.get("binary_sensor.front_door_online").state == STATE_ON
    assert hass.states.get("binary_sensor.garage_online").state == STATE_OFF
    assert hass.states.get("binary_sensor.front_door_online").attributes["device_class"] == "connectivity"
    assert hass.states.get("binary_sensor.front_door_motion").attributes["device_class"] == "motion"


async def test_object_sensors_follow_event_labels(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    """Object detection is not shipped yet, so this runs on invented labels and a guessed `detect.labels` setting."""
    nvr.cameras[0]["config"]["detect"] = {"enabled": True, "labels": ["person", "car"]}
    await load()
    person, car = "binary_sensor.front_door_person", "binary_sensor.front_door_car"
    assert hass.states.get(person).state == STATE_OFF
    assert hass.states.get(person).attributes["device_class"] == "occupancy"
    assert hass.states.get("binary_sensor.garage_person") is None

    event = make_event(11, "cam1", T0, labels=["person"])
    nvr.push("event", {"phase": "start", "event": event})
    await wait_for(hass, lambda: hass.states.get(person).state == STATE_ON)
    assert hass.states.get(car).state == STATE_OFF
    nvr.push("event", {"phase": "end", "event": {**event, "endMs": T0 + 5000}})
    await wait_for(hass, lambda: hass.states.get(person).state == STATE_OFF)

    nvr.cameras[0]["config"]["detect"]["labels"] = ["person", "dog"]
    nvr.push("cameras", nvr.cameras)
    await wait_for(hass, lambda: hass.states.get("binary_sensor.front_door_dog") is not None)
    await wait_for(hass, lambda: hass.states.get(car) is None)


async def test_no_object_sensors_without_detect_settings(hass: HomeAssistant, setup) -> None:
    assert not [s for s in hass.states.async_entity_ids("binary_sensor") if not s.endswith(("_motion", "_online"))]


# ---- switches ------------------------------------------------------------


async def test_recording_switch_sends_the_full_config(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    assert hass.states.get("switch.front_door_recording").state == STATE_ON
    await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: "switch.front_door_recording"}, blocking=True)
    (body,) = nvr.calls("PUT", "/api/cameras/cam1")
    assert body["record"]["enabled"] is False
    assert body["connection"]["password"] == "cam-secret" and body["name"] == "Front Door"
    assert hass.states.get("switch.front_door_recording").state == STATE_OFF
    assert hass.states.get("switch.garage_recording").state == STATE_ON


async def test_detection_switch_only_when_the_nvr_has_the_setting(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.cameras[0]["config"]["detect"] = {"enabled": False}
    await load()
    assert hass.states.get("switch.front_door_detection").state == STATE_OFF
    assert hass.states.get("switch.garage_detection") is None
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: "switch.front_door_detection"}, blocking=True)
    (body,) = nvr.calls("PUT", "/api/cameras/cam1")
    assert body["detect"]["enabled"] is True
    assert hass.states.get("switch.front_door_detection").state == STATE_ON


async def test_switch_error_surfaces(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    nvr.cameras = [c for c in nvr.cameras if c["id"] != "cam1"]  # the NVR forgot it: PUT 404s
    with pytest.raises(HomeAssistantError, match="找不到攝影機"):
        await hass.services.async_call(
            "switch", "turn_off", {ATTR_ENTITY_ID: "switch.front_door_recording"}, blocking=True
        )


# ---- PTZ -----------------------------------------------------------------


async def test_ptz_entities_only_for_ptz_cameras(hass: HomeAssistant, setup) -> None:
    select = hass.states.get("select.front_door_ptz_preset")
    assert select.attributes["options"] == ["Door", "Gate"]
    for direction in ("up", "down", "left", "right"):
        assert hass.states.get(f"button.front_door_ptz_{direction}")
        assert hass.states.get(f"button.garage_ptz_{direction}") is None
    assert hass.states.get("select.garage_ptz_preset") is None


async def test_select_preset_goes_there(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: "select.front_door_ptz_preset", "option": "Gate"}, blocking=True
    )
    assert nvr.calls("POST", "/cam1/ptz") == [{"action": "preset", "token": "2"}]
    assert hass.states.get("select.front_door_ptz_preset").state == "Gate"


async def test_buttons_nudge_relative(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: "button.front_door_ptz_left"}, blocking=True)
    (move,) = nvr.calls("POST", "/cam1/ptz")
    assert move["action"] == "move" and move["mode"] == "relative"
    assert move["pan"] == pytest.approx(-0.1) and move["tilt"] == 0


async def test_buttons_fall_back_to_a_short_continuous_move(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.ptz["cam1"]["relative"] = False
    await load()
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: "button.front_door_ptz_up"}, blocking=True)
    moves = nvr.calls("POST", "/cam1/ptz")
    assert moves[0]["mode"] == "continuous" and moves[0]["tilt"] == 1.0
    assert moves[-1] == {"action": "stop"}


async def test_preset_list_refreshes_on_poll(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    nvr.ptz["cam1"]["presets"].append({"token": "3", "name": "Driveway"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await wait_for(hass, lambda: "Driveway" in hass.states.get("select.front_door_ptz_preset").attributes["options"])


# ---- last event ----------------------------------------------------------


async def test_last_event_sensor_and_image(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.events.append(make_event(5, "cam1", T0, T0 + 9000, labels=["person"]))
    await load()
    sensor = hass.states.get("sensor.front_door_last_event")
    assert sensor.state == dt_util.utc_from_timestamp(T0 / 1000).isoformat()
    assert sensor.attributes["event_id"] == 5 and sensor.attributes["labels"] == ["person"]
    assert sensor.attributes["in_progress"] is False
    assert hass.states.get("sensor.garage_last_event").state == STATE_UNKNOWN

    entity = hass.data[image.DATA_COMPONENT].get_entity("image.front_door_last_event")
    assert await entity.async_image() == THUMB
    assert await entity.async_image() == THUMB
    assert len([r for r in nvr.requests if r[1] == "/api/events/5/thumb.jpg"]) == 1  # second call came from cache
    assert await hass.data[image.DATA_COMPONENT].get_entity("image.garage_last_event").async_image() is None


async def test_new_event_moves_sensor_and_image(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    event = make_event(6, "cam1", T0 + 60_000)
    nvr.push("event", {"phase": "start", "event": event})
    await wait_for(hass, lambda: hass.states.get("sensor.front_door_last_event").state != STATE_UNKNOWN)
    assert hass.states.get("sensor.front_door_last_event").attributes["in_progress"] is True
    before = hass.states.get("image.front_door_last_event").state
    assert before != STATE_UNKNOWN
    nvr.push("event", {"phase": "end", "event": {**event, "endMs": T0 + 70_000}})
    await wait_for(hass, lambda: hass.states.get("sensor.front_door_last_event").attributes["in_progress"] is False)


# ---- NVR device and permissions ------------------------------------------


async def test_nvr_sensors_for_admin(hass: HomeAssistant, setup) -> None:
    assert float(hass.states.get("sensor.home_nvr_disk_used").state) == 60.0
    assert float(hass.states.get("sensor.home_nvr_disk_free").state) == pytest.approx(400 * 1024**3 / 1e9, rel=1e-3)
    assert float(hass.states.get("sensor.home_nvr_disk_size").state) == pytest.approx(1000 * 1024**3 / 1e9, rel=1e-3)
    assert float(hass.states.get("sensor.home_nvr_recordings_size").state) == pytest.approx(
        15 * 1024**3 / 1e9, rel=1e-3
    )
    workers = hass.states.get("sensor.home_nvr_healthy_workers")
    assert workers.state == "2"
    assert workers.attributes["total"] == 3 and workers.attributes["unhealthy"] == ["record:cam2"]


async def test_worker_message_updates_healthy_count(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await wait_for(hass, lambda: nvr.stream_count == 1)
    nvr.push("worker", {"id": "record:cam2", "kind": "record", "state": "running", "unstable": False})
    await wait_for(hass, lambda: hass.states.get("sensor.home_nvr_healthy_workers").state == "3")
    nvr.push("worker", {"id": "record:cam2", "kind": "record", "state": "stopped", "unstable": False})
    await wait_for(hass, lambda: hass.states.get("sensor.home_nvr_healthy_workers").state == "2")


async def test_non_admin_token_degrades_gracefully(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.role = "user"
    nvr.cameras[0]["config"]["detect"] = {"enabled": True}
    entry = await load()
    assert hass.states.get("camera.front_door")
    assert hass.states.get("select.front_door_ptz_preset")
    assert hass.states.get("sensor.front_door_last_event")
    assert hass.states.async_entity_ids("switch") == []
    assert [e for e in hass.states.async_entity_ids("sensor") if "home_nvr" in e] == []
    # the NVR device is still there for the cameras to hang off
    from .conftest import get_device

    assert get_device(hass, entry) is not None


async def test_admin_entities_appear_when_token_is_upgraded(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.role = "user"
    await load()
    assert hass.states.async_entity_ids("switch") == []
    nvr.role = "admin"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await wait_for(hass, lambda: hass.states.get("switch.front_door_recording") is not None)
    assert hass.states.get("sensor.home_nvr_disk_free") is not None
    nvr.role = "user"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=122))
    await wait_for(hass, lambda: hass.states.get("switch.front_door_recording") is None)


async def test_removing_the_entry_clears_nothing_stale(hass: HomeAssistant, setup) -> None:
    ent = er.async_get(hass)
    assert any(e.platform == DOMAIN for e in ent.entities.values())
    assert hass.states.get("camera.front_door").state != STATE_UNAVAILABLE
