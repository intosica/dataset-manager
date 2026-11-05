"""Image file helpers: saving and thumbnails."""
from __future__ import annotations

import functools
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from . import paths

_SAVE_KW = {".jpg": dict(format="JPEG", quality=95, subsampling=0),
            ".jpeg": dict(format="JPEG", quality=95, subsampling=0),
            ".webp": dict(format="WEBP", quality=95), ".png": dict(format="PNG"), ".bmp": dict(format="BMP")}


def save_image(img: Image.Image, dest: str) -> None:
    img.save(dest, **_SAVE_KW.get(os.path.splitext(dest)[1].lower(), {}))


@functools.cache
def _thumb_dir():
    folder = paths.cache_dir() / "thumbnails"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def make_thumbnail(path: str, side: int = 256) -> str | None:
    """Cached JPEG thumbnail; returns its path, or None if the image can't be read."""
    try:
        st = os.stat(path)
        key = hashlib.md5(f"{path}|{st.st_mtime_ns}|{side}".encode()).hexdigest()
        out = _thumb_dir() / f"{key}.jpg"
        if not out.exists():
            with Image.open(path) as im:
                try:
                    im.draft("RGB", (side * 2, side * 2))
                except Exception:
                    pass
                rgb = im.convert("RGB")
            rgb.thumbnail((side, side))
            tmp = out.with_name(f"{key}.{threading.get_ident()}.tmp")
            rgb.save(tmp, "JPEG", quality=85)
            os.replace(tmp, out)           # atomic: a crash or a racing worker never leaves a broken thumbnail
        return str(out)
    except Exception:
        return None


def make_thumbnails(file_paths: list[str], side: int = 256) -> list[str | None]:
    with ThreadPoolExecutor(min(8, os.cpu_count() or 2)) as ex:
        return list(ex.map(lambda p: make_thumbnail(p, side), file_paths))
