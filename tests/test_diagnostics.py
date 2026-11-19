"""Diagnostics never carry a secret."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant

from custom_components.cookienvr.diagnostics import async_get_config_entry_diagnostics

from .fake_nvr import TOKEN, FakeNVR


async def test_diagnostics_redact_secrets(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    result = await async_get_config_entry_diagnostics(hass, setup)
    text = json.dumps(result, default=str)
    for secret in (TOKEN, "cam-secret", "cam-user", "192.168.1.50", "alice", "nvr.local", f"127.0.0.1:{nvr.port}"):
        assert secret not in text
    assert result["entry"]["token"] == "**REDACTED**"
    assert result["state"]["cameras"]["cam1"]["config"]["name"] == "Front Door"
    assert result["last_update_success"] is True
