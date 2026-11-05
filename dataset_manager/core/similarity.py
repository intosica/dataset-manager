"""Keep only scraped images that look like your examples: image embeddings + cosine similarity.

score = mean cosine similarity to the positive examples − mean similarity to the negative examples (0 if none).
An image is kept when score >= threshold. Example embeddings are cached on disk per backend.
"""
from __future__ import annotations

import hashlib
import os
import pickle
from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import paths
from .tasks import TaskContext
from .utils import iter_images

# key -> (label, required modules, install command)
BACKENDS = {
    "imgbeddings": ("imgbeddings — CLIP ViT-B/32 ONNX, ~88 MB, no PyTorch", ["onnxruntime", "transformers"],
                    "pip install onnxruntime transformers"),
    "tinyclip": ("TinyCLIP ViT-8M — smallest / fastest", ["torch", "transformers"], "pip install torch transformers"),
    "imageprism:semantic": ("imageprism — semantic (CLIP), ONNX", ["imageprism"], "pip install imageprism"),
    "imageprism:style": ("imageprism — visual style (MobileNetV2), ONNX", ["imageprism"], "pip install imageprism"),
    "imageprism:instance": ("imageprism — same object (DINOv2), ONNX", ["imageprism"], "pip install imageprism"),
    "openclip": ("OpenCLIP ViT-B/32 — best quality", ["torch", "open_clip"], "pip install torch open_clip_torch"),
    "mobilenet": ("MobileNetV3 — pure visual style, tiny", ["torch", "torchvision"], "pip install torch torchvision"),
}
# Combo-row sized names: the full labels above are too long for a row and get ellipsized.
SHORT = {
    "imgbeddings": "imgbeddings (88 MB)",
    "tinyclip": "TinyCLIP ViT-8M",
    "imageprism:semantic": "imageprism · semantic",
    "imageprism:style": "imageprism · style",
    "imageprism:instance": "imageprism · object",
    "openclip": "OpenCLIP ViT-B/32",
    "mobilenet": "MobileNetV3",
}
TINYCLIP_ID = "wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M"


@dataclass
class SimilaritySpec:
    backend: str = ""             # "" = off
    positive_dir: str = ""
    negative_dir: str = ""
    threshold: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.backend)


