"""Aesthetic score filter: keeps images the model rates at or above a threshold (aesthetic-predictor-v2-5, 1–10).

Separate from `aesthetics.py` (the old, coarse cafe_aesthetic low-quality check): this one is a straight score that
the user sets per session. The model is ~1.6 GB of torch weights, loaded once per run.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import capabilities

MODEL = "aesthetic-predictor-v2-5"
NOTE = "scores 1–10; 5.5+ is a good photo, 6.5+ is striking"


@dataclass
class AestheticSpec:
    enabled: bool = False
    threshold: float = 5.5


class AestheticFilter:
    def __init__(self, spec: AestheticSpec, ctx=None):
        self.spec = spec
        self.ctx = ctx
        self.model = self.processor = None
        self.broken = False
        self._scored = 0

    @staticmethod
    def available() -> bool:
        return capabilities.has("aesthetic_predictor_v2_5")

    def _log(self, msg: str) -> None:
        if self.ctx is not None:
            self.ctx.log(msg)

    def _load(self) -> bool:
        if self.model is not None:
            return True
        if self.broken:
            return False
        try:
            self._log("Loading the aesthetic model (first run downloads ~1.6 GB)…")
            from aesthetic_predictor_v2_5 import convert_v2_5_from_siglip
            self.model, self.processor = convert_v2_5_from_siglip(low_cpu_mem_usage=True, trust_remote_code=True)
            self.model.eval()
            self._log("Aesthetic model ready.")
            return True
        except Exception as exc:
            self.broken = True
            self._log(f"Aesthetic filter off: {str(exc).splitlines()[0]}")
            return False

    def score(self, img) -> float:
        px = self.processor(images=img, return_tensors="pt").pixel_values
        out = self.model(px)
        logits = out.logits if hasattr(out, "logits") else out
        return float(logits.squeeze().float().cpu().numpy())

    def keep(self, img) -> bool:
        """True = keep. Keeps everything (and says why once) if the model cannot be loaded."""
        if not self._load():
            self.broken = True
            return True
        try:
            value = self.score(img)
        except Exception as exc:
            self.broken = True
            self._log(f"Aesthetic filter off: {str(exc).splitlines()[0]}")
            return True
        self._scored += 1
        if self.ctx is not None and self._scored % 20 == 1:
            self._log(f"Aesthetic: {value:.2f} (keep ≥ {self.spec.threshold:g})")
        return value >= self.spec.threshold
