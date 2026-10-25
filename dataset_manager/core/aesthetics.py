"""Low-quality detection: a size rule plus an optional aesthetic classifier."""
from __future__ import annotations

import logging

from PIL import Image

from . import capabilities

log = logging.getLogger(__name__)
MIN_GOOD_SIDE = 400


class QualityDetector:
    def __init__(self):
        self.pipe, self.broken = None, False

    @property
    def model_available(self) -> bool:
        return capabilities.has("transformers") and not self.broken

    def _load(self) -> bool:
        if self.pipe is None and self.model_available:
            try:
                import torch
                from transformers import pipeline
                dev = 0 if (capabilities.has("torch") and torch.cuda.is_available()) else -1
                self.pipe = pipeline("image-classification", model="cafeai/cafe_aesthetic", device=dev)
            except Exception as e:
                log.warning("Could not load the aesthetic model: %s", e)
                self.broken = True
        return self.pipe is not None

    def is_low_quality(self, img: Image.Image, strictness: float = 0.45) -> bool:
        w, h = img.size
        if w < MIN_GOOD_SIDE or h < MIN_GOOD_SIDE:
            return True
        if not self._load():
            return False
        scores = {r["label"]: r["score"] for r in self.pipe(img, top_k=2)}
        if "not_aesthetic" in scores:
            return scores["not_aesthetic"] > strictness
        if "aesthetic" in scores:
            return scores["aesthetic"] < (1.0 - strictness)
        return False


detector = QualityDetector()
