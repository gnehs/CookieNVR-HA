"""Diagnostics with every secret removed."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN, CONF_URL
from .coordinator import CookieNVRConfigEntry

TO_REDACT = {
    CONF_TOKEN,
    CONF_URL,
    "password",
    "username",
    "host",
    "mainUrl",
    "subUrl",
    "main",
    "sub",
    "token",
    "rtsp",
    "stderrTail",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: CookieNVRConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    state = asdict(coordinator.data)
    state["motion"] = sorted(state["motion"])
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "info": coordinator.info,
            "last_update_success": coordinator.last_update_success,
            "state": state,
        },
        TO_REDACT,
    )
