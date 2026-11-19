"""Display names for object classes: the one place that decides what a label sensor is called.

Labels are the NVR's keys (lowercase with underscores). Common ones have names in `translations/`;
anything else, such as a class from a custom model, shows its key made readable. When CookieNVR
exposes the classes its model supports with display names, look them up here instead.
"""

from __future__ import annotations

TRANSLATED_LABELS = frozenset(
    {
        "person",
        "car",
        "motorcycle",
        "bicycle",
        "truck",
        "bus",
        "cat",
        "dog",
        "bird",
        "vehicle",
        "pet",
        "package",
    }
)


def readable_label(label: str) -> str:
    """`package_box` -> `Package box`."""
    words = label.replace("_", " ").replace("-", " ").split()
    return " ".join(words).capitalize() if words else label


def label_translation(label: str) -> tuple[str, dict[str, str]]:
    """The entity translation key and placeholders that name a label sensor."""
    if label in TRANSLATED_LABELS:
        return f"object_{label}", {}
    return "object", {"label": readable_label(label)}
