"""PTZ preset selector."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import actions
from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import CookieNVRCameraEntity, async_setup_dynamic, camera_keys
from .models import preset_options, ptz_supported

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: CookieNVRConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass,
        entry,
        async_add_entities,
        (
            lambda state: camera_keys(state, lambda cid, _: ptz_supported(state, cid)),
            lambda coordinator, camera_id: PresetSelect(coordinator, camera_id),
        ),
    )


class PresetSelect(CookieNVRCameraEntity, SelectEntity):
    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str) -> None:
        super().__init__(coordinator, camera_id, "ptz_preset")
        self._selected: str | None = None

    @property
    def options(self) -> list[str]:
        return list(preset_options(self.coordinator.data.ptz.get(self.camera_id, {})))

    @property
    def current_option(self) -> str | None:
        # the camera does not report where it points, so this is the last preset chosen here
        return self._selected if self._selected in self.options else None

    async def async_select_option(self, option: str) -> None:
        await actions.ptz_goto_preset(self.coordinator, self.camera_id, option)
        self._selected = option
        self.async_write_ha_state()
