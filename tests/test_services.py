"""ptz_move, ptz_preset and export_clip."""

from __future__ import annotations

from pathlib import Path

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.cookienvr.const import DOMAIN

from .fake_nvr import MP4, FakeNVR

FRONT = {"entity_id": "camera.front_door"}


async def _call(hass: HomeAssistant, service: str, data: dict, **kwargs):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, **kwargs)


async def test_ptz_move_defaults_to_relative(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await _call(hass, "ptz_move", {**FRONT, "pan": 0.5, "speed": 0.8})
    assert nvr.calls("POST", "/cam1/ptz") == [
        {"action": "move", "pan": 0.5, "tilt": 0.0, "zoom": 0.0, "mode": "relative", "speed": 0.8}
    ]


async def test_ptz_move_continuous_resends_then_stops(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await _call(hass, "ptz_move", {**FRONT, "tilt": -1, "mode": "continuous", "duration": 0.7})
    posts = nvr.calls("POST", "/cam1/ptz")
    assert len([p for p in posts if p["action"] == "move"]) >= 2  # the NVR stops after 1.5 s of silence
    assert posts[-1] == {"action": "stop"}


async def test_ptz_move_on_a_camera_without_ptz_is_a_clear_error(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    with pytest.raises(ServiceValidationError, match="Garage has no PTZ control"):
        await _call(hass, "ptz_move", {"entity_id": "camera.garage", "pan": 1})
    assert nvr.calls("POST", "/cam2/ptz") == []


async def test_ptz_move_validation(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    with pytest.raises(ServiceValidationError, match="at least one"):
        await _call(hass, "ptz_move", FRONT)
    with pytest.raises(vol.Invalid):
        await _call(hass, "ptz_move", {**FRONT, "pan": 4})
    nvr.ptz["cam1"]["zoom"] = False
    await hass.config_entries.async_reload(setup.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError, match="cannot zoom"):
        await _call(hass, "ptz_move", {**FRONT, "zoom": 1})
    with pytest.raises(vol.Invalid):
        await _call(hass, "ptz_move", {**FRONT, "pan": 1, "mode": "absolute"})


async def test_ptz_move_unsupported_mode(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.ptz["cam1"]["continuous"] = False
    await load()
    with pytest.raises(ServiceValidationError, match="does not support continuous"):
        await _call(hass, "ptz_move", {**FRONT, "pan": 1, "mode": "continuous"})


async def test_ptz_preset_goto_by_name_or_token(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await _call(hass, "ptz_preset", {**FRONT, "action": "goto", "preset": "Door"})
    await _call(hass, "ptz_preset", {**FRONT, "action": "goto", "preset": "2"})
    assert nvr.calls("POST", "/cam1/ptz") == [
        {"action": "preset", "token": "1"},
        {"action": "preset", "token": "2"},
    ]
    with pytest.raises(ServiceValidationError, match="Nowhere"):
        await _call(hass, "ptz_preset", {**FRONT, "action": "goto", "preset": "Nowhere"})
    with pytest.raises(ServiceValidationError, match="Choose a preset"):
        await _call(hass, "ptz_preset", {**FRONT, "action": "goto"})


async def test_ptz_preset_save_and_delete(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    await _call(hass, "ptz_preset", {**FRONT, "action": "save", "name": "Porch"})
    assert nvr.calls("POST", "/cam1/ptz/presets") == [{"name": "Porch"}]
    assert "Porch" in hass.states.get("select.front_door_ptz_preset").attributes["options"]

    await _call(hass, "ptz_preset", {**FRONT, "action": "save", "preset": "Door", "name": "Main door"})
    assert nvr.calls("POST", "/cam1/ptz/presets")[-1] == {"name": "Main door", "token": "1"}

    await _call(hass, "ptz_preset", {**FRONT, "action": "delete", "preset": "Gate"})
    assert nvr.calls("DELETE", "/cam1/ptz/presets") == [{"token": "2"}]
    assert "Gate" not in hass.states.get("select.front_door_ptz_preset").attributes["options"]
    with pytest.raises(ServiceValidationError, match="name"):
        await _call(hass, "ptz_preset", {**FRONT, "action": "save"})


async def test_ptz_preset_management_needs_admin(hass: HomeAssistant, nvr: FakeNVR, load) -> None:
    nvr.role = "user"
    await load()
    with pytest.raises(ServiceValidationError, match="administrator"):
        await _call(hass, "ptz_preset", {**FRONT, "action": "save", "name": "x"})
    await _call(hass, "ptz_preset", {**FRONT, "action": "goto", "preset": "Door"})  # allowed for everyone


async def test_ptz_api_error_is_reported(hass: HomeAssistant, nvr: FakeNVR, setup) -> None:
    nvr.ptz["cam1"]["supported"] = True
    nvr.ptz["cam2"] = {**nvr.ptz["cam1"]}  # the NVR now says cam2 has PTZ, but HA has not polled yet
    nvr.cameras = [c for c in nvr.cameras if c["id"] != "cam1"]
    with pytest.raises(HomeAssistantError, match="找不到攝影機"):
        await _call(hass, "ptz_move", {**FRONT, "pan": 1})


async def test_export_clip_writes_into_media_dir(hass: HomeAssistant, nvr: FakeNVR, setup, tmp_path: Path) -> None:
    hass.config.media_dirs = {"local": str(tmp_path)}
    result = await _call(
        hass,
        "export_clip",
        {**FRONT, "start": "2026-01-01 12:00:00", "duration": 30},
        return_response=True,
    )
    path = tmp_path / "cookienvr" / "Front Door_20260101-120000.mp4"
    assert path.read_bytes() == MP4
    assert result["camera.front_door"] == {
        "path": str(path),
        "media_content_id": "media-source://media_source/local/cookienvr/Front Door_20260101-120000.mp4",
    }
    (job,) = nvr.calls("POST", "/api/exports")
    assert job["cameraId"] == "cam1" and job["endMs"] - job["startMs"] == 30_000 and job["tz"] == hass.config.time_zone
    assert [r for r in nvr.requests if r[0] == "DELETE" and r[1] == "/api/exports/exp1"]


async def test_export_clip_custom_name_cannot_escape_the_folder(
    hass: HomeAssistant, nvr: FakeNVR, setup, tmp_path: Path
) -> None:
    hass.config.media_dirs = {"local": str(tmp_path)}
    result = await _call(
        hass,
        "export_clip",
        {**FRONT, "start": "2026-01-01 12:00:00", "end": "2026-01-01 12:01:00", "filename": "../../evil"},
        return_response=True,
    )
    assert (tmp_path / "cookienvr" / "evil.mp4").exists()
    assert result["camera.front_door"]["path"].startswith(str(tmp_path / "cookienvr"))


async def test_export_clip_failures(hass: HomeAssistant, nvr: FakeNVR, setup, tmp_path: Path) -> None:
    hass.config.media_dirs = {"local": str(tmp_path)}
    start = "2026-01-01 12:00:00"
    nvr.export_state = "failed"
    with pytest.raises(HomeAssistantError, match="沒有錄影"):
        await _call(hass, "export_clip", {**FRONT, "start": start, "duration": 10}, return_response=True)
    nvr.export_state = "done"
    with pytest.raises(ServiceValidationError, match="either end or duration"):
        await _call(hass, "export_clip", {**FRONT, "start": start}, return_response=True)
    with pytest.raises(ServiceValidationError, match="either end or duration"):
        await _call(hass, "export_clip", {**FRONT, "start": start, "end": start, "duration": 5}, return_response=True)
    with pytest.raises(ServiceValidationError, match="after the start"):
        await _call(hass, "export_clip", {**FRONT, "start": start, "end": "2026-01-01 11:00:00"}, return_response=True)
    hass.config.media_dirs = {}
    with pytest.raises(ServiceValidationError, match="media directory"):
        await _call(hass, "export_clip", {**FRONT, "start": start, "duration": 10}, return_response=True)
