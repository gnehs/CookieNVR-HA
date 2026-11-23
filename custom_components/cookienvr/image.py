"""Thumbnail of the most recent event."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import CookieNVRApiError, CookieNVRConnectionError
from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import CookieNVRCameraEntity, async_setup_dynamic, camera_keys

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: CookieNVRConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_setup_dynamic(
        hass,
        entry,
        async_add_entities,
        (camera_keys, lambda coordinator, camera_id: LastEventImage(coordinator, camera_id)),
    )


class LastEventImage(CookieNVRCameraEntity, ImageEntity):
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: CookieNVRCoordinator, camera_id: str) -> None:
        CookieNVRCameraEntity.__init__(self, coordinator, camera_id, "last_event")
        ImageEntity.__init__(self, coordinator.hass)
        self._cache: tuple[tuple[int, datetime], bytes] | None = None

    @property
    def _event(self) -> dict | None:
        return self.coordinator.data.last_event.get(self.camera_id)

    @property
    def image_last_updated(self) -> datetime | None:
        event = self._event
        if event is None:
            return None
        return self.coordinator.image_updated.get(self.camera_id) or datetime.fromtimestamp(
            event["startMs"] / 1000, UTC
        )

    async def async_image(self) -> bytes | None:
        event = self._event
        stamp = self.image_last_updated
        if event is None or stamp is None or not event.get("hasThumb"):
            return None
        key = (event["id"], stamp)
        if self._cache and self._cache[0] == key:
            return self._cache[1]
        try:
            data = await self.coordinator.client.event_thumb(event["id"], boxes=True)
        except (CookieNVRApiError, CookieNVRConnectionError):
            return None
        if data is not None:
            self._cache = (key, data)
        return data
