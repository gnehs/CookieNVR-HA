"""Camera platform: NVR snapshot and RTSP restream."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from yarl import URL

from . import actions
from .api import CookieNVRApiError, CookieNVRConnectionError
from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import CookieNVRCameraEntity, async_setup_dynamic, camera_keys

PARALLEL_UPDATES = 0

PTZ_MOVE_SCHEMA = {
    vol.Optional("pan", default=0): vol.All(vol.Coerce(float), vol.Range(min=-1, max=1)),
    vol.Optional("tilt", default=0): vol.All(vol.Coerce(float), vol.Range(min=-1, max=1)),
    vol.Optional("zoom", default=0): vol.All(vol.Coerce(float), vol.Range(min=-1, max=1)),
    vol.Optional("mode"): vol.In(["continuous", "relative"]),
    vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
    vol.Optional("speed"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1)),
}
PTZ_PRESET_SCHEMA = {
    vol.Required("action"): vol.In(["goto", "save", "delete"]),
    vol.Optional("preset"): cv.string,
    vol.Optional("name"): cv.string,
}
EXPORT_SCHEMA = {
    vol.Required("start"): cv.datetime,
    vol.Optional("end"): cv.datetime,
    vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=1)),
    vol.Optional("filename"): cv.string,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: CookieNVRConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass,
        entry,
        async_add_entities,
        (camera_keys, lambda coordinator, camera_id: CookieNVRCamera(coordinator, camera_id)),
    )
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("ptz_move", PTZ_MOVE_SCHEMA, "async_ptz_move")
    platform.async_register_entity_service("ptz_preset", PTZ_PRESET_SCHEMA, "async_ptz_preset")
    platform.async_register_entity_service(
        "export_clip", EXPORT_SCHEMA, "async_export_clip", supports_response=SupportsResponse.OPTIONAL
    )


class CookieNVRCamera(CookieNVRCameraEntity, Camera):
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_name = None

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str) -> None:
        CookieNVRCameraEntity.__init__(self, coordinator, camera_id, "camera")
        Camera.__init__(self)
        self._attr_translation_key = None

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        try:
            return await self.coordinator.client.snapshot(self.camera_id)
        except (CookieNVRApiError, CookieNVRConnectionError):
            return None

    async def stream_source(self) -> str | None:
        camera = self.camera
        rtsp = (camera or {}).get("rtsp") or {}
        main = rtsp.get("main")
        if not main:
            return None
        # RTSP user is the token owner, the password field carries the token
        return str(URL(main).with_user(self.coordinator.data.username).with_password(self.coordinator.client.token))

    async def async_ptz_move(
        self,
        pan: float = 0,
        tilt: float = 0,
        zoom: float = 0,
        mode: str | None = None,
        duration: float | None = None,
        speed: float | None = None,
    ) -> None:
        await actions.ptz_move(
            self.coordinator, self.camera_id, pan=pan, tilt=tilt, zoom=zoom, mode=mode, duration=duration, speed=speed
        )

    async def async_ptz_preset(self, action: str, preset: str | None = None, name: str | None = None) -> None:
        if action == "goto":
            if not preset:
                raise actions._invalid("preset_required")
            await actions.ptz_goto_preset(self.coordinator, self.camera_id, preset)
        else:
            await actions.ptz_manage_preset(self.coordinator, self.camera_id, action, preset, name)

    async def async_export_clip(
        self,
        start: datetime,
        end: datetime | None = None,
        duration: float | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        if (end is None) == (duration is None):
            raise actions._invalid("export_end_or_duration")
        start = dt_util.as_utc(start)
        end_utc = dt_util.as_utc(end) if end is not None else start + timedelta(seconds=duration or 0)
        return await actions.export_clip(self.hass, self.coordinator, self.camera_id, start, end_utc, filename)
