"""Caption (.txt sidecar) generation and editing."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_TEMPLATE = "{series}, {extra}, {quality}, {orientation}"
PLACEHOLDERS = "{series}  {extra}  {quality}  {orientation}"


@dataclass
class CaptionSpec:
    template: str = DEFAULT_TEMPLATE
    good_tags: str = "masterpiece, high quality"
    low_tags: str = "low quality, bad anatomy, worst quality"
    extra_tags: str = ""


def split_tags(text: str) -> list[str]:
    out, seen = [], set()
    for t in re.split(r"[,\n]", text or ""):
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def render_caption(spec: CaptionSpec, series: str, orientation: str, low_quality: bool = False) -> str:
    text = (spec.template.replace("{series}", series)
            .replace("{extra}", spec.extra_tags)
            .replace("{quality}", spec.low_tags if low_quality else spec.good_tags)
            .replace("{orientation}", orientation.lower() if orientation != "Square" else "1:1"))
    return ", ".join(split_tags(text))


def apply_caption(txt_path: str, caption: str, mode: str, swap: tuple[str, str] | None = None) -> bool:
    """mode: off | add (only if missing) | append (add missing tags) | overwrite. True if the file changed."""
    if mode == "off":
        return False
    if not os.path.isfile(txt_path):
        if not caption:
            return False
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(caption)
        return True
    try:
        with open(txt_path, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        return False
    new = old
    if swap and swap[0] and mode in ("add", "append"):
        new = new.replace(swap[0], swap[1])
    if mode == "overwrite":
        if not caption:
            return False
        new = caption
    elif mode == "append":
        have = {t.lower() for t in split_tags(new)}
        extra = [t for t in split_tags(caption) if t.lower() not in have]
        if extra:
            new = (new.rstrip().rstrip(",") + ", " if new.strip() else "") + ", ".join(extra)
    if new != old:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(new)
        return True
    return False
