"""Optional packages. Detected without importing them, so start-up stays instant."""
from __future__ import annotations

import importlib.util

# module -> what it enables, the pip packages that provide it, optional post-install command
FEATURES = {
    "playwright": {"what": "Image search collection (browser automation)",
                   "pip": ["playwright"], "post": "playwright install chromium"},
    "playwright_stealth": {"what": "Anti-bot hardening for searches (optional)",
                           "pip": ["playwright-stealth"]},
    "requests": {"what": "ArtofMTG source (HTTP)", "pip": ["requests"]},
    "bs4": {"what": "ArtofMTG source (HTML parsing)", "pip": ["beautifulsoup4"]},
    "torch": {"what": "AI upscaling, GPU support", "pip": ["torch"]},
    "spandrel": {"what": "AI upscaling models", "pip": ["spandrel"]},
    "transformers": {"what": "Low-quality detection, TinyCLIP similarity", "pip": ["transformers"]},
    "onnxruntime": {"what": "Similarity filter: imgbeddings backend",
                    "pip": ["onnxruntime", "transformers"]},
    "imageprism": {"what": "Similarity filter: imageprism backends", "pip": ["imageprism"]},
    "open_clip": {"what": "Similarity filter: OpenCLIP backend", "pip": ["open_clip_torch"]},
    "torchvision": {"what": "Similarity filter: MobileNet backend", "pip": ["torchvision"]},
    "aesthetic_predictor_v2_5": {"what": "Aesthetic score filter (1–10) while collecting",
                                 "pip": ["aesthetic-predictor-v2-5"]},
    "einops": {"what": "Auto-tagging: JTP Hydra 3.5", "pip": ["einops", "safetensors", "torch"]},
    "timm": {"what": "Auto-tagging: WD EVA02 large v3", "pip": ["timm", "safetensors", "torch"]},
    "llama_cpp": {"what": "JoyCaption from .gguf files (llama.cpp, CPU build)",
                  "pip": ["llama-cpp-python"],
                  "extra_index": "https://abetlen.github.io/llama-cpp-python/whl/cpu"},
    "accelerate": {"what": "JoyCaption from Hugging Face weights (GPU)",
                   "pip": ["accelerate", "transformers", "torch"]},
    "bitsandbytes": {"what": "JoyCaption 4-bit mode (fits 8–12 GB GPUs)", "pip": ["bitsandbytes"]},
}


def refresh() -> None:
    """Call after installing packages so new modules are found without a restart."""
    importlib.invalidate_caches()
    _found.clear()


_found: dict[str, bool] = {}


def has(module: str) -> bool:
    """Cached: find_spec scans every sys.path entry, and the UI asks often."""
    if module not in _found:
        try:
            _found[module] = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            _found[module] = False
    return _found[module]


def report() -> list[dict]:
    return [{"module": mod, "ok": has(mod), **info} for mod, info in FEATURES.items()]


def pip_hint(modules) -> str:
    pkgs = [p for m in modules for p in FEATURES[m]["pip"]]
    return "pip install " + " ".join(dict.fromkeys(pkgs))
