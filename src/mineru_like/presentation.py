"""Reader annotations; source and model-facing content stays unlabelled."""

import re
from dataclasses import dataclass


@dataclass
class Annotation:
    role: str
    content: str
    before: bool = False
    generated: bool = False


def annotation(
    role: str,
    content: str = "",
    *,
    location: str | None = None,
    asset: str | None = None,
    cue: str | None = None,
) -> str:
    label = role + (f"; {location}" if location else "")
    if role.startswith("Unclear") or role.startswith("Running"):
        if content:
            label += ": " + content
        if cue:
            label += "; " + cue.rstrip(".") + "."
        return "[" + label + "]"
    parts = ["[" + label + "]"]
    if asset:
        parts.append(f"[view_src_img]({asset})")
    if content:
        parts.append(content)
    if cue:
        parts.append(cue)
    return " ".join(parts)


def unclear(kind: str, location: str | None = None) -> str:
    return annotation("Unclear " + kind, location=location, cue="zoom if possible")


def visual_description(content: str, asset: str | None) -> str:
    cue = "View source image if helpful; Caption may be partial/unreliable."
    return annotation("AI img desc", content, asset=asset, cue=cue)


def list_item(text: str, depth: int = 0, *, index: bool = False) -> str:
    if index or re.match(r"^\s*(?:[-+*]\s|(?:\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+)[.)]\s)", text):
        return "  " * depth + text
    if re.fullmatch(r"\s*(?:\.{3}|…|\\cdots)\s*", text):
        return "  " * depth + text
    return "  " * depth + "- " + text
