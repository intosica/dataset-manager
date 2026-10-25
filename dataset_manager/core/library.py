"""Read-only library queries: per-collection statistics and preview file lists."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .tasks import TaskContext
from .utils import LOW_QUALITY_DIRS, ORIENTATION_DIRS, collection_dirs, has_part, iter_images, pick


@dataclass
class CollectionStats:
    name: str
    path: str
    total: int = 0
    landscape: int = 0
    portrait: int = 0
    square: int = 0
    low_quality: int = 0
    size: int = 0


def library_stats(base: str, ctx: TaskContext | None = None) -> list[CollectionStats]:
    rows = []
    if not os.path.isdir(base):
        return rows
    for path, _tag in collection_dirs(base):
        s = CollectionStats(os.path.basename(path.rstrip("/")) or path, path)
        for f in iter_images(path):
            parts = {x.lower() for x in Path(f).relative_to(path).parts[:-1]}
            s.total += 1
            s.landscape += "landscape" in parts
            s.portrait += "portrait" in parts
            s.square += "square" in parts
            s.low_quality += bool(parts & LOW_QUALITY_DIRS)
            try:
                s.size += os.path.getsize(f)
            except OSError:
                pass
        rows.append(s)
        if ctx and ctx.cancelled:
            break
    return rows


def preview_files(collection_path: str, view: str = "All", limit: int = 60, shuffle: bool = True) -> list[str]:
    """view: All | Landscape | Portrait | Square | Low quality"""
    files = list(iter_images(collection_path))
    if view == "Low quality":
        files = [f for f in files if has_part(f, LOW_QUALITY_DIRS)]
    elif view in ORIENTATION_DIRS:
        files = [f for f in files if has_part(f, view) and not has_part(f, LOW_QUALITY_DIRS)]
    return pick(files, limit, shuffle)
