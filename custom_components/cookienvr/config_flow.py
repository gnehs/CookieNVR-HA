"""Config flow: discovery or address, then approval in CookieNVR (no token to copy), with reauth.

Linking follows the shape of OAuth 2.0 device authorization (RFC 8628): we ask the NVR for a link
request, show the user its approval link and short code, and poll with a secret only we hold until
someone signed in to CookieNVR approves it. Pasting an API token stays available as a fallback.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from ipaddress import IPv6Address
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from yarl import URL

from .api import (
    CookieNVRApiError,
    CookieNVRAuthError,
    CookieNVRClient,
    CookieNVRConnectionError,
    normalize_url,
)
from .const import API_VERSION_MAX, API_VERSION_MIN, CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN, LOGGER

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
LINK_APP = "Home Assistant"
# Consecutive failed polls before we tell the user the NVR went away.
LINK_MAX_POLL_FAILURES = 5

type LinkOutcome = tuple[str, dict[str, Any]]


class CookieNVRConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._url = ""
        self._verify_ssl = True
        self._info: dict[str, Any] = {}
        self._link: dict[str, Any] | None = None
        self._link_task: asyncio.Task[LinkOutcome] | None = None

    def _client(self, url: str, token: str = "", verify_ssl: bool | None = None) -> CookieNVRClient:
        verify = self._verify_ssl if verify_ssl is None else verify_ssl
        return CookieNVRClient(async_get_clientsession(self.hass, verify_ssl=verify), url, token)

    async def _fetch_info(self, url: str, verify_ssl: bool) -> tuple[str | None, dict[str, Any]]:
        """Return `(error_key, info)` for an NVR address; the key is None when it is usable."""
        try:
            parsed = URL(url)
            if parsed.scheme not in ("http", "https") or not parsed.host:
                return "invalid_url", {}
        except ValueError:
            return "invalid_url", {}
        try:
            info = await self._client(url, verify_ssl=verify_ssl).info()
        except (CookieNVRConnectionError, CookieNVRApiError, CookieNVRAuthError, ValueError, AttributeError):
            return "cannot_connect", {}
        if not isinstance(info, dict):
            return "cannot_connect", {}
        version = info.get("apiVersion")
        if not isinstance(version, int) or version < API_VERSION_MIN:
            return "nvr_outdated", info
        if version > API_VERSION_MAX:
            return "integration_outdated", info
        return None, info

    async def _check_token(self, token: str) -> str | None:
        try:
            await self._client(self._url, token).me()
        except CookieNVRAuthError:
            return "invalid_auth"
        except (CookieNVRConnectionError, CookieNVRApiError):
            return "cannot_connect"
        return None

    async def _claim_unique_id(self, info: dict[str, Any], url: str) -> None:
        """The NVR's instance id identifies it across address changes; older NVRs fall back to the URL."""
        instance_id = info.get("instanceId")
        await self.async_set_unique_id(instance_id if isinstance(instance_id, str) and instance_id else url.lower())
        self._abort_if_unique_id_configured(updates={CONF_URL: url})

    def _finish(self, token: str) -> ConfigFlowResult:
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates={CONF_TOKEN: token})
        return self.async_create_entry(
            title=self._info.get("siteName") or "CookieNVR",
            data={CONF_URL: self._url, CONF_TOKEN: token, CONF_VERIFY_SSL: self._verify_ssl},
        )

    # ---- manual address -------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            verify = user_input.get(CONF_VERIFY_SSL, True)
            error, info = await self._fetch_info(url, verify)
            if error is None:
                await self._claim_unique_id(info, url)
                self._url, self._verify_ssl, self._info = url, verify, info
                token = (user_input.get(CONF_TOKEN) or "").strip()
                if not token:
                    return await self.async_step_link()
                error = await self._check_token(token)
                if error is None:
                    return self._finish(token)
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://")): str,
                    vol.Optional(CONF_VERIFY_SSL, default=(user_input or {}).get(CONF_VERIFY_SSL, True)): bool,
                    vol.Optional(CONF_TOKEN): TOKEN_SELECTOR,
                }
            ),
            errors=errors,
        )

    # ---- discovery --------------------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        instance_id = discovery_info.properties.get("id")
        if not instance_id or not discovery_info.port:
            return self.async_abort(reason="not_cookienvr")
        host = discovery_info.host
        if isinstance(discovery_info.ip_address, IPv6Address):
            host = f"[{host}]"
        url = f"http://{host}:{discovery_info.port}"

        await self.async_set_unique_id(instance_id)
        self._abort_if_unique_id_configured(updates={CONF_URL: url})

        error, info = await self._fetch_info(url, True)
        if error is not None:
            return self.async_abort(reason=error)
        if info.get("instanceId") != instance_id:
            return self.async_abort(reason="not_cookienvr")
        self._url, self._info = url, info
        self.context["title_placeholders"] = {"name": info.get("siteName") or "CookieNVR"}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_link()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._info.get("siteName") or "CookieNVR", "url": self._url},
        )

    # ---- approval in CookieNVR -------------------------------------------

    async def _wait_for_approval(self) -> LinkOutcome:
        assert self._link is not None
        client = self._client(self._url)
        secret = self._link["pollSecret"]
        interval = max(0.1, float(self._link.get("interval", 2)))
        deadline = time.monotonic() + max(0.0, (self._link["expiresAt"] - time.time() * 1000) / 1000)
        failures = 0
        while time.monotonic() < deadline + interval:
            await asyncio.sleep(interval)
            try:
                result = await client.poll_link(secret)
            except CookieNVRApiError as err:
                if err.status == 404:
                    return "link_expired", {}
                if err.status == 429:
                    await asyncio.sleep(interval * 4)
                    continue
                failures += 1
            except CookieNVRConnectionError:
                failures += 1
            else:
                failures = 0
                status = result.get("status")
                if status == "approved" and isinstance(result.get("token"), str):
                    return "approved", result
                if status in ("denied", "expired", "claimed"):
                    return f"link_{status}", {}
            if failures >= LINK_MAX_POLL_FAILURES:
                return "cannot_connect", {}
        return "link_expired", {}

    async def async_step_link(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._link is None:
            try:
                self._link = await self._client(self._url).create_link(LINK_APP)
            except CookieNVRApiError as err:
                LOGGER.debug("CookieNVR refused a link request: %s", err)
                return self.async_abort(reason="link_rate_limited" if err.status == 429 else "cannot_connect")
            except CookieNVRConnectionError:
                return self.async_abort(reason="cannot_connect")
        if self._link_task is None:
            self._link_task = self.hass.async_create_task(self._wait_for_approval())
        if self._link_task.done():
            return self.async_show_progress_done(next_step_id="link_done")
        return self.async_show_progress(
            step_id="link",
            progress_action="wait_for_approval",
            description_placeholders={"url": self._link["approveUrl"], "code": self._link["code"]},
            progress_task=self._link_task,
        )

    async def async_step_link_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._link_task is not None
        outcome, result = self._link_task.result()
        self._link, self._link_task = None, None
        if outcome == "approved":
            error = await self._check_token(result["token"])
            if error is None:
                return self._finish(result["token"])
            outcome = error
        return self.async_show_form(
            step_id="link_retry",
            errors={"base": outcome},
            description_placeholders={"name": self._info.get("siteName") or "CookieNVR"},
        )

    async def async_step_link_retry(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_link()

    # ---- reauth -----------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._url = entry_data[CONF_URL]
        self._verify_ssl = entry_data.get(CONF_VERIFY_SSL, True)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error, info = await self._fetch_info(self._url, self._verify_ssl)
            if error is None:
                self._info = info
                instance_id = info.get("instanceId")
                # Entries made before instance ids existed are keyed by URL; setup migrates those.
                if isinstance(instance_id, str) and instance_id and "://" not in (entry.unique_id or ""):
                    await self.async_set_unique_id(instance_id)
                    self._abort_if_unique_id_mismatch()
                token = (user_input.get(CONF_TOKEN) or "").strip()
                if not token:
                    return await self.async_step_link()
                error = await self._check_token(token)
                if error is None:
                    return self._finish(token)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"name": entry.title, "url": self._url},
            errors=errors,
        )
