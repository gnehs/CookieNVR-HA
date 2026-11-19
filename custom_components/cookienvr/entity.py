"""Base entities and the add/remove bookkeeping shared by every platform."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
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


type Group = tuple[Callable[[NVRState], set[Any]], Callable[[CookieNVRCoordinator, Any], Entity]]


@callback
def async_setup_dynamic(
    hass: HomeAssistant,
    entry: CookieNVRConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    *groups: Group,
) -> None:
    """Add an entity for every wanted key as it appears and remove it when it goes away.

    Each group is a `(wanted, make)` pair. On the first pass, registry entries of this platform that no
    group wants any more (a token that lost admin rights, a label that was dropped) are removed too.
    """
    coordinator = entry.runtime_data
    known: list[dict[Hashable, Entity]] = [{} for _ in groups]
    registry = er.async_get(hass)
    platform = entity_platform.async_get_current_platform()
    first = True

    @callback
    def sync() -> None:
        nonlocal first
        for (wanted, make), group_known in zip(groups, known, strict=True):
            keys = wanted(coordinator.data)
            fresh = sorted(keys - group_known.keys(), key=repr)
            if fresh:
                entities = [make(coordinator, key) for key in fresh]
                group_known.update(zip(fresh, entities, strict=True))
                async_add_entities(entities)
            for key in group_known.keys() - keys:
                entity = group_known.pop(key)
                if entity.entity_id:
                    registry.async_remove(entity.entity_id)
        if first:
            first = False
            live = {e.unique_id for group_known in known for e in group_known.values()}
            for stale in er.async_entries_for_config_entry(registry, entry.entry_id):
                if stale.domain == platform.domain and stale.unique_id not in live:
                    registry.async_remove(stale.entity_id)

    entry.async_on_unload(coordinator.async_add_listener(sync))
    sync()


def camera_keys(state: NVRState, predicate: Callable[[str, Json], bool] | None = None) -> set[Any]:
    return {cid for cid, cam in state.cameras.items() if predicate is None or predicate(cid, cam)}