def _embedder(backend: str):
    """Returns a function PIL.Image -> 1-D numpy vector. Imports happen here so the app starts without them."""
    if backend == "imgbeddings":
        # imgbeddings' ONNX model, run directly: the imgbeddings package itself no longer works with current
        # huggingface_hub / transformers releases.
        from huggingface_hub import hf_hub_download
        from onnxruntime import InferenceSession
        from transformers import CLIPImageProcessor
        sess = InferenceSession(hf_hub_download("minimaxir/imgbeddings", "patch32_v1.onnx"),
                                providers=["CPUExecutionProvider"])
        proc = CLIPImageProcessor.from_pretrained("openai/clip-vit-base-patch32", do_center_crop=False)

        def ibed(img):
            side = max(img.size)                           # pad to square like imgbeddings does
            sq = Image.new("RGB", (side, side))
            sq.paste(img.convert("RGB"), ((side - img.width) // 2, (side - img.height) // 2))
            return sess.run(["embeddings"], dict(proc(images=[sq], return_tensors="np")))[0][0]
        return ibed
    if backend.startswith("imageprism:"):
        from imageprism import ImagePrism
        dim = backend.split(":", 1)[1]
        prism = ImagePrism(dimensions=[dim])
        return lambda img: np.asarray(prism.embed(img)[dim]).ravel()
    import torch
    if backend == "tinyclip":
        from transformers import CLIPModel, CLIPProcessor
        model, proc = CLIPModel.from_pretrained(TINYCLIP_ID).eval(), CLIPProcessor.from_pretrained(TINYCLIP_ID)

        def tiny(img):
            out = model.get_image_features(**proc(images=img, return_tensors="pt"))
            return (out if torch.is_tensor(out) else out.pooler_output)[0]
        fn = tiny
    elif backend == "openclip":
        import open_clip
        model, _, prep = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
        model.eval()
        fn = lambda img: model.encode_image(prep(img).unsqueeze(0))[0]
    elif backend == "mobilenet":
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small
        weights = MobileNet_V3_Small_Weights.DEFAULT
        net, prep = mobilenet_v3_small(weights=weights).eval(), weights.transforms()
        fn = lambda img: net.avgpool(net.features(prep(img).unsqueeze(0))).flatten()
    else:
        raise ValueError(f"unknown similarity backend: {backend}")

    def run(img):
        with torch.no_grad():
            return fn(img).float().cpu().numpy()
    return run


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).ravel()
    return v / (np.linalg.norm(v) or 1.0)


class SimilarityFilter:
    def __init__(self, spec: SimilaritySpec, ctx: TaskContext):
        if spec.backend not in BACKENDS:
            raise ValueError(f"unknown backend {spec.backend!r}")
        label, mods, install = BACKENDS[spec.backend]
        from . import capabilities
        missing = [m for m in mods if not capabilities.has(m)]
        if missing:
            raise RuntimeError(f"{label.split(' — ')[0]} needs: {install}")
        self.spec, self.ctx = spec, ctx
        ctx.log(f"Similarity filter: loading {label.split(' — ')[0]} (first use downloads the model)...")
        self.embed = _embedder(spec.backend)
        self.pos = self._examples(spec.positive_dir)
        self.neg = self._examples(spec.negative_dir)
        if not len(self.pos) and not len(self.neg):
            raise RuntimeError("no example images found in the positive / negative folders")
        ctx.log(f"Similarity filter ready: {len(self.pos)} positive, {len(self.neg)} negative examples, "
                f"threshold {spec.threshold:+.2f}.")

    def _vec(self, img: Image.Image) -> np.ndarray:
        img = img.convert("RGB")
        img.thumbnail((448, 448))         # models resize to ~224 anyway; this keeps big downloads fast
        return _unit(self.embed(img))

    def _examples(self, folder: str) -> np.ndarray:
        folder = paths.resolve(folder)
        if not folder or not os.path.isdir(folder):
            return np.zeros((0, 1), np.float32)
        cache_file = paths.cache_dir() / f"similarity-{self.spec.backend.replace(':', '-')}.pkl"
        try:
            with open(cache_file, "rb") as f:
                cache = pickle.load(f)
        except Exception:                 # missing or corrupt: start over
            cache = {}
        vecs, dirty = [], False
        files = sorted(iter_images(folder))
        for i, p in enumerate(files):
            if self.ctx.cancelled:
                break
            try:
                st = os.stat(p)
            except OSError:
                continue
            key = hashlib.md5(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
            if key not in cache:
                self.ctx.progress(i, len(files), f"Embedding examples: {os.path.basename(folder)}")
                try:
                    with Image.open(p) as im:
                        cache[key] = self._vec(im)
                    dirty = True
                except Exception:
                    continue
            vecs.append(cache[key])
        if dirty:
            tmp = cache_file.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                pickle.dump(cache, f)
            os.replace(tmp, cache_file)
        return np.stack(vecs) if vecs else np.zeros((0, 1), np.float32)

    def score(self, img: Image.Image) -> float:
        v = self._vec(img)
        pos = float((self.pos @ v).mean()) if len(self.pos) else 0.0
        neg = float((self.neg @ v).mean()) if len(self.neg) else 0.0
        return pos - neg

    def keep(self, img: Image.Image) -> bool:
        return self.score(img) >= self.spec.threshold


if __name__ == "__main__":       # self-check with a fake embedder: colour histogram stand-in
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(f"{d}/pos"); os.makedirs(f"{d}/neg")
        Image.new("RGB", (64, 64), "red").save(f"{d}/pos/a.png")
        Image.new("RGB", (64, 64), "blue").save(f"{d}/neg/b.png")
        f = SimilarityFilter.__new__(SimilarityFilter)
        f.spec, f.ctx = SimilaritySpec("test", f"{d}/pos", f"{d}/neg", 0.0), TaskContext()
        f.embed = lambda im: np.asarray(im.resize((1, 1))).ravel().astype(float) + 1
        BACKENDS["test"] = ("t", [], "")
        f.pos, f.neg = f._examples(f.spec.positive_dir), f._examples(f.spec.negative_dir)
        assert f.keep(Image.new("RGB", (32, 32), (250, 10, 10)))
        assert not f.keep(Image.new("RGB", (32, 32), (10, 10, 250)))
        os.remove(paths.cache_dir() / "similarity-test.pkl")
    print("ok")
