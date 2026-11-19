"""Base entities and the add/remove bookkeeping shared by every platform."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator, camera_device_id
from .models import Json, NVRState, camera_model, camera_name


def nvr_device_info(coordinator: CookieNVRCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
        name=coordinator.info.get("siteName") or "CookieNVR",
        manufacturer="CookieNVR",
        model="NVR",
        sw_version=coordinator.info.get("version"),
        configuration_url=coordinator.client.base_url,
    )


class CookieNVRNvrEntity(CoordinatorEntity[CookieNVRCoordinator]):
    """An entity of the NVR device itself."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CookieNVRCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_nvr_{key}"
        self._attr_translation_key = key
        self._attr_device_info = nvr_device_info(coordinator)


class CookieNVRCameraEntity(CoordinatorEntity[CookieNVRCoordinator]):
    """An entity that belongs to one camera device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.camera_id = camera_id
        self._attr_unique_id = f"{entry.entry_id}_{camera_id}_{key}"
        if key:
            self._attr_translation_key = key
        camera = coordinator.data.cameras[camera_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, camera_device_id(entry.entry_id, camera_id))},
            name=camera_name(camera),
            manufacturer="CookieNVR",
            model=camera_model(camera),
            via_device_id=dr.async_get_device_id_by_identifier(
                coordinator.hass, (DOMAIN, entry.entry_id), config_entry_id=entry.entry_id
            ),
            configuration_url=coordinator.client.base_url,
        )

    @property
    def camera(self) -> Json | None:
        return self.coordinator.data.cameras.get(self.camera_id) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return super().available and self.camera is not None


@callback
def async_setup_dynamic[K: Hashable](
    hass: HomeAssistant,
    entry: CookieNVRConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    wanted: Callable[[NVRState], set[K]],
    make: Callable[[CookieNVRCoordinator, K], Entity],
) -> None:
    """Add an entity for every wanted key as it appears and remove it when it goes away."""
    coordinator = entry.runtime_data
    known: dict[K, Entity] = {}

    @callback
    def sync() -> None:
        keys = wanted(coordinator.data)
        fresh = [key for key in sorted(keys - known.keys(), key=repr)]
        if fresh:
            entities = [make(coordinator, key) for key in fresh]
            known.update(zip(fresh, entities, strict=True))
            async_add_entities(entities)
        for key in known.keys() - keys:
            entity = known.pop(key)
            if entity.entity_id:
                er.async_get(hass).async_remove(entity.entity_id)

    entry.async_on_unload(coordinator.async_add_listener(sync))
    sync()


def camera_keys(state: NVRState, predicate: Callable[[str, Json], bool] | None = None) -> set[Any]:
    return {cid for cid, cam in state.cameras.items() if predicate is None or predicate(cid, cam)}
