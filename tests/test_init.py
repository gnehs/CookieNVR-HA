"""Setup, unload and the failure modes of loading an entry."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cookienvr.const import DOMAIN

from .conftest import get_device, wait_for
from .fake_nvr import FakeNVR


async def test_setup_creates_nvr_and_camera_devices(hass: HomeAssistant, setup: MockConfigEntry) -> None:
    nvr_device = get_device(hass, setup)
    assert nvr_device and nvr_device.name == "Home NVR" and nvr_device.sw_version == "1.2.3"
    front = get_device(hass, setup, "cam1")
    assert front and front.name == "Front Door" and front.via_device_id == nvr_device.id
    assert get_device(hass, setup, "cam2")


async def test_unload_closes_the_stream(hass: HomeAssistant, nvr: FakeNVR, setup: MockConfigEntry) -> None:
    await wait_for(hass, lambda: nvr.stream_count == 1)
    assert await hass.config_entries.async_unload(setup.entry_id)
    await wait_for(hass, lambda: nvr.stream_count == 0)
    assert setup.state is ConfigEntryState.NOT_LOADED


async def test_unreachable_nvr_retries_setup(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    await nvr.stop()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_token_starts_reauth(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    nvr.token = "cnvr_other"
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_old_nvr_raises_repair_issue(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    nvr.info["apiVersion"] = 0
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"nvr_outdated_{entry.entry_id}")


async def test_old_integration_raises_repair_issue_and_clears_it(
    hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry
) -> None:
    nvr.info["apiVersion"] = 5
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"integration_outdated_{entry.entry_id}")

    nvr.info["apiVersion"] = 1
    await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert registry.async_get_issue(DOMAIN, f"integration_outdated_{entry.entry_id}") is None
    await hass.config_entries.async_unload(entry.entry_id)
