"""Caption-sidecar editing for the Tag Editor: load, filter, batch edit, save. Pure Python, no GTK."""
from __future__ import annotations

import os
from collections import Counter

from .captions import split_tags
from .utils import IMAGE_EXTS


def sidecar(image: str) -> str:
    return os.path.splitext(image)[0] + ".txt"


def list_images(folder: str, recursive: bool = True) -> list[str]:
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        out += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(IMAGE_EXTS)]
        if not recursive:
            break
    return out


def read_tags(image: str) -> list[str]:
    try:
        with open(sidecar(image), encoding="utf-8", errors="replace") as f:
            return split_tags(f.read())
    except OSError:
        return []


def write_tags(image: str, tags: list[str]) -> None:
    """Writes the .txt sidecar; an empty tag list removes it (undo of a first caption leaves no empty file)."""
    path = sidecar(image)
    if not tags:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(", ".join(tags))
    os.replace(tmp, path)


def load(folder: str, recursive: bool = True) -> dict[str, list[str]]:
    return {p: read_tags(p) for p in list_images(folder, recursive)}


# ---- filtering ----
def _term_hit(term: str, low: list[str]) -> bool:
    if term.startswith("*") and term.endswith("*") and len(term) > 2:
        return any(term[1:-1] in t for t in low)
    if term.endswith("*"):
        return any(t.startswith(term[:-1]) for t in low)
    return term in low


def matches(tags: list[str], query: str) -> bool:
    """query: comma-separated terms. `-tag` excludes, `tag*` is a prefix, `*part*` a substring, `:untagged` = empty."""
    low = [t.lower() for t in tags]
    for raw in query.split(","):
        term = raw.strip().lower()
        if not term:
            continue
        neg = term.startswith("-")
        term = term[1:].strip() if neg else term
        hit = (not low) if term == ":untagged" else _term_hit(term, low)
        if hit == neg:
            return False
    return True


def counts(captions: dict[str, list[str]], files) -> Counter:
    c = Counter()
    for f in files:
        c.update(captions.get(f, ()))
    return c


# ---- edits; each returns the new tag list ----
def _has(tags, tag) -> bool:
    return tag.lower() in {t.lower() for t in tags}


def add(tags: list[str], new: list[str], at_start: bool = False) -> list[str]:
    extra = [t for t in new if not _has(tags, t)]
    return extra + tags if at_start else tags + extra


def remove(tags: list[str], drop: list[str]) -> list[str]:
    gone = {t.lower() for t in drop}
    return [t for t in tags if t.lower() not in gone]


def replace(tags: list[str], find: str, repl: str) -> list[str]:
    """Whole-tag replace (case-insensitive); an empty replacement removes the tag."""
    f = find.strip().lower()
    out = [repl.strip() if t.lower() == f else t for t in tags]
    return split_tags(", ".join(t for t in out if t))


def merge(old: list[str], new: list[str], mode: str) -> list[str] | None:
    """mode: merge | prepend | replace | skip. None = leave the file alone."""
    if mode == "skip" and old:
        return None
    if mode == "replace" or not old:
        return list(new)
    return add(new, old) if mode == "prepend" else add(old, new)


if __name__ == "__main__":
    t = ["1girl", "Solo", "long hair"]
    assert matches(t, "solo, -boy") and not matches(t, "-solo") and matches(t, "long*") and matches(t, "*hai*")
    assert matches([], ":untagged") and not matches(t, ":untagged") and matches(t, "-:untagged")
    assert add(t, ["solo", "smile"]) == ["1girl", "Solo", "long hair", "smile"]
    assert add(t, ["smile"], at_start=True)[0] == "smile"
    assert remove(t, ["SOLO"]) == ["1girl", "long hair"]
    assert replace(t, "long hair", "short hair") == ["1girl", "Solo", "short hair"]
    assert replace(t, "solo", "") == ["1girl", "long hair"]
    assert merge(t, ["a"], "skip") is None and merge([], ["a"], "skip") == ["a"]
    assert merge(["x"], ["a", "x"], "prepend") == ["a", "x"] and merge(["x"], ["a"], "merge") == ["x", "a"]
    print("tagedit ok")
