"""Phi-4 mini (GGUF via llama.cpp): rewrite one caption — split it into paragraphs, or tidy away redundancy.

Text only; the model never sees the image. Nothing here saves a caption: the Tag Editor stages the result and the
user still presses Save Tags / Save All Tags.
"""
from __future__ import annotations

import re
import threading

from . import capabilities, models

SYSTEM = ("You edit captions for image datasets. Reply with the edited caption only: no introduction, no quotes, "
          "no markdown, no explanation.")

TASKS = {
    "paragraphs": ("Split this caption into short paragraphs. Start a new paragraph whenever the subject, setting, "
                   "or action changes. Keep every detail and the original wording wherever possible. Do not add, "
                   "remove, explain, or translate anything. Separate the paragraphs with a blank line."),
    "tidy": ("Rewrite this caption as one tidy paragraph. Remove repeated words, repeated mentions of the same "
             "thing, and filler connectives such as chains of 'and', but keep every distinct detail and the original "
             "meaning. Do not add anything new, do not explain, and do not turn it into a list."),
}

_lock = threading.Lock()
_llm = None
_llm_path = ""


def available() -> bool:
    return capabilities.has("llama_cpp")


def current_model() -> str:
    return _llm_path


def unload() -> None:
    global _llm, _llm_path
    _llm = None
    _llm_path = ""


def _load(settings):
    global _llm, _llm_path
    path = models.find_phi(settings)
    if not path:
        raise RuntimeError("Phi-4 mini is not downloaded: Preferences → Models → Phi-4 mini.")
    if _llm is not None and _llm_path == path:
        return _llm
    from llama_cpp import Llama
    unload()
    _llm = Llama(model_path=path, n_ctx=4096, n_gpu_layers=-1, verbose=False)
    _llm_path = path
    return _llm


def _clean(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[\w-]*\n?|\n?```$", "", text).strip()
    text = re.sub(r"^(?:caption|result|output)\s*:\s*", "", text, flags=re.I)
    if len(text) > 1 and text[0] in "\"'“" and text[-1] in "\"'”":
        text = text[1:-1].strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def rewrite(settings, text: str, task: str, ctx=None) -> str:
    """task: 'paragraphs' | 'tidy'. Returns the edited caption ('' when there was nothing to do)."""
    text = (text or "").strip()
    if not text:
        return ""
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}")
    if ctx is not None:
        ctx.log("Loading Phi-4 mini…" if _llm is None else "Thinking…")
    with _lock:                            # one model, one request at a time
        llm = _load(settings)
        out = llm.create_chat_completion(
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"{TASKS[task]}\n\nCaption:\n{text}"}],
            max_tokens=1024, temperature=0.2)
    return _clean(out["choices"][0]["message"]["content"])


if __name__ == "__main__":
    assert _clean("```\nCaption: \"a, b\"\n```") == "a, b"
    assert _clean("  one\n\n\n\ntwo  ") == "one\n\ntwo"
    assert rewrite(None, "", "tidy") == ""
    print("textllm ok")
