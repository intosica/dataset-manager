"""Image file helpers: saving and thumbnails."""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from . import paths

_SAVE_KW = {".jpg": dict(format="JPEG", quality=95, subsampling=0),
            ".jpeg": dict(format="JPEG", quality=95, subsampling=0),
            ".webp": dict(format="WEBP", quality=95), ".png": dict(format="PNG"), ".bmp": dict(format="BMP")}


def save_image(img: Image.Image, dest: str) -> None:
    img.save(dest, **_SAVE_KW.get(os.path.splitext(dest)[1].lower(), {}))


def make_thumbnail(path: str, side: int = 256) -> str | None:
    """Cached JPEG thumbnail; returns its path, or None if the image can't be read."""
    try:
        st = os.stat(path)
        key = hashlib.md5(f"{path}|{st.st_mtime_ns}|{side}".encode()).hexdigest()
        folder = paths.cache_dir() / "thumbnails"
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"{key}.jpg"
        if not out.exists():
            with Image.open(path) as im:
                try:
                    im.draft("RGB", (side * 2, side * 2))
                except Exception:
                    pass
                rgb = im.convert("RGB")
            rgb.thumbnail((side, side))
            rgb.save(out, "JPEG", quality=85)
        return str(out)
    except Exception:
        return None


def make_thumbnails(file_paths: list[str], side: int = 256) -> list[str | None]:
    with ThreadPoolExecutor(min(8, os.cpu_count() or 2)) as ex:
        return list(ex.map(lambda p: make_thumbnail(p, side), file_paths))
