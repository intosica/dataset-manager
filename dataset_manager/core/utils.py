from __future__ import annotations

import os
import random
import re
from pathlib import Path

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
ORIENTATION_DIRS = ("Landscape", "Portrait", "Square")
LOW_QUALITY_DIR = "low_quality"
LOW_QUALITY_DIRS = {"low_quality", "desperate"}   # "desperate" = folder name used by older versions
HIDDEN_DIRS = {"_duplicates_trash"}


def clean_tag(text: str) -> str:
    return re.sub(r"[\s-]+", "_", re.sub(r"[^\w\s]", "", text)).strip("_").lower()


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name).strip(" .")
    return re.sub(r"\s+", "_", name)[:80] or "Untitled"


def orientation(w: int, h: int) -> str:
    return "Landscape" if w > h else "Portrait" if h > w else "Square"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def iter_images(base: str):
    """Every image under base, skipping hidden folders."""
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in HIDDEN_DIRS and not d.startswith(".")]
        for f in files:
            if f.lower().endswith(IMAGE_EXTS):
                yield os.path.join(root, f)


def has_part(path: str, names) -> bool:
    """True if any folder component of `path` matches (case-insensitive)."""
    names = {names.lower()} if isinstance(names, str) else {n.lower() for n in names}
    return any(p.lower() in names for p in Path(path).parts[:-1])


def sidecars(path: str) -> list[str]:
    txt = os.path.splitext(path)[0] + ".txt"
    return [txt] if os.path.isfile(txt) else []


def list_collections(base: str) -> list[str]:
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base)
                  if os.path.isdir(os.path.join(base, d)) and d not in HIDDEN_DIRS and not d.startswith("."))


def collection_dirs(base: str) -> list[tuple[str, str]]:
    """[(path, tag)] — treats `base` itself as one collection if it already looks like one."""
    names = list_collections(base)
    loose = any(f.lower().endswith(IMAGE_EXTS) for f in os.listdir(base))
    if loose or (names and all(n in ORIENTATION_DIRS for n in names)):
        return [(base, clean_tag(os.path.basename(base.rstrip("/"))) or "collection")]
    return [(os.path.join(base, n), clean_tag(n)) for n in names]


def prune_empty_dirs(root: str, keep_root: bool = True) -> int:
    removed = 0
    for r, _dirs, _files in os.walk(root, topdown=False):
        if r == root and keep_root:
            continue
        try:
            if not os.listdir(r):
                os.rmdir(r)
                removed += 1
        except OSError:
            pass
    return removed


def pick(files: list[str], n: int, shuffle: bool) -> list[str]:
    return random.sample(files, min(n, len(files))) if shuffle else sorted(files)[:n]
