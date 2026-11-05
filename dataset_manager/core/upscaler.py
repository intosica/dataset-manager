"""Image resizing with optional AI upscaling (spandrel). Heavy imports happen lazily."""
from __future__ import annotations

import logging
import os
import threading

import numpy as np
from PIL import Image

from . import capabilities, paths

log = logging.getLogger(__name__)
MODEL_EXTS = (".safetensors", ".pth", ".pt")


def discover_models(dirs: list[str]) -> list[str]:
    found = []
    for d in dirs:
        if d and os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f)
                if f.lower().endswith(MODEL_EXTS) and os.path.isfile(p) and p not in found:
                    found.append(p)
    return found


def _snap64(x: float) -> int:
    return int(round(x / 64) * 64)


class Upscaler:
    def __init__(self):
        self.model_path: str | None = None
        self.model = None
        self.device = "cpu"
        self.last_error = ""
        self._lock = threading.Lock()

    def configure(self, setting: str, dirs: list[str]) -> None:
        """setting: '' = first model found, 'none' = standard resize only, or a full path."""
        if setting == "none":
            path = None
        elif setting and os.path.isfile(paths.resolve(setting)):
            path = paths.resolve(setting)      # accepts ~, relative paths and file:// URLs
        else:
            models = discover_models(dirs)
            path = models[0] if models else None
        if path != self.model_path:
            self.model_path, self.model, self.last_error = path, None, ""

    def check(self) -> tuple[bool, str]:
        """Load the selected model now and report whether it works (used by the Test button)."""
        if not self.model_path:
            return False, "No upscale model selected — standard resampling is used."
        if not (capabilities.has("torch") and capabilities.has("spandrel")):
            return False, "Needs: pip install torch spandrel"
        name = os.path.basename(self.model_path)
        if not self._load():
            return False, f"Could not load {name}: {self.last_error or 'unsupported model file'}"
        scale = getattr(self.model, "scale", None)
        return True, f"{name} loaded on {self.device}" + (f" (×{scale} model)" if scale else "")

    @property
    def available(self) -> bool:
        return bool(self.model_path and os.path.isfile(self.model_path)
                    and capabilities.has("torch") and capabilities.has("spandrel"))

    def _load(self) -> bool:
        if self.model is not None:
            return True
        if not self.available:
            return False
        with self._lock:
            if self.model is None:
                try:
                    import torch
                    from spandrel import ModelLoader
                    self.device = "cuda" if torch.cuda.is_available() else "cpu"
                    self.model = ModelLoader().load_from_file(self.model_path).to(self.device).eval()
                except Exception as e:  # unreadable / unsupported model file
                    self.last_error = str(e)
                    log.warning("Could not load upscale model %s: %s", self.model_path, e)
                    self.model_path = None
        return self.model is not None

    def _tiled(self, t, tile: int, pad: int = 16):
        import torch
        _, c, h, w = t.shape
        out = None
        for y in range(0, h, tile):
            for x in range(0, w, tile):
                y0, x0 = max(y - pad, 0), max(x - pad, 0)
                y1, x1 = min(y + tile + pad, h), min(x + tile + pad, w)
                patch = t[:, :, y0:y1, x0:x1].to(self.device)
                with torch.no_grad():
                    o = self.model(patch).float().cpu()
                s = o.shape[-1] // patch.shape[-1]
                if out is None:
                    out = torch.zeros((1, c, h * s, w * s))
                th, tw = min(tile, h - y), min(tile, w - x)
                oy, ox = (y - y0) * s, (x - x0) * s
                out[:, :, y * s:(y + th) * s, x * s:(x + tw) * s] = o[:, :, oy:oy + th * s, ox:ox + tw * s]
        return out

    def upscale(self, img: Image.Image):
        if not self._load():
            return None
        import torch
        arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
        t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
        for tile in (384, 192, 96):          # retry with smaller tiles on out-of-memory
            try:
                out = self._tiled(t, tile)
                out = (out.squeeze(0).permute(1, 2, 0).clamp(0, 1).numpy() * 255).round().astype(np.uint8)
                return Image.fromarray(out)
            except RuntimeError as e:
                if "out of memory" in str(e).lower():
                    if self.device == "cuda":
                        torch.cuda.empty_cache()
                    continue
                log.warning("AI upscale failed: %s", e)
                return None
            except Exception as e:
                log.warning("AI upscale failed: %s", e)
                return None
        return None


_upscaler = Upscaler()


def upscale_folder(folder: str, ctx, target: int, snap: bool = True, preserve: bool = False) -> dict:
    """Enlarge every image under folder to `target` on the longer side, in place.
    preserve: the untouched original is copied to original_images/ next to it first."""
    import shutil

    from PIL import Image, ImageOps

    from .imaging import save_image
    from .utils import iter_images

    stats = {"upscaled": 0, "unchanged": 0, "failed": 0}
    if not _upscaler.available:
        ctx.log("No AI upscale model available: choose one in Preferences → Upscaling (needs torch + spandrel).")
        return stats
    files = [f for f in iter_images(folder) if "original_images" not in os.path.normpath(f).split(os.sep)]
    for i, p in enumerate(files):
        if ctx.cancelled:
            ctx.log("Cancelled.")
            break
        ctx.progress(i, len(files), f"Upscaling {i + 1} / {len(files)}")
        try:
            with Image.open(p) as im:
                img = ImageOps.exif_transpose(im).convert("RGB")
            out = resize_image(img, target, "up", snap)
            if out.size == img.size:
                stats["unchanged"] += 1
                continue
            if preserve:
                keep = os.path.join(os.path.dirname(p), "original_images")
                os.makedirs(keep, exist_ok=True)
                keep = os.path.join(keep, os.path.basename(p))
                if not os.path.exists(keep):
                    shutil.copy2(p, keep)
            save_image(out, p)
            stats["upscaled"] += 1
        except Exception as exc:
            stats["failed"] += 1
            ctx.log(f"  {os.path.basename(p)}: {exc}")
    ctx.progress(len(files), len(files), "")
    ctx.log(f"Upscaled {stats['upscaled']:,} images"
            + (f", {stats['unchanged']:,} already large enough" if stats["unchanged"] else "")
            + (f", {stats['failed']:,} failed" if stats["failed"] else "")
            + (" · originals kept in original_images/" if preserve else ""))
    return stats


def get_upscaler() -> Upscaler:
    return _upscaler


def resize_image(img: Image.Image, target: int, direction: str = "both", snap: bool = True) -> Image.Image:
    """Resize so the LONGER side equals `target`. direction: both | up | down."""
    w, h = img.size
    longest = max(w, h)
    if (longest > target and direction == "up") or (longest < target and direction == "down"):
        return img
    nw, nh = w * target / longest, h * target / longest
    if snap:
        nw, nh = max(64, _snap64(nw)), max(64, _snap64(nh))
    else:
        nw, nh = max(1, int(round(nw))), max(1, int(round(nh)))
    if (nw, nh) == (w, h):
        return img
    if longest < target and _upscaler.available:
        up = _upscaler.upscale(img)
        if up is not None:
            img = up
    return img.resize((nw, nh), Image.LANCZOS)
