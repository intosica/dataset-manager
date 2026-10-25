"""Where things live. Follows the XDG spec; a `portable` folder switches to ./data."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .. import APP_ID

APP_DIR = Path(__file__).resolve().parents[2]  # the folder containing run.sh and venv/


def portable() -> bool:
    return os.environ.get("DATASET_MANAGER_PORTABLE") == "1" or (APP_DIR / "portable").exists()


def _dir(xdg_env: str, fallback: str, portable_name: str) -> Path:
    if portable():
        p = APP_DIR / "data" / portable_name
    else:
        p = Path(os.environ.get(xdg_env) or Path.home() / fallback) / APP_ID
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    return _dir("XDG_CONFIG_HOME", ".config", "config")


def cache_dir() -> Path:
    return _dir("XDG_CACHE_HOME", ".cache", "cache")


def data_dir() -> Path:
    return _dir("XDG_DATA_HOME", ".local/share", "share")


def clean_user_path(p) -> str:
    """Tidy pasted paths: quotes, whitespace, file:// URLs from file managers."""
    s = str(p or "").strip().strip("\"'").strip()
    if s.startswith("file://"):
        s = unquote(urlparse(s).path)
    return s


def resolve(p, base: Path = APP_DIR) -> str:
    s = clean_user_path(p)
    if not s:
        return ""
    q = Path(os.path.expandvars(os.path.expanduser(s)))
    if not q.is_absolute():
        q = base / q
    return os.path.normpath(str(q))


def pictures_dir() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    try:
        m = re.search(r'XDG_PICTURES_DIR="([^"]+)"', cfg.read_text("utf-8"))
        if m:
            return Path(m.group(1).replace("$HOME", str(Path.home())))
    except OSError:
        pass
    return Path.home() / "Pictures"


def default_library_dir() -> Path:
    return pictures_dir() / "Dataset Library"


def default_model_dirs() -> list[Path]:
    return [APP_DIR / "models", APP_DIR / "upscaler", data_dir() / "models"]  # "upscaler" = legacy layout


def default_profile_dir() -> Path:
    return data_dir() / "browser-profile"
