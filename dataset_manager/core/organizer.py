"""Library maintenance: de-duplicate, sort, rename, convert, resize, flag low quality, caption."""
from __future__ import annotations

import logging
import os
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import capabilities
from .aesthetics import detector
from .captions import CaptionSpec, apply_caption, render_caption
from .duplicates import apply_removals, scan_duplicates
from .imaging import save_image
from .tasks import TaskContext
from .upscaler import get_upscaler, resize_image
from .utils import (LOW_QUALITY_DIR, LOW_QUALITY_DIRS, clean_tag, collection_dirs, iter_images, orientation,
                    prune_empty_dirs)

log = logging.getLogger(__name__)


@dataclass
class OrganizeOptions:
    remove_duplicates: bool = False
    duplicate_threshold: int = 6
    detect_low_quality: bool = False
    low_quality_strictness: float = 0.45
    sort_by_orientation: bool = True
    clean_filenames: bool = True
    convert_to_jpg: bool = True
    resize: bool = False
    target_dim: int = 1024
    direction: str = "both"          # both | up | down
    snap_to_64: bool = True
    caption_mode: str = "add"        # off | add | append | overwrite
    caption: CaptionSpec = field(default_factory=CaptionSpec)

    def per_image_steps(self) -> bool:
        return any([self.detect_low_quality, self.sort_by_orientation, self.clean_filenames, self.convert_to_jpg,
                    self.resize, self.caption_mode != "off"])


def _process_image(path: str, root: str, tag: str, o: OrganizeOptions, stats: dict) -> None:
    rel_dirs = [x.lower() for x in Path(path).relative_to(root).parts[:-1]]
    was_low = any(d in LOW_QUALITY_DIRS for d in rel_dirs)
    with Image.open(path) as im:
        is_jpeg = (im.format or "").upper() == "JPEG"
        img = im.convert("RGB")

    low = detector.is_low_quality(img, o.low_quality_strictness) if o.detect_low_quality else was_low

    resized = False
    if o.resize and not low:
        new = resize_image(img, o.target_dim, o.direction, o.snap_to_64)
        if new.size != img.size:
            img, resized = new, True
            stats["resized"] += 1
    fld = orientation(*img.size)

    if o.sort_by_orientation:
        t_dir = os.path.join(root, fld, LOW_QUALITY_DIR) if low else os.path.join(root, fld)
    else:
        t_dir = os.path.dirname(path)
    src_stem, src_ext = os.path.splitext(os.path.basename(path))
    src_ext = src_ext.lower()
    stem = (clean_tag(src_stem) or "image") if o.clean_filenames else src_stem
    ext = ".jpg" if o.convert_to_jpg else src_ext
    os.makedirs(t_dir, exist_ok=True)

    def is_self(p: str) -> bool:   # also true on case-insensitive mounts (exFAT/NTFS drives)
        return os.path.abspath(p) == os.path.abspath(path) or (os.path.exists(p) and os.path.samefile(p, path))

    dest, n = os.path.join(t_dir, stem + ext), 1
    while os.path.exists(dest) and not is_self(dest):
        dest = os.path.join(t_dir, f"{stem}_{n}{ext}")
        n += 1

    reencode = resized or (o.convert_to_jpg and not is_jpeg)
    if reencode:
        save_image(img, dest)
        if not is_self(dest):
            os.remove(path)
    elif not is_self(dest):
        shutil.move(path, dest)                       # lossless move
    elif os.path.abspath(dest) != os.path.abspath(path):
        try:
            os.rename(path, dest)                     # case-only rename
        except OSError:
            dest = path
    if reencode or os.path.abspath(dest) != os.path.abspath(path):
        stats["moved"] += 1

    # captions always travel with their image
    old_txt, new_txt = os.path.splitext(path)[0] + ".txt", os.path.splitext(dest)[0] + ".txt"
    if os.path.isfile(old_txt) and os.path.abspath(old_txt) != os.path.abspath(new_txt) and not os.path.exists(new_txt):
        shutil.move(old_txt, new_txt)
    if o.caption_mode != "off":
        caption = render_caption(o.caption, tag, fld, low)
        swap = None
        if low != was_low:
            swap = (o.caption.good_tags, o.caption.low_tags) if low else (o.caption.low_tags, o.caption.good_tags)
        if apply_caption(new_txt, caption, o.caption_mode, swap):
            stats["captioned"] += 1
    if low:
        stats["low_quality"] += 1


def organize_library(base: str, o: OrganizeOptions, ctx: TaskContext) -> dict:
    stats: dict = defaultdict(int)
    if not os.path.isdir(base):
        ctx.log(f"Folder not found: {base}")
        return stats
    if not (o.remove_duplicates or o.per_image_steps()):
        ctx.log("No steps selected.")
        return stats

    if o.detect_low_quality and not capabilities.has("transformers"):
        ctx.log("Note: aesthetic model unavailable (pip install transformers); only the small-image rule applies.")
    if o.resize and o.direction != "down" and not get_upscaler().available:
        ctx.log("Note: no AI upscale model active; enlarging uses standard resampling.")

    if o.remove_duplicates:
        ctx.log("Step: remove duplicates")
        groups = scan_duplicates(base, o.duplicate_threshold, "library", ctx)
        if ctx.cancelled:
            return stats
        selection = [(g.keeper["path"], [d["path"] for d in g.duplicates]) for g in groups]
        res = apply_removals(selection, "trash", ctx)
        stats["duplicates_removed"] = res.removed
        ctx.log(f"Moved {res.removed} duplicate images to the Trash.")

    if o.per_image_steps() and not ctx.cancelled:
        ctx.log("Step: process images")
        work = [(root, tag, f) for root, tag in collection_dirs(base) for f in iter_images(root)]
        total = len(work)
        ctx.log(f"{total} images in {len({w[0] for w in work})} collections.")
        for i, (root, tag, f) in enumerate(work):
            if ctx.cancelled:
                ctx.log("Cancelled.")
                break
            try:
                _process_image(f, root, tag, o, stats)
            except Exception as e:
                stats["errors"] += 1
                ctx.log(f"Error on {os.path.basename(f)}: {e}")
                log.exception("organize failed for %s", f)
            ctx.progress(i + 1, total, f"Processing images ({i + 1}/{total})")
        if o.sort_by_orientation:
            for root, _ in collection_dirs(base):
                stats["folders_removed"] += prune_empty_dirs(root)

    ctx.log("Finished: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in stats.items()) if stats else "Finished.")
    return stats
