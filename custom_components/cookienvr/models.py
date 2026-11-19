"""Helpers over the raw JSON the NVR returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

type Json = dict[str, Any]


@dataclass
class NVRState:
    """Everything the entities read, kept in sync by polling and the SSE stream."""

    cameras: dict[str, Json] = field(default_factory=dict)
    active: dict[str, dict[int, Json]] = field(default_factory=dict)
    last_event: dict[str, Json] = field(default_factory=dict)
    motion: set[str] = field(default_factory=set)
    ptz: dict[str, Json] = field(default_factory=dict)
    system: Json | None = None
    workers: dict[str, Json] = field(default_factory=dict)
    admin: bool = False
    username: str = ""


def camera_name(camera: Json) -> str:
    return str(camera.get("config", {}).get("name") or camera["id"])


def detect_config(camera: Json) -> Json | None:
    detect = camera.get("config", {}).get("detect")
    return detect if isinstance(detect, dict) else None


def detect_enabled(camera: Json) -> bool | None:
    """None when this NVR version has no detection setting."""
    detect = detect_config(camera)
    if detect is None or not isinstance(detect.get("enabled"), bool):
        return None
    return detect["enabled"]


def record_enabled(camera: Json) -> bool | None:
    record = camera.get("config", {}).get("record")
    if not isinstance(record, dict) or not isinstance(record.get("enabled"), bool):
        return None
    return record["enabled"]


def object_labels(camera: Json) -> list[str]:
    """Object classes this camera is configured to tag events with (empty until object detection ships)."""
    detect = detect_config(camera)
    labels = detect.get("labels") if detect else None
    if not isinstance(labels, list):
        return []
    return sorted({label for label in labels if isinstance(label, str) and label})


def camera_model(camera: Json) -> str:
    connection = camera.get("config", {}).get("connection", {})
    return "Tapo" if connection.get("type") == "tapo" else "RTSP camera"


def ptz_supported(state: NVRState, camera_id: str) -> bool:
    return state.ptz.get(camera_id, {}).get("supported") is True


def preset_options(ptz: Json) -> dict[str, str]:
    """Map a unique display name to the preset token."""
    options: dict[str, str] = {}
    for preset in ptz.get("presets", []):
        name = str(preset.get("name") or preset["token"])
        if name in options:
            name = f"{name} ({preset['token']})"
        options[name] = preset["token"]
    return options
