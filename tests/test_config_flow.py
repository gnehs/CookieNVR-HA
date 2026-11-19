"""Config flow: setup, errors, duplicates, reauth."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cookienvr.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN

from .fake_nvr import TOKEN, FakeNVR, free_port


async def _start(hass: HomeAssistant, url: str, token: str = TOKEN):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: url, CONF_TOKEN: token})


async def test_success_creates_entry(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _start(hass, nvr.url + "/")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home NVR"
    assert result["data"] == {CONF_URL: nvr.url, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == nvr.url
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_address_without_scheme_gets_http(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _start(hass, f"127.0.0.1:{nvr.port}")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == nvr.url
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start(hass, f"http://127.0.0.1:{free_port()}")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_url(hass: HomeAssistant) -> None:
    result = await _start(hass, "ftp://example.com")
    assert result["errors"] == {"base": "invalid_url"}


async def test_invalid_token(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _start(hass, nvr.url, "cnvr_wrong")
    assert result["errors"] == {"base": "invalid_auth"}


async def test_nvr_too_old(hass: HomeAssistant, nvr: FakeNVR) -> None:
    nvr.info["apiVersion"] = 0
    result = await _start(hass, nvr.url)
    assert result["errors"] == {"base": "nvr_outdated"}


async def test_integration_too_old(hass: HomeAssistant, nvr: FakeNVR) -> None:
    nvr.info["apiVersion"] = 99
    result = await _start(hass, nvr.url)
    assert result["errors"] == {"base": "integration_outdated"}


async def test_error_then_success_in_same_flow(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _start(hass, nvr.url, "cnvr_wrong")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: nvr.url, CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_same_nvr_cannot_be_added_twice(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    result = await _start(hass, nvr.url.upper().replace("HTTP", "http"))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    nvr.token = "cnvr_rotated"
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "cnvr_bad"})
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "cnvr_rotated"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "cnvr_rotated"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
