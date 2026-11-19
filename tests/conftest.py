"""Shared fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cookienvr.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN

from .fake_nvr import TOKEN, FakeNVR


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let HA load custom_components/cookienvr."""


@pytest.fixture(autouse=True)
def allow_local_sockets(socket_enabled: None) -> None:
    """The fake NVR is a real server on 127.0.0.1."""


@pytest.fixture(autouse=True)
def fast_backoff():
    with (
        patch("custom_components.cookienvr.coordinator.SSE_BACKOFF_MIN", 0.05),
        patch("custom_components.cookienvr.coordinator.SSE_BACKOFF_MAX", 0.2),
    ):
        yield


@pytest.fixture
async def nvr() -> AsyncGenerator[FakeNVR]:
    server = FakeNVR()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def entry(nvr: FakeNVR) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home NVR",
        unique_id=nvr.url,
        data={CONF_URL: nvr.url, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )


@pytest.fixture
async def setup(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry):
    """Set the integration up and unload it afterwards so no task outlives the test."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def load(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry):
    """Like `setup`, but returns a callable so a test can shape the NVR before the entry loads."""

    async def _load() -> MockConfigEntry:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await wait_for(hass, lambda: nvr.stream_count == 1)
        return entry

    yield _load
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def wait_for(hass: HomeAssistant, condition: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)
            await hass.async_block_till_done()


def get_device(hass: HomeAssistant, entry: MockConfigEntry, camera_id: str | None = None):
    """The NVR device, or the device of one camera."""
    ident = entry.entry_id if camera_id is None else f"{entry.entry_id}:{camera_id}"
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, ident), entry.entry_id)
