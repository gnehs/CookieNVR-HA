"""Recording and detection switches (admin tokens only)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CookieNVRApiError, CookieNVRConnectionError
from .const import DOMAIN
from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import CookieNVRCameraEntity, async_setup_dynamic
from .models import NVRState, detect_enabled, record_enabled

PARALLEL_UPDATES = 1

SECTIONS = {"recording": ("record", record_enabled), "detection": ("detect", detect_enabled)}


def _wanted(state: NVRState) -> set[tuple[str, str]]:
    if not state.admin:
        return set()
    return {
        (camera_id, key)
        for camera_id, camera in state.cameras.items()
        for key, (_, read) in SECTIONS.items()
        if read(camera) is not None
    }


async def async_setup_entry(
    hass: HomeAssistant, entry: CookieNVRConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass, entry, async_add_entities, _wanted, lambda coordinator, key: ConfigSwitch(coordinator, *key)
    )


class ConfigSwitch(CookieNVRCameraEntity, SwitchEntity):
    """Flips `config.<section>.enabled` through a full-config PUT."""

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str, key: str) -> None:
        super().__init__(coordinator, camera_id, key)
        self._section, self._read = SECTIONS[key]

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.admin and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        camera = self.camera
        return self._read(camera) if camera else None

    async def _set(self, value: bool) -> None:
        camera = self.camera
        if camera is None or not self.coordinator.data.admin:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="admin_required")
        config = deepcopy(camera["config"])
        config[self._section]["enabled"] = value
        try:
            updated = await self.coordinator.client.put_camera(self.camera_id, config)
        except (CookieNVRApiError, CookieNVRConnectionError) as err:
            message = err.message if isinstance(err, CookieNVRApiError) else str(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"error": message}
            ) from err
        self.coordinator.data.cameras[self.camera_id] = updated
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
