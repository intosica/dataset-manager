from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

from . import paths
from .captions import DEFAULT_TEMPLATE, CaptionSpec


@dataclass
class Settings:
    library_dir: str = ""
    browser_path: str = ""            # blank = auto-detect
    browser_profile_dir: str = ""     # blank = app data folder
    headless: bool = False
    model_dirs: list = field(default_factory=list)   # extra folders scanned for upscale models
    upscale_model: str = ""           # "" = first found · "none" = standard resize · or a full path
    target_dim: int = 1024
    scale_direction: str = "both"     # both | up | down
    snap_to_64: bool = True
    duplicate_threshold: int = 6
    min_image_side: int = 256
    parallel_downloads: int = 4
    caption_template: str = DEFAULT_TEMPLATE
    good_quality_tags: str = "masterpiece, high quality"
    low_quality_tags: str = "low quality, bad anatomy, worst quality"
    extra_tags: str = ""

    # ---- derived ----
    def library_path(self) -> str:
        return paths.resolve(self.library_dir) or str(paths.default_library_dir())

    def profile_path(self) -> str:
        return paths.resolve(self.browser_profile_dir) or str(paths.default_profile_dir())

    def caption_spec(self) -> CaptionSpec:
        return CaptionSpec(self.caption_template, self.good_quality_tags, self.low_quality_tags, self.extra_tags)

    def all_model_dirs(self) -> list[str]:
        dirs = [paths.resolve(d) for d in self.model_dirs] + [str(d) for d in paths.default_model_dirs()]
        return [d for i, d in enumerate(dirs) if d and d not in dirs[:i]]

    # ---- persistence ----
    @staticmethod
    def file() -> str:
        return str(paths.config_dir() / "settings.json")

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            with open(cls.file(), encoding="utf-8") as f:
                data = json.load(f)
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        tmp = self.file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, self.file())
