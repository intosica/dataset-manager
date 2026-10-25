"""ImageSink: the single place where downloaded bytes become library files."""
from __future__ import annotations

import hashlib
import io
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field

from PIL import Image

from .captions import CaptionSpec, render_caption
from .duplicates import DuplicateGuard, analyze_bytes, open_cache
from .imaging import save_image
from .upscaler import resize_image
from .utils import iter_images, orientation


@dataclass
class SinkOptions:
    min_side: int = 256
    resize: bool = False
    target_dim: int = 1024
    direction: str = "both"
    snap_to_64: bool = True
    skip_duplicates: bool = True
    duplicate_threshold: int = 6
    write_captions: bool = True
    caption: CaptionSpec = field(default_factory=CaptionSpec)


class ImageSink:
    def __init__(self, save_dir: str, series_tag: str, opts: SinkOptions):
        self.save_dir, self.series_tag, self.opts = save_dir, series_tag, opts
        self.counts = defaultdict(int)
        self.guard: DuplicateGuard | None = None
        self.stems: set[str] = set()
        self._seen_file = os.path.join(save_dir, ".seen_urls.json")
        self._seen: set[str] = set()

    # ---- setup / teardown ----
    def prepare(self) -> None:
        os.makedirs(self.save_dir, exist_ok=True)
        self.stems = {os.path.splitext(os.path.basename(p))[0] for p in iter_images(self.save_dir)}
        if self.opts.skip_duplicates:
            try:
                with open(self._seen_file, encoding="utf-8") as f:
                    self._seen = set(json.load(f))
            except (OSError, ValueError):
                pass
            self.guard = DuplicateGuard.from_folder(self.save_dir, self.opts.duplicate_threshold, open_cache())

    def flush(self) -> None:
        if self.opts.skip_duplicates:
            try:
                with open(self._seen_file, "w", encoding="utf-8") as f:
                    json.dump(sorted(self._seen), f)
            except OSError:
                pass

    # ---- download history ----
    @staticmethod
    def _key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def is_seen(self, url: str) -> bool:
        return self.opts.skip_duplicates and self._key(url) in self._seen

    def mark_seen(self, url: str) -> None:
        self._seen.add(self._key(url))

    def has_name(self, stem: str) -> bool:
        return stem in self.stems

    # ---- ingest ----
    def ingest(self, data: bytes, name: str) -> str:
        """Returns: saved | exists | duplicate | small | invalid"""
        status = self._ingest(data, name)
        self.counts[status] += 1
        return status

    def _ingest(self, data: bytes, name: str) -> str:
        if name in self.stems:
            return "exists"
        try:
            rec = analyze_bytes(data)
            if min(rec["w"], rec["h"]) < self.opts.min_side:
                return "small"
            if self.guard and self.guard.check(rec):
                return "duplicate"
            with Image.open(io.BytesIO(data)) as im:
                img = im.convert("RGB")
            if self.opts.resize:
                img = resize_image(img, self.opts.target_dim, self.opts.direction, self.opts.snap_to_64)
            fld = orientation(*img.size)
            folder = os.path.join(self.save_dir, fld)
            os.makedirs(folder, exist_ok=True)
            save_image(img, os.path.join(folder, f"{name}.jpg"))
            if self.opts.write_captions:
                caption = render_caption(self.opts.caption, self.series_tag, fld)
                if caption:
                    with open(os.path.join(folder, f"{name}.txt"), "w", encoding="utf-8") as f:
                        f.write(caption)
            if self.guard:
                self.guard.add(rec)
            self.stems.add(name)
            return "saved"
        except Exception:
            return "invalid"

    def summary(self) -> str:
        c = self.counts
        return (f"{c['saved']} saved, {c['duplicate'] + c['exists']} duplicates skipped, {c['small']} too small, "
                f"{c['invalid']} unreadable")
