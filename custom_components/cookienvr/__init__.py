"""The CookieNVR integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CookieNVRApiError, CookieNVRAuthError, CookieNVRClient, CookieNVRConnectionError
from .const import (
    API_VERSION_MAX,
    API_VERSION_MIN,
    CONF_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    ISSUE_INTEGRATION_OUTDATED,
    ISSUE_NVR_OUTDATED,
)
from .coordinator import CookieNVRConfigEntry, CookieNVRCoordinator
from .entity import nvr_device_info

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.IMAGE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def _check_api_version(hass: HomeAssistant, entry: ConfigEntry, info: dict) -> str | None:
    """Raise or clear the version repair issues; returns the issue id when incompatible."""
    version = info.get("apiVersion")
    problem: str | None = None
    if not isinstance(version, int) or version < API_VERSION_MIN:
        problem = ISSUE_NVR_OUTDATED
    elif version > API_VERSION_MAX:
        problem = ISSUE_INTEGRATION_OUTDATED
    for issue in (ISSUE_NVR_OUTDATED, ISSUE_INTEGRATION_OUTDATED):
        issue_id = f"{issue}_{entry.entry_id}"
        if issue == problem:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=issue,
                translation_placeholders={
                    "nvr_version": str(info.get("version", "?")),
                    "api_version": str(version),
                    "min": str(API_VERSION_MIN),
                    "max": str(API_VERSION_MAX),
                },
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    return problem


async def async_setup_entry(hass: HomeAssistant, entry: CookieNVRConfigEntry) -> bool:
    client = CookieNVRClient(
        async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
        entry.data[CONF_URL],
        entry.data[CONF_TOKEN],
    )
    try:
        info = await client.info()
        await client.me()
    except CookieNVRAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (CookieNVRConnectionError, CookieNVRApiError) as err:
        raise ConfigEntryNotReady(f"Cannot reach CookieNVR: {err}") from err

    if _check_api_version(hass, entry, info):
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="incompatible_version")

    coordinator = CookieNVRCoordinator(hass, entry, client, info)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # Camera devices point at the NVR device, and the NVR may have no entities of its own (non-admin token)
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **nvr_device_info(coordinator))
    entry.async_on_unload(coordinator.async_add_listener(coordinator.sync_devices))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start_stream()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CookieNVRConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CookieNVRConfigEntry) -> None:
    for issue in (ISSUE_NVR_OUTDATED, ISSUE_INTEGRATION_OUTDATED):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")
