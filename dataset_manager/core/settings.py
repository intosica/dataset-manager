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
    headless: bool = False            # ignored by the imageboards, which always run hidden
    mature_content: bool = False      # reveals Rule34.xxx in the source list
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
    install_target: str = "venv"      # venv | system — where optional packages from Preferences go
    venv_dir: str = ""                # blank = <app data>/venv
    # model locations (blank = <app data>/models/<name>); a folder or a model file
    hydra_path: str = ""
    wd_path: str = ""
    joycaption_path: str = ""
    florence_path: str = ""         # blank = auto-detect ComfyUI models/LLM
    # caption-rewriting text model (GGUF); blank = <app data>/models/llm, Qwen2.5 1.5B uncensored Q8_0
    llm_path: str = ""               # the chosen GGUF's folder (or file)
    llm_repo: str = ""               # see models.LLM_REPOS
    llm_file: str = ""
    hf_cache_dir: str = ""            # blank = app cache; where CLIP / aesthetic / similarity models go
    tag_threshold: float = 0.0       # 0 = automatic (Hydra: calibrated per tag · WD: 0.35)
    tag_editor_dir: str = ""          # last folder opened in the Tag Editor
    tag_editor_follow_library: bool = False   # Tag Editor opens whatever the Library page has selected
    upscale_preserve_originals: bool = False  # Library → Upscale keeps copies in original_images/
    tagger: str = "hydra"             # hydra | wd | florence:<folder>
    florence_task: str = "more"      # dense | detailed | more | ocr
    joy_caption_type: str = "Descriptive"
    joy_caption_length: str = "long"
    joy_extra: list = field(default_factory=list)
    joy_name: str = ""
    joy_temperature: float = 0.6
    joy_max_tokens: int = 512

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
            for old in ("path", "repo", "file"):          # the Phi-4-only versions called these phi_*
                if f"phi_{old}" in data:
                    data.setdefault(f"llm_{old}", data[f"phi_{old}"])
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
