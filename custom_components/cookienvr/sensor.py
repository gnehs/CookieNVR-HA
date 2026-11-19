"""Last-event time per camera and NVR health sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import CookieNVRCameraEntity, CookieNVRNvrEntity, async_setup_dynamic, camera_keys
from .models import Json, NVRState

PARALLEL_UPDATES = 0


def _disk(state: NVRState) -> Json | None:
    return state.system.get("disk") if state.system else None


def _used_percent(state: NVRState) -> float | None:
    disk = _disk(state)
    if not disk or not disk.get("totalBytes"):
        return None
    return round((disk["totalBytes"] - disk["freeBytes"]) / disk["totalBytes"] * 100, 1)


def _healthy_workers(state: NVRState) -> int:
    return sum(1 for w in state.workers.values() if w.get("state") == "running" and not w.get("unstable"))


@dataclass(frozen=True, kw_only=True)
class NvrSensorDescription(SensorEntityDescription):
    value: Callable[[NVRState], Any]


_BYTES = {
    "device_class": SensorDeviceClass.DATA_SIZE,
    "native_unit_of_measurement": UnitOfInformation.BYTES,
    "suggested_unit_of_measurement": UnitOfInformation.GIGABYTES,
    "suggested_display_precision": 1,
    "state_class": SensorStateClass.MEASUREMENT,
}

NVR_SENSORS = (
    NvrSensorDescription(
        key="disk_total",
        translation_key="disk_total",
        value=lambda s: (_disk(s) or {}).get("totalBytes"),
        **_BYTES,
    ),
    NvrSensorDescription(
        key="disk_free",
        translation_key="disk_free",
        value=lambda s: (_disk(s) or {}).get("freeBytes"),
        **_BYTES,
    ),
    NvrSensorDescription(
        key="disk_used_percent",
        translation_key="disk_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value=_used_percent,
    ),
    NvrSensorDescription(
        key="recordings_size",
        translation_key="recordings_size",
        value=lambda s: sum(u.get("bytes", 0) for u in (s.system or {}).get("usage", [])) if s.system else None,
        **_BYTES,
    ),
    NvrSensorDescription(
        key="workers_healthy",
        translation_key="workers_healthy",
        state_class=SensorStateClass.MEASUREMENT,
        value=_healthy_workers,
    ),
)
SENSORS_BY_KEY = {d.key: d for d in NVR_SENSORS}


def _nvr_wanted(state: NVRState) -> set[str]:
    return set(SENSORS_BY_KEY) if state.admin and state.system is not None else set()


async def async_setup_entry(
    hass: HomeAssistant, entry: CookieNVRConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass,
        entry,
        async_add_entities,
        camera_keys,
        lambda coordinator, camera_id: LastEventSensor(coordinator, camera_id),
    )
    async_setup_dynamic(
        hass,
        entry,
        async_add_entities,
        _nvr_wanted,
        lambda coordinator, key: NvrSensor(coordinator, SENSORS_BY_KEY[key]),
    )


class LastEventSensor(CookieNVRCameraEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str) -> None:
        super().__init__(coordinator, camera_id, "last_event")

    @property
    def _event(self) -> Json | None:
        return self.coordinator.data.last_event.get(self.camera_id)

    @property
    def native_value(self) -> datetime | None:
        event = self._event
        return datetime.fromtimestamp(event["startMs"] / 1000, UTC) if event else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        event = self._event
        if event is None:
            return None
        return {
            "event_id": event["id"],
            "score": event.get("score"),
            "labels": list(event.get("labels") or []),
            "in_progress": event.get("endMs") is None,
        }


class NvrSensor(CookieNVRNvrEntity, SensorEntity):
    entity_description: NvrSensorDescription

    def __init__(self, coordinator: CookieNVRCoordinator, description: NvrSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "workers_healthy":
            return None
        workers = self.coordinator.data.workers
        return {
            "total": len(workers),
            "unhealthy": sorted(w for w, s in workers.items() if s.get("state") != "running"),
        }
