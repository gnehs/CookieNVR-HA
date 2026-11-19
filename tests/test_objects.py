"""Object classes: one binary sensor per ticked class, driven by event labels over SSE."""

from __future__ import annotations

import time

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.cookienvr.const import EVENT_COOKIENVR
from custom_components.cookienvr.labels import label_translation, readable_label

from .conftest import wait_for
from .fake_nvr import FakeNVR, make_event

T0 = (int(time.time()) - 3600) * 1000
PERSON = "binary_sensor.front_door_person"
CAR = "binary_sensor.front_door_car"


def _tick(nvr: FakeNVR, *labels: str) -> None:
    nvr.cameras[0]["config"]["detect"] = {"enabled": True, "labels": list(labels)}


def _state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return state.state if state else None


async def test_ticking_and_unticking_classes_adds_and_removes_sensors(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    _tick(nvr, "person", "car")
    await load()
    assert _state(hass, PERSON) == STATE_OFF
    assert hass.states.get(PERSON).attributes["device_class"] == "occupancy"
    assert _state(hass, "binary_sensor.garage_person") is None

    registry = er.async_get(hass)
    registry.async_update_entity(PERSON, new_entity_id="binary_sensor.porch_someone")
    await hass.async_block_till_done()

    _tick(nvr, "car", "dog")
    nvr.push("cameras", nvr.cameras)
    await wait_for(hass, lambda: _state(hass, "binary_sensor.front_door_dog") is not None)
    await wait_for(hass, lambda: _state(hass, "binary_sensor.porch_someone") is None)
    assert _state(hass, CAR) == STATE_OFF

    # ticking it again brings back the same entity, so automations that use it keep working
    _tick(nvr, "car", "dog", "person")
    nvr.push("cameras", nvr.cameras)
    await wait_for(hass, lambda: _state(hass, "binary_sensor.porch_someone") == STATE_OFF)

    _tick(nvr)
    nvr.push("cameras", nvr.cameras)
    await wait_for(hass, lambda: _state(hass, CAR) is None and _state(hass, "binary_sensor.front_door_dog") is None)


async def test_an_update_turns_the_class_on_at_once_and_the_end_turns_it_off(
    hass: HomeAssistant, nvr: FakeNVR, load
) -> None:
    _tick(nvr, "person", "car")
    await load()
    fired = async_capture_events(hass, EVENT_COOKIENVR)

    event = make_event(21, "cam1", T0, labels=[])
    nvr.push("event", {"phase": "start", "event": event})
    await wait_for(hass, lambda: _state(hass, "binary_sensor.front_door_motion") == STATE_ON)
    assert _state(hass, PERSON) == STATE_OFF

    nvr.push("event", {"phase": "update", "event": {**event, "labels": ["person"]}})
    await wait_for(hass, lambda: _state(hass, PERSON) == STATE_ON)
    assert _state(hass, CAR) == STATE_OFF
    attrs = hass.states.get(PERSON).attributes
    assert attrs["event_id"] == 21 and attrs["last_detected"] is not None

    # both classes in the same event, and an update that adds nothing new fires nothing
    nvr.push("event", {"phase": "update", "event": {**event, "labels": ["person", "car"]}})
    await wait_for(hass, lambda: _state(hass, CAR) == STATE_ON)
    nvr.push("event", {"phase": "update", "event": {**event, "labels": ["person", "car"], "score": 0.9}})

    nvr.push("event", {"phase": "end", "event": {**event, "labels": ["person", "car"], "endMs": T0 + 9000}})
    await wait_for(hass, lambda: _state(hass, PERSON) == STATE_OFF and _state(hass, CAR) == STATE_OFF)

    assert [(e.data["state"], e.data["labels"], e.data["new_labels"]) for e in fired] == [
        ("start", [], []),
        ("update", ["person"], ["person"]),
        ("update", ["person", "car"], ["car"]),
        ("end", ["person", "car"], []),
    ]


async def test_two_events_on_one_camera(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    _tick(nvr, "person", "car")
    await load()
    first = make_event(31, "cam1", T0, labels=["person"])
    second = make_event(32, "cam1", T0 + 1000, labels=["car"])
    nvr.push("event", {"phase": "start", "event": first})
    nvr.push("event", {"phase": "start", "event": second})
    await wait_for(hass, lambda: _state(hass, PERSON) == STATE_ON and _state(hass, CAR) == STATE_ON)
    nvr.push("event", {"phase": "end", "event": {**first, "endMs": T0 + 5000}})
    await wait_for(hass, lambda: _state(hass, PERSON) == STATE_OFF)
    assert _state(hass, CAR) == STATE_ON


async def test_reconnect_corrects_labels_in_both_directions(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    _tick(nvr, "person", "car")
    await load()
    fired = async_capture_events(hass, EVENT_COOKIENVR)
    ending = make_event(41, "cam1", T0, labels=["person"])
    growing = make_event(42, "cam1", T0 + 1000, labels=[])
    nvr.events += [ending, growing]
    nvr.push("event", {"phase": "start", "event": ending})
    nvr.push("event", {"phase": "start", "event": growing})
    await wait_for(hass, lambda: _state(hass, PERSON) == STATE_ON)

    # while HA is not listening, one event ends and the other gains a class
    nvr.events[0] = {**ending, "endMs": T0 + 60_000}
    nvr.events[1] = {**growing, "labels": ["car"]}
    nvr.drop_streams()
    await wait_for(hass, lambda: _state(hass, PERSON) == STATE_OFF and _state(hass, CAR) == STATE_ON)
    assert ("update", ["car"]) in [(e.data["state"], e.data["new_labels"]) for e in fired]


async def test_an_update_for_an_event_we_missed_counts_as_its_start(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    _tick(nvr, "person")
    await load()
    fired = async_capture_events(hass, EVENT_COOKIENVR)
    nvr.push("event", {"phase": "update", "event": make_event(51, "cam1", T0, labels=["person"])})
    await wait_for(hass, lambda: _state(hass, PERSON) == STATE_ON)
    assert [(e.data["state"], e.data["new_labels"]) for e in fired] == [("start", ["person"])]


async def test_custom_and_translated_class_names(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    _tick(nvr, "package", "garbage_bin")
    await load()
    assert hass.states.get("binary_sensor.front_door_package").attributes["friendly_name"] == "Front Door Package"
    assert (
        hass.states.get("binary_sensor.front_door_garbage_bin").attributes["friendly_name"] == "Front Door Garbage bin"
    )


def test_label_naming_lives_in_one_place() -> None:
    assert label_translation("person") == ("object_person", {})
    assert label_translation("garbage_bin") == ("object", {"label": "Garbage bin"})
    assert readable_label("hard-hat") == "Hard hat"


async def test_last_detected_comes_from_history_after_a_restart(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    _tick(nvr, "person")
    nvr.events.append(make_event(61, "cam1", T0, T0 + 9000, labels=["person"]))
    await load()
    attrs = hass.states.get(PERSON).attributes
    assert attrs["event_id"] == 61
    assert _state(hass, PERSON) == STATE_OFF
