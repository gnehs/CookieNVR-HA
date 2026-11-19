"""Config flow: NVR address and API token, with reauth."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from yarl import URL

from .api import (
    CookieNVRApiError,
    CookieNVRAuthError,
    CookieNVRClient,
    CookieNVRConnectionError,
    normalize_url,
)
from .const import API_VERSION_MAX, API_VERSION_MIN, CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class CookieNVRConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, url: str, token: str, verify_ssl: bool) -> tuple[str | None, dict[str, Any]]:
        """Return `(error_key, info)`; the key is None when the NVR accepted the token."""
        try:
            parsed = URL(url)
            if parsed.scheme not in ("http", "https") or not parsed.host:
                return "invalid_url", {}
        except ValueError:
            return "invalid_url", {}
        client = CookieNVRClient(async_get_clientsession(self.hass, verify_ssl=verify_ssl), url, token)
        try:
            info = await client.info()
            version = info.get("apiVersion")
            if not isinstance(version, int) or version < API_VERSION_MIN:
                return "nvr_outdated", info
            if version > API_VERSION_MAX:
                return "integration_outdated", info
            await client.me()
        except CookieNVRAuthError:
            return "invalid_auth", {}
        except (CookieNVRConnectionError, CookieNVRApiError):
            return "cannot_connect", {}
        except (ValueError, AttributeError):
            return "cannot_connect", {}
        return None, info

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            verify = user_input.get(CONF_VERIFY_SSL, True)
            await self.async_set_unique_id(url.lower())
            self._abort_if_unique_id_configured()
            error, info = await self._validate(url, user_input[CONF_TOKEN], verify)
            if error is None:
                return self.async_create_entry(
                    title=info.get("siteName") or "CookieNVR",
                    data={CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN], CONF_VERIFY_SSL: verify},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://")): str,
                    vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
                    vol.Optional(CONF_VERIFY_SSL, default=(user_input or {}).get(CONF_VERIFY_SSL, True)): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error, _ = await self._validate(
                entry.data[CONF_URL], user_input[CONF_TOKEN], entry.data.get(CONF_VERIFY_SSL, True)
            )
            if error is None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )
