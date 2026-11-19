"""Motion, online and per-object-class sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import CookieNVRCameraEntity, async_setup_dynamic
from .labels import label_translation
from .models import NVRState, event_labels, object_labels

PARALLEL_UPDATES = 0


def _wanted(state: NVRState) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for camera_id, camera in state.cameras.items():
        keys.add((camera_id, "motion", ""))
        keys.add((camera_id, "online", ""))
        keys.update((camera_id, "label", label) for label in object_labels(camera))
    return keys


def _make(coordinator: CookieNVRCoordinator, key: tuple[str, str, str]) -> BinarySensorEntity:
    camera_id, kind, label = key
    if kind == "motion":
        return MotionSensor(coordinator, camera_id)
    if kind == "online":
        return OnlineSensor(coordinator, camera_id)
    return ObjectSensor(coordinator, camera_id, label)


async def async_setup_entry(
    hass: HomeAssistant, entry: CookieNVRConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, async_add_entities, (_wanted, _make))


class MotionSensor(CookieNVRCameraEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str) -> None:
        super().__init__(coordinator, camera_id, "motion")

    @property
    def is_on(self) -> bool:
        state = self.coordinator.data
        return self.camera_id in state.motion or bool(state.active.get(self.camera_id))


class OnlineSensor(CookieNVRCameraEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str) -> None:
        super().__init__(coordinator, camera_id, "online")

    @property
    def is_on(self) -> bool | None:
        camera = self.camera
        return bool(camera["online"]) if camera else None


class ObjectSensor(CookieNVRCameraEntity, BinarySensorEntity):
    """On while an event in progress carries this object class."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str, label: str) -> None:
        # unique_id is camera id + label, so renaming the camera or unticking and re-ticking the class keeps it
        super().__init__(coordinator, camera_id, f"label_{label}")
        self._label = label
        self._attr_translation_key, self._attr_translation_placeholders = label_translation(label)

    @property
    def is_on(self) -> bool:
        events = self.coordinator.data.active.get(self.camera_id, {})
        return any(self._label in event_labels(event) for event in events.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        seen = self.coordinator.data.label_seen.get(self.camera_id, {}).get(self._label)
        return {"last_detected": seen["at"] if seen else None, "event_id": seen["event_id"] if seen else None}
