"""Local text model (any instruct GGUF via llama.cpp; tuned on Qwen2.5 1.5B): rewrite one caption — split it into paragraphs, or tidy away redundancy.

Text only; the model never sees the image. Nothing here saves a caption: the Tag Editor stages the result and the
user still presses Save Tags / Save All Tags.
"""
from __future__ import annotations

import logging
import re
import threading

from . import capabilities, models

# Splitting into paragraphs never lets the model reword anything (small models can't resist): the caption is cut
# into numbered sentences in code, the model only labels each one (an easy job even for a 1.5B model), and the
# original sentences are regrouped subject → setting → style, keeping their order within each paragraph.
# A tag list or a single sentence has nothing to split and comes back as it was.
LABELS = ("subject", "setting", "style")
PARAGRAPHS = ("You sort the numbered sentences of an image caption. Label every sentence with exactly one word:\n"
              "subject - the main people, animals or objects: how they look, what they wear, hold or do\n"
              "setting - the place, the surroundings, the background, other things around the subject\n"
              "style - lighting, colours of the whole image, camera, photo or art style, mood\n"
              "Reply with one line per sentence: the number, a colon, the label.")
PARAGRAPHS_EXAMPLE = ("1. A young woman with long red hair stands in a field.\n2. She wears a white dress and a straw hat."
                      "\n3. She holds a bunch of sunflowers.\n4. Behind her, a wooden fence runs along a dirt road."
                      "\n5. Mountains rise in the distance.\n6. The image is a soft, warm photograph with film grain.",
                      "1: subject\n2: subject\n3: subject\n4: setting\n5: setting\n6: style")

TIDY = ("You tidy image captions. Rewrite the caption as one tidy paragraph: remove repeated words, repeated mentions "
        "of the same thing and chains of 'and'. Keep every distinct detail exactly as stated: every object, person, "
        "animal, colour, number, position and action, attached to the same thing it was attached to. Never add "
        "anything that is not in the caption: no new objects, no opinions, no mood words. Reply with the tidy "
        "caption only. A caption with nothing repeated stays almost exactly as it is.")
TIDY_EXAMPLES = (
    ("A man is standing on a beach, and the man is wearing a red shirt, and the man has a red shirt with white "
     "stripes. The beach is sandy. There is a dog next to the man, a small brown dog, and the dog is wet. Two boats "
     "are on the sea behind him.",
     "A man in a red shirt with white stripes stands on a sandy beach next to a small, wet brown dog. Two boats are "
     "on the sea behind him."),
    ("Three apples are on a table. The apples are red, three red apples, and one green pear is next to them. The "
     "table is wooden and the table is old. A lamp is on the left.",
     "Three red apples and one green pear sit on an old wooden table. A lamp is on the left."),
    ("A grey rabbit sits in a box. The rabbit has long ears. The box is on a porch next to a garden. The light is "
     "soft.",
     "A grey rabbit with long ears sits in a box. The box is on a porch next to a garden. The light is soft."),
)

TASKS = ("paragraphs", "tidy")

_SENTENCE = re.compile(r"(?<=[.!?;])\s+|(?<=[.!?;][\"'”’)\]])\s+")
_TAGLIKE = re.compile(r"^[^.!?;\n]*$")

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
    path = models.find_llm(settings)
    if not path:
        raise RuntimeError("No text model is downloaded: Preferences → Models → Text model.")
    if _llm is not None and _llm_path == path:
        return _llm
    from llama_cpp import Llama
    unload()
    if note := capabilities.llama_gpu_note():
        logging.getLogger(__name__).warning(note)
    _llm = Llama(model_path=path, n_ctx=4096, n_gpu_layers=-1, verbose=False)
    _llm_path = path
    return _llm


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text or "", flags=re.S).strip()   # Qwen3-style reasoning
    text = re.sub(r"^```[\w-]*\n?|\n?```$", "", text).strip()
    text = re.sub(r"^(?:caption|result|output)\s*:\s*", "", text, flags=re.I)
    if len(text) > 1 and text[0] in "\"'“" and text[-1] in "\"'”":
        text = text[1:-1].strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE.split(" ".join(text.split())) if p.strip()]


def regroup(parts: list[str], reply: str) -> list[str]:
    """Paragraphs from the model's "n: label" lines; an unlabelled sentence goes with the one before it."""
    got = {}
    for m in re.finditer(r"(\d+)\W*(" + "|".join(LABELS) + ")", reply, re.I):
        got.setdefault(int(m.group(1)) - 1, LABELS.index(m.group(2).lower()))
    groups, label = {}, 0
    for i, part in enumerate(parts):
        label = got.get(i, label)
        part = re.sub(r";$", ".", part)
        groups.setdefault(label, []).append(part[:1].upper() + part[1:])
    return [" ".join(groups[k]) for k in sorted(groups)]


def _chat(settings, messages: list, ctx, max_tokens: int) -> str:
    if ctx is not None:
        ctx.log("Loading the text model…" if _llm is None else "Thinking…")
    with _lock:                            # one model, one request at a time
        llm = _load(settings)
        if "qwen3" in _llm_path.rsplit("/", 1)[-1].lower():         # Qwen3 thinks out loud unless told not to
            messages[-1] = {**messages[-1], "content": messages[-1]["content"] + " /no_think"}
        out = llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=0.0, seed=0)
    return _clean(out["choices"][0]["message"]["content"])


def rewrite(settings, text: str, task: str, ctx=None) -> str:
    """task: 'paragraphs' | 'tidy'. Returns the edited caption ('' only for an empty one).

    A tag list (no sentences) comes back unchanged: Batch Edit → Tidy is the tool for those.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}")
    if _TAGLIKE.match(text):
        return text
    if task == "paragraphs":
        parts = sentences(text)
        if len(parts) < 2:
            return text
        numbered = "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))
        paras = regroup(parts, _chat(settings, [{"role": "system", "content": PARAGRAPHS},
                                 {"role": "user", "content": PARAGRAPHS_EXAMPLE[0]},
                                 {"role": "assistant", "content": PARAGRAPHS_EXAMPLE[1]},
                                 {"role": "user", "content": numbered}], ctx, 16 + 8 * len(parts)))
        return "\n\n".join(paras) if len(paras) > 1 else text
    flat = " ".join(text.split())
    shots = [{"role": r, "content": c} for q, a in TIDY_EXAMPLES for r, c in (("user", q), ("assistant", a))]
    return " ".join(_chat(settings, [{"role": "system", "content": TIDY}, *shots, {"role": "user", "content": flat}],
                          ctx, 1024).split())


if __name__ == "__main__":
    assert _clean("```\nCaption: \"a, b\"\n```") == "a, b"
    assert _clean("<think>hmm</think>\nshort") == "short"
    assert _clean("  one\n\n\n\ntwo  ") == "one\n\ntwo"
    assert rewrite(None, "", "tidy") == ""
    assert rewrite(None, "cat, solo, orange fur", "paragraphs") == "cat, solo, orange fur"
    assert rewrite(None, "One cat sits.", "paragraphs") == "One cat sits."
    assert sentences("A cat sits. It is \"grey.\" A dog; a bird!") == ["A cat sits.", "It is \"grey.\"", "A dog;", "a bird!"]
    assert regroup(["A.", "B;", "c.", "D."], "1: subject\n2: Setting\n3 - subject\n") == ["A. C. D.", "B."]
    assert regroup(["A.", "B."], "nonsense") == ["A. B."]
    print("textllm ok")
