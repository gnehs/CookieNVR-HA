"""Config flow: discovery, manual address, approval in CookieNVR, errors, duplicates, reauth."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cookienvr.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL, DOMAIN

from .conftest import wait_for
from .fake_nvr import INSTANCE_ID, TOKEN, FakeNVR, free_port


def _discovery(port: int, instance_id: str = INSTANCE_ID, host: str = "127.0.0.1") -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        port=port,
        hostname="cookienvr-6f1c2a9e.local.",
        type="_cookienvr._tcp.local.",
        name="Home NVR (6f1c)._cookienvr._tcp.local.",
        properties={"id": instance_id, "version": "1.2.3", "api": "1"},
    )


async def _user(hass: HomeAssistant, url: str, **extra: Any):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: url, **extra})


async def _finish_link(hass: HomeAssistant, nvr: FakeNVR, result, decision: str):
    """The person opens the approval page and decides; then the flow moves on by itself."""
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "wait_for_approval"
    code = result["description_placeholders"]["code"]
    assert result["description_placeholders"]["url"] == f"{nvr.url}/#/link/{code}"
    if decision != "wait":
        nvr.decide_link(decision, code)
    flow_id = result["flow_id"]
    await wait_for(hass, lambda: hass.config_entries.flow.async_get(flow_id)["step_id"] != "link", timeout=10)
    return await hass.config_entries.flow.async_configure(flow_id)


async def _unload(hass: HomeAssistant, result) -> None:
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_discovery_to_entry_without_typing_anything(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery(nvr.port)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {"name": "Home NVR", "url": nvr.url}
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows[0]["context"]["title_placeholders"] == {"name": "Home NVR"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _finish_link(hass, nvr, result, "approved")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home NVR"
    assert result["data"] == {CONF_URL: nvr.url, CONF_TOKEN: "cnvr_linkedtoken", CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == INSTANCE_ID
    assert nvr.calls("POST", "/api/link") == [{"app": "Home Assistant"}]
    await _unload(hass, result)


async def test_manual_address_then_approval(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _user(hass, f"127.0.0.1:{nvr.port}/")
    result = await _finish_link(hass, nvr, result, "approved")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == nvr.url
    assert result["data"][CONF_TOKEN] == "cnvr_linkedtoken"
    assert result["result"].unique_id == INSTANCE_ID
    await _unload(hass, result)


async def test_denied_shows_why_and_can_retry(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _finish_link(hass, nvr, await _user(hass, nvr.url), "denied")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link_retry"
    assert result["errors"] == {"base": "link_denied"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert len(nvr.links) == 2
    result = await _finish_link(hass, nvr, result, "approved")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _unload(hass, result)


async def test_expired_code(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _finish_link(hass, nvr, await _user(hass, nvr.url), "expired")
    assert result["errors"] == {"base": "link_expired"}


async def test_code_running_out_while_waiting(hass: HomeAssistant, nvr: FakeNVR) -> None:
    nvr.link_ttl_ms = 300
    result = await _finish_link(hass, nvr, await _user(hass, nvr.url), "wait")
    assert result["errors"] == {"base": "link_expired"}


async def test_forgotten_request_reads_as_expired(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _user(hass, nvr.url)
    nvr.links.clear()
    result = await _finish_link(hass, nvr, result, "wait")
    assert result["errors"] == {"base": "link_expired"}


async def test_token_taken_by_someone_else(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _finish_link(hass, nvr, await _user(hass, nvr.url), "claimed")
    assert result["errors"] == {"base": "link_claimed"}


async def test_nvr_goes_away_while_waiting(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _user(hass, nvr.url)
    await nvr.stop()
    result = await _finish_link(hass, nvr, result, "wait")
    assert result["errors"] == {"base": "cannot_connect"}


async def test_too_many_link_requests(hass: HomeAssistant, nvr: FakeNVR) -> None:
    nvr.link_create_status = 429
    result = await _user(hass, nvr.url)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "link_rate_limited"


async def test_pasted_token_still_works(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _user(hass, nvr.url, **{CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: nvr.url, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert nvr.links == {}
    await _unload(hass, result)


async def test_invalid_pasted_token(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await _user(hass, nvr.url, **{CONF_TOKEN: "cnvr_wrong"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant) -> None:
    result = await _user(hass, f"http://127.0.0.1:{free_port()}")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_invalid_url(hass: HomeAssistant) -> None:
    result = await _user(hass, "ftp://example.com")
    assert result["errors"] == {"base": "invalid_url"}


async def test_nvr_too_old(hass: HomeAssistant, nvr: FakeNVR) -> None:
    nvr.info["apiVersion"] = 0
    result = await _user(hass, nvr.url)
    assert result["errors"] == {"base": "nvr_outdated"}


async def test_integration_too_old(hass: HomeAssistant, nvr: FakeNVR) -> None:
    nvr.info["apiVersion"] = 99
    result = await _user(hass, nvr.url)
    assert result["errors"] == {"base": "integration_outdated"}


async def test_nvr_without_instance_id_is_keyed_by_url(hass: HomeAssistant, nvr: FakeNVR) -> None:
    del nvr.info["instanceId"]
    result = await _user(hass, nvr.url, **{CONF_TOKEN: TOKEN})
    assert result["result"].unique_id == nvr.url
    await _unload(hass, result)


async def test_same_nvr_cannot_be_added_twice(hass: HomeAssistant, nvr: FakeNVR) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=INSTANCE_ID, data={CONF_URL: nvr.url, CONF_TOKEN: TOKEN}).add_to_hass(hass)
    result = await _user(hass, f"127.0.0.1:{nvr.port}/")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _rediscover(hass: HomeAssistant, entry: MockConfigEntry, port: int):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery(port)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    await hass.async_block_till_done()


async def test_rediscovery_at_a_new_address_updates_the_entry(hass: HomeAssistant, nvr: FakeNVR) -> None:
    old = f"http://127.0.0.1:{free_port()}"  # the NVR no longer answers there
    entry = MockConfigEntry(domain=DOMAIN, unique_id=INSTANCE_ID, data={CONF_URL: old, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    await _rediscover(hass, entry, nvr.port)
    assert entry.data[CONF_URL] == nvr.url
    await hass.config_entries.async_unload(entry.entry_id)


async def test_an_impostor_announcing_our_id_gets_no_token(hass: HomeAssistant, nvr: FakeNVR) -> None:
    """Anyone can copy the public instance id; only the NVR that issued the token can answer the proof."""
    impostor = FakeNVR()
    impostor.token = "cnvr_something_else"
    await impostor.start()
    try:
        entry = MockConfigEntry(
            domain=DOMAIN, unique_id=INSTANCE_ID, data={CONF_URL: f"http://127.0.0.1:{free_port()}", CONF_TOKEN: TOKEN}
        )
        entry.add_to_hass(hass)
        await _rediscover(hass, entry, impostor.port)
        assert entry.data[CONF_URL] != impostor.url
    finally:
        await impostor.stop()


async def test_announcement_ignored_while_the_old_address_still_answers(hass: HomeAssistant, nvr: FakeNVR) -> None:
    other = FakeNVR()
    await other.start()
    try:
        entry = MockConfigEntry(domain=DOMAIN, unique_id=INSTANCE_ID, data={CONF_URL: nvr.url, CONF_TOKEN: TOKEN})
        entry.add_to_hass(hass)
        await _rediscover(hass, entry, other.port)
        assert entry.data[CONF_URL] == nvr.url
        assert other.calls("POST", "/api/link/proof") == []
    finally:
        await other.stop()


async def test_discovery_of_something_else_is_ignored(hass: HomeAssistant, nvr: FakeNVR) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery(nvr.port, instance_id="other")
    )
    assert result["reason"] == "not_cookienvr"
    info = _discovery(nvr.port)
    info.properties.pop("id")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    assert result["reason"] == "not_cookienvr"


async def test_discovered_nvr_unreachable(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery(free_port())
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_setup_adopts_the_instance_id_for_old_entries(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    assert setup.unique_id == INSTANCE_ID


async def test_reauth_through_approval(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, unique_id=INSTANCE_ID)
    nvr.token = "cnvr_revoked"
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"name": "Home NVR", "url": nvr.url}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _finish_link(hass, nvr, result, "approved")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "cnvr_linkedtoken"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reauth_with_a_pasted_token(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    nvr.token = "cnvr_rotated"
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "cnvr_bad"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "cnvr_rotated"})
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "cnvr_rotated"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reauth_refuses_a_different_nvr(hass: HomeAssistant, nvr: FakeNVR, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, unique_id="some-other-nvr")
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
