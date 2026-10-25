"""Optional packages. Detected without importing them, so start-up stays instant."""
from __future__ import annotations

import importlib.util

# module -> (what it enables, install command)
FEATURES = {
    "playwright": ("Image search collection (browser automation)", "pip install playwright && playwright install chromium"),
    "playwright_stealth": ("Anti-bot hardening for searches (optional)", "pip install playwright-stealth"),
    "requests": ("ArtofMTG source", "pip install requests"),
    "bs4": ("ArtofMTG source", "pip install beautifulsoup4"),
    "torch": ("AI upscaling, GPU support", "pip install torch"),
    "spandrel": ("AI upscaling models", "pip install spandrel"),
    "transformers": ("Low-quality (aesthetic) detection", "pip install transformers"),
}


def has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def report() -> list[tuple[str, bool, str, str]]:
    return [(mod, has(mod), what, cmd) for mod, (what, cmd) in FEATURES.items()]
