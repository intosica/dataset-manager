"""Auto-taggers: JTP Hydra 3.5 (e621), WD EVA02 Large v3 (Danbooru), and Florence-2.

Hydra and WD run with torch on the GPU when there is one. Florence-2 uses the ComfyUI model folders as-is.
A loaded tagger stays in memory until another one is chosen or `unload()` is called.
"""
from __future__ import annotations

import csv
import os
import re
import threading

from . import models

_lock = threading.Lock()
_loaded: dict = {}          # {"key": ..., "path": ..., "tagger": ...}

FLORENCE_TASKS = [
    ("dense", "<DENSE_REGION_CAPTION>", "Dense region captions"),
    ("detailed", "<DETAILED_CAPTION>", "Detailed caption"),
    ("more", "<MORE_DETAILED_CAPTION>", "More detailed caption"),
    ("ocr", "<OCR>", "OCR (text in the image)"),
]
FLORENCE_PROMPTS = {key: prompt for key, prompt, _label in FLORENCE_TASKS}
FLORENCE_LABELS = {key: label for key, _prompt, label in FLORENCE_TASKS}


def _caption_tags(text: str) -> list[str]:
    text = re.sub(r"<[^>]+>", " ", text or "")
    out, seen = [], set()
    for part in re.split(r"[,;\n]+", text):
        part = part.strip().strip(".\"' ")
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            out.append(part)
    return out[:256]


def _florence_text(parsed, prompt: str) -> str:
    value = parsed.get(prompt, parsed) if isinstance(parsed, dict) else parsed
    if isinstance(value, dict):
        if "labels" in value:
            return ", ".join(str(x) for x in value["labels"])
        if "text" in value:
            t = value["text"]
            return ", ".join(str(x) for x in t) if isinstance(t, (list, tuple)) else str(t)
        return ""
    return str(value or "")


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


class FlorenceTagger:
    """Florence-2 from a local ComfyUI models/LLM folder (trust_remote_code)."""
    batch = 1

    def __init__(self, path: str, task: str = "more"):
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoProcessor
        if not florence_transformers_ok(transformers.__version__):
            raise RuntimeError(
                f"Florence-2 needs transformers 4.38–4.46, but {transformers.__version__} is installed. "
                "Run: pip install 'transformers>=4.38,<4.47' (or reinstall it from Preferences → System)."
            )
        self.torch = torch
        self.dev = _device()
        self.dtype = torch.float16 if self.dev.type == "cuda" else torch.float32
        self.processor = AutoProcessor.from_pretrained(path, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            path, trust_remote_code=True, torch_dtype=self.dtype, attn_implementation="eager"
        ).to(self.dev).eval()
        self.set_task(task)

    def set_threshold(self, threshold: float) -> None:
        pass                      # Florence has no probability threshold

    def set_task(self, task: str) -> None:
        self.task = task if task in FLORENCE_PROMPTS else "more"
        self.prompt = FLORENCE_PROMPTS[self.task]

    def tag(self, files: list[str]) -> list[dict]:
        from PIL import Image
        out = []
        for f in files:
            with Image.open(f) as im:
                img = im.convert("RGB")
            inputs = self.processor(images=img, text=self.prompt, return_tensors="pt")
            inputs = {k: (v.to(self.dev, dtype=self.dtype) if self.torch.is_floating_point(v) else v.to(self.dev))
                      for k, v in inputs.items()}
            with self.torch.inference_mode():
                ids = self.model.generate(
                    input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                    max_new_tokens=1024, num_beams=3, do_sample=False, early_stopping=False,
                )
            text = self.processor.batch_decode(ids, skip_special_tokens=False)[0]
            parsed = self.processor.post_process_generation(text, task=self.prompt, image_size=img.size)
            text = _florence_text(parsed, self.prompt)
            # Dense labels are tags; the caption/OCR tasks produce prose, so keep each as one caption.
            tags = _caption_tags(text) if self.task == "dense" else ([text] if text else [])
            out.append({t: 1.0 for t in tags})
        return out


def florence_transformers_ok(version: str) -> bool:
    """Florence-2's remote code uses the legacy KV-cache API, removed in transformers 4.47."""
    try:
        parts = tuple(int(x) for x in version.split(".")[:2])
    except ValueError:
        return True
    return (4, 38) <= parts < (4, 47)


def pretty(tag: str) -> str:
    """'long_hair' -> 'long hair'; short kaomoji like '^_^' keep their underscores."""
    return tag.replace("_", " ") if len(tag) > 3 else tag


def get(key: str, settings, log=print):
    """The tagger `key` ('hydra' | 'wd' | 'florence:<folder>'), loading it on first use."""
    if key.startswith("florence:"):
        path = key.split(":", 1)[1]
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Florence model folder not found: {path}. Pick another model or set the folder in "
                                    "Preferences → Models.")
        label = os.path.basename(path)
    else:
        path = models.find_hydra(settings) if key == "hydra" else models.find_wd(settings)
        if not path:
            raise FileNotFoundError(f"{models.MODELS[key]['name']} isn't downloaded. Set its folder or download it in "
                                    "Preferences → Models.")
        label = models.MODELS[key]["name"]
    thr = float(getattr(settings, "tag_threshold", 0) or 0)
    task = getattr(settings, "florence_task", "more")
    with _lock:
        if _loaded.get("key") == (key, path):          # a new threshold/task doesn't need a full reload
            if key.startswith("florence:"):
                _loaded["tagger"].set_task(task)
            elif _loaded.get("thr") != thr:
                _loaded["tagger"].set_threshold(thr)
                _loaded["thr"] = thr
            return _loaded["tagger"]
        unload()
        log(f"Loading {label} from {path}…")
        if key.startswith("florence:"):
            t = FlorenceTagger(path, task)
        elif key == "hydra":
            t = HydraTagger(path, thr)
        else:
            t = WDTagger(path, thr)
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
