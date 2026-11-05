"""Auto-taggers: JTP Hydra 3.5 (e621 vocabulary) and WD EVA02 Large v3 (Danbooru vocabulary).

Both run with torch on the GPU when there is one. A loaded tagger stays in memory until another one is
chosen or `unload()` is called, so tagging a second batch starts instantly.
"""
from __future__ import annotations

import csv
import os
import threading

from . import models

_lock = threading.Lock()
_loaded: dict = {}          # {"key": ..., "path": ..., "tagger": ...}


def _device():
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


class HydraTagger:
    batch = 4

    def __init__(self, path: str, threshold: float):
        import torch
        from ..vendor.hydra.model import load_model
        self.torch = torch
        self.dev = _device()
        self.model = load_model(path).to(self.dev).eval()
        self.set_threshold(threshold)

    def set_threshold(self, threshold: float) -> None:
        # 0 = the per-tag thresholds Hydra was calibrated with (best precision/recall balance)
        self.cal = self.model.calibrate() if threshold <= 0 else self.model.calibrate(float(threshold))

    def tag(self, files: list[str]) -> list[dict]:
        from ..vendor.hydra import image as I
        imgs = [self.model.load_image(f) for f in files]
        with self.torch.inference_mode():
            p, s = I.stack(imgs, 16, self.model.image_config.max_seqlen, device=self.dev)
            out = self.model.forward(self.model.from_srgb(p), s).float().cpu()
        return [self.cal.classify_output(o, implications="inherit", exclude_categories={"lore"}) for o in out]


class WDTagger:
    batch = 8
    SIZE = 448

    def __init__(self, folder: str, threshold: float):
        import json

        import safetensors.torch
        import timm
        import torch
        self.torch = torch
        self.dev = _device()
        with open(os.path.join(folder, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.model = timm.create_model(cfg.get("architecture", "eva02_large_patch14_448"), pretrained=False,
                                       num_classes=cfg.get("num_classes", 10861), **cfg.get("model_args", {}))
        self.model.load_state_dict(safetensors.torch.load_file(os.path.join(folder, "model.safetensors")))
        self.dtype = torch.float16 if self.dev.type == "cuda" else torch.float32
        self.model = self.model.to(self.dev, dtype=self.dtype).eval()
        self.names, self.cats = [], []
        with open(os.path.join(folder, "selected_tags.csv"), encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.names.append(row["name"])
                self.cats.append(int(row["category"]))
        self.set_threshold(threshold)

    def set_threshold(self, threshold: float) -> None:
        self.general = threshold if threshold > 0 else 0.35
        self.character = max(self.general, 0.85)

    def _prep(self, path: str):
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.draft("RGB", (self.SIZE * 2, self.SIZE * 2))
            rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        side = max(bg.size)
        sq = Image.new("RGB", (side, side), (255, 255, 255))
        sq.paste(bg, ((side - bg.width) // 2, (side - bg.height) // 2))
        sq = sq.resize((self.SIZE, self.SIZE), Image.BICUBIC)
        import numpy as np
        a = (np.asarray(sq, dtype=np.float32) / 255.0 - 0.5) / 0.5
        return a[:, :, ::-1].transpose(2, 0, 1).copy()      # HWC RGB -> CHW BGR, as the model was trained

    def tag(self, files: list[str]) -> list[dict]:
        import numpy as np
        x = self.torch.from_numpy(np.stack([self._prep(f) for f in files])).to(self.dev, self.dtype)
        with self.torch.inference_mode():
            probs = self.torch.sigmoid(self.model(x).float()).cpu().numpy()
        out = []
        for row in probs:
            tags = {}
            for i in np.argsort(-row):
                p = float(row[i])
                if p < self.general:
                    break
                c = self.cats[i]
                if c == 0 or (c == 4 and p >= self.character):
                    tags[self.names[i]] = p
            out.append(tags)
        return out


def pretty(tag: str) -> str:
    """'long_hair' -> 'long hair'; short kaomoji like '^_^' keep their underscores."""
    return tag.replace("_", " ") if len(tag) > 3 else tag


def get(key: str, settings, log=print):
    """The tagger `key` ('hydra' | 'wd'), loading it on first use."""
    path = models.find_hydra(settings) if key == "hydra" else models.find_wd(settings)
    if not path:
        raise FileNotFoundError(f"{models.MODELS[key]['name']} isn't downloaded. Set its folder or download it in "
                                "Preferences → Models.")
    thr = float(getattr(settings, "tag_threshold", 0) or 0)
    with _lock:
        if _loaded.get("key") == (key, path):          # a new threshold doesn't need a 1 GB reload
            if _loaded.get("thr") != thr:
                _loaded["tagger"].set_threshold(thr)
                _loaded["thr"] = thr
            return _loaded["tagger"]
        unload()
        log(f"Loading {models.MODELS[key]['name']} from {path}…")
        t = HydraTagger(path, thr) if key == "hydra" else WDTagger(path, thr)
        _loaded.update(key=(key, path), thr=thr, tagger=t)
        return t


def unload() -> None:
    if _loaded:
        _loaded.clear()
        try:
            import gc

            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def tag_files(key: str, settings, files: list[str], ctx) -> dict[str, dict]:
    """{path: {tag: prob}} for every readable file; unreadable ones are logged and skipped."""
    t = get(key, settings, ctx.log)
    out = {}
    for i in range(0, len(files), t.batch):
        if ctx.cancelled:
            break
        chunk = files[i:i + t.batch]
        try:
            res = t.tag(chunk)
        except Exception:                 # one bad file shouldn't sink the batch: retry singly
            res = []
            for f in chunk:
                try:
                    res += t.tag([f])
                except Exception as e:
                    ctx.log(f"  {os.path.basename(f)}: {e}")
                    res.append(None)
        for f, r in zip(chunk, res):
            if r is not None:
                out[f] = {pretty(k): v for k, v in r.items()}
        ctx.progress(min(i + t.batch, len(files)), len(files), f"Tagged {min(i + t.batch, len(files))} / {len(files)}")
    return out
