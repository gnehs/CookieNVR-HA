"""PTZ nudge buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import actions
from .const import PTZ_NUDGE_SECONDS, PTZ_NUDGE_SPEED
from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import CookieNVRCameraEntity, async_setup_dynamic
from .models import NVRState, ptz_supported

PARALLEL_UPDATES = 1

DIRECTIONS: dict[str, tuple[int, int]] = {
    "ptz_up": (0, 1),
    "ptz_down": (0, -1),
    "ptz_left": (-1, 0),
    "ptz_right": (1, 0),
}
RELATIVE_STEP = 0.1


def _wanted(state: NVRState) -> set[tuple[str, str]]:
    return {(cid, key) for cid in state.cameras if ptz_supported(state, cid) for key in DIRECTIONS}


async def async_setup_entry(
    hass: HomeAssistant, entry: CookieNVRConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_setup_dynamic(hass, entry, async_add_entities, _wanted, lambda coordinator, key: PtzButton(coordinator, *key))


class PtzButton(CookieNVRCameraEntity, ButtonEntity):
    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str, key: str) -> None:
        super().__init__(coordinator, camera_id, key)
        self._pan, self._tilt = DIRECTIONS[key]

    async def async_press(self) -> None:
        caps = self.coordinator.data.ptz.get(self.camera_id, {})
        if caps.get("relative"):
            await actions.ptz_move(
                self.coordinator,
                self.camera_id,
                pan=self._pan * RELATIVE_STEP,
                tilt=self._tilt * RELATIVE_STEP,
                mode="relative",
                speed=PTZ_NUDGE_SPEED,
            )
        else:
            await actions.ptz_move(
                self.coordinator,
                self.camera_id,
                pan=float(self._pan),
                tilt=float(self._tilt),
                mode="continuous",
                duration=PTZ_NUDGE_SECONDS,
                speed=PTZ_NUDGE_SPEED,
            )
