"""
Duplicate detection.

* exact copies      -> MD5 of the file bytes
* near duplicates   -> 256-bit difference hash; survives re-encoding, resizing and upscaling
* fast lookup       -> multi-index hashing, so large libraries don't need an O(n²) scan
* greedy matching   -> each removable image is within the threshold of an image that is KEPT,
                       so runs of similar frames can't chain together
* hash cache        -> sqlite, keyed by path + size + mtime, so re-scans are fast
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import sqlite3
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import paths
from .tasks import TaskContext
from .utils import LOW_QUALITY_DIRS, has_part, iter_images, sidecars

HASH_SIZE = 16
HASH_BITS = HASH_SIZE * HASH_SIZE
FLAT_STD = 2.0          # images flatter than this are matched by exact copy only
ASPECT_TOL = 0.10
popcount = int.bit_count if hasattr(int, "bit_count") else (lambda x: bin(x).count("1"))


def perceptual_hash(img: Image.Image):
    g = img.convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
    a = np.asarray(g, dtype=np.int16)
    if a.std() < FLAT_STD:
        return None
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def analyze_bytes(data: bytes) -> dict:
    md5 = hashlib.md5(data).hexdigest()
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        try:
            im.draft("RGB", (512, 512))
        except Exception:
            pass
        ph = perceptual_hash(im)
    return {"md5": md5, "w": w, "h": h, "dhash": ph}


def analyze_file(path: str):
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            rec = analyze_bytes(f.read())
        rec.update(path=path, size=st.st_size, mtime_ns=st.st_mtime_ns)
        return rec
    except Exception:
        return None


class HashCache:
    def __init__(self, db_path: str):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS hashes16 (path TEXT PRIMARY KEY, size INTEGER, "
                          "mtime_ns INTEGER, w INTEGER, h INTEGER, md5 TEXT, dhash TEXT)")
        self.conn.commit()

    def get(self, path, size, mtime_ns):
        with self.lock:
            row = self.conn.execute("SELECT w,h,md5,dhash FROM hashes16 WHERE path=? AND size=? AND mtime_ns=?",
                                    (path, size, mtime_ns)).fetchone()
        if not row:
            return None
        return {"path": path, "size": size, "mtime_ns": mtime_ns, "w": row[0], "h": row[1],
                "md5": row[2], "dhash": int(row[3], 16) if row[3] else None}

    def put_many(self, recs):
        rows = [(r["path"], r["size"], r["mtime_ns"], r["w"], r["h"], r["md5"],
                 format(r["dhash"], "x") if r["dhash"] is not None else "") for r in recs]
        with self.lock:
            self.conn.executemany("INSERT OR REPLACE INTO hashes16 VALUES (?,?,?,?,?,?,?)", rows)
            self.conn.commit()


def open_cache() -> HashCache:
    try:
        return HashCache(str(paths.cache_dir() / "hashes.sqlite"))
    except Exception:
        return HashCache(":memory:")


class HashIndex:
    """Multi-index hashing: all stored hashes within `threshold` bits of a query."""

    def __init__(self, threshold: int):
        self.t = max(0, int(threshold))
        self.m = self.t + 1
        self.w = -(-HASH_BITS // self.m)
        self.mask = (1 << self.w) - 1
        self.buckets = defaultdict(list)
        self.items = []

    def _keys(self, h):
        return [(i, (h >> (i * self.w)) & self.mask) for i in range(self.m)]

    def add(self, h, aspect, ident):
        pos = len(self.items)
        self.items.append((h, aspect, ident))
        for k in self._keys(h):
            self.buckets[k].append(pos)

    def query(self, h, aspect):
        seen, hits = set(), []
        for k in self._keys(h):
            for pos in self.buckets.get(k, ()):
                if pos in seen:
                    continue
                seen.add(pos)
                oh, oa, ident = self.items[pos]
                if abs(oa - aspect) / max(aspect, 1e-6) > ASPECT_TOL:
                    continue
                d = popcount(h ^ oh)
                if d <= self.t:
                    hits.append((d, ident))
        hits.sort(key=lambda x: x[0])
        return hits


class DuplicateGuard:
    """Checks new images against the ones already in a folder (used while collecting)."""

    def __init__(self, threshold: int):
        self.md5, self.index = set(), HashIndex(threshold)

    @classmethod
    def from_folder(cls, folder: str, threshold: int, cache: HashCache | None = None):
        g = cls(threshold)
        for recs, _, _ in iter_hashed(list(iter_images(folder)), cache):
            for r in recs:
                g.add(r)
        return g

    def check(self, r: dict) -> bool:
        if r["md5"] in self.md5:
            return True
        return r["dhash"] is not None and bool(self.index.query(r["dhash"], r["w"] / max(r["h"], 1)))

    def add(self, r: dict) -> None:
        self.md5.add(r["md5"])
        if r["dhash"] is not None:
            self.index.add(r["dhash"], r["w"] / max(r["h"], 1), r["md5"])


def iter_hashed(file_paths, cache, ctx: TaskContext | None = None, workers=None):
    """Yield (records, done, total) while hashing, using the cache and a thread pool."""
    ready, todo = [], []
    for p in file_paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        hit = cache.get(p, st.st_size, st.st_mtime_ns) if cache else None
        (ready if hit else todo).append(hit or p)
    total, done = len(ready) + len(todo), len(ready)
    yield ready, done, total
    with ThreadPoolExecutor(workers or min(8, os.cpu_count() or 2)) as ex:
        for i in range(0, len(todo), 128):
            if ctx and ctx.cancelled:
                return
            chunk = todo[i:i + 128]
            recs = [r for r in ex.map(analyze_file, chunk) if r]
            if cache:
                cache.put_many(recs)
            done += len(chunk)
            yield recs, done, total


def keeper_key(r: dict):
    """Higher is better: not low-quality, more pixels, bigger file, shorter path, older."""
    return (not has_part(r["path"], LOW_QUALITY_DIRS), r["w"] * r["h"], r["size"], -len(r["path"]), -r["mtime_ns"])


@dataclass
class DuplicateGroup:
    keeper: dict
    duplicates: list = field(default_factory=list)

    @property
    def reclaimable(self) -> int:
        return sum(d["size"] for d in self.duplicates)


def find_groups(recs: list[dict], threshold: int) -> list[DuplicateGroup]:
    kept_by_md5, idx, groups = {}, HashIndex(threshold), {}
    for r in sorted(recs, key=keeper_key, reverse=True):
        match = None
        if r["md5"] in kept_by_md5:
            match = kept_by_md5[r["md5"]]
        elif r["dhash"] is not None:
            hits = idx.query(r["dhash"], r["w"] / max(r["h"], 1))
            if hits:
                match = hits[0][1]
        if match is None:
            kept_by_md5[r["md5"]] = r["path"]
            groups[r["path"]] = DuplicateGroup(r)
            if r["dhash"] is not None:
                idx.add(r["dhash"], r["w"] / max(r["h"], 1), r["path"])
        else:
            groups[match].duplicates.append(r)
    return [g for g in groups.values() if g.duplicates]


def scan_duplicates(base: str, threshold: int, scope: str, ctx: TaskContext) -> list[DuplicateGroup]:
    """scope: 'library' (compare everything) or 'collection' (within each top-level folder)."""
    ctx.log(f"Scanning {base} (similarity threshold {threshold}/{HASH_BITS})")
    cache, recs = open_cache(), []
    files = list(iter_images(base))
    for batch, done, total in iter_hashed(files, cache, ctx):
        recs.extend(batch)
        ctx.progress(done, total, f"Analyzing images ({done}/{total})")
    if ctx.cancelled:
        ctx.log("Cancelled.")
        return []
    buckets = defaultdict(list)
    for r in recs:
        key = ""
        if scope == "collection":
            rel = os.path.relpath(r["path"], base).split(os.sep)
            key = rel[0] if len(rel) > 1 else ""
        buckets[key].append(r)
    groups = [g for b in buckets.values() for g in find_groups(b, threshold)]
    groups.sort(key=lambda g: -g.reclaimable)
    ctx.log(f"{len(recs)} images analyzed: {len(groups)} duplicate groups, "
            f"{sum(len(g.duplicates) for g in groups)} redundant files.")
    return groups


@dataclass
class RemovalResult:
    removed: int = 0
    freed: int = 0
    failed: int = 0


def apply_removals(selection: list[tuple[str, list[str]]], mode: str, ctx: TaskContext) -> RemovalResult:
    """selection: [(keeper_path, [duplicate_paths])]. mode: 'trash' or 'delete'.
    Caption files go with their image; a duplicate's caption is kept if the keeper has none."""
    res = RemovalResult()
    total = sum(len(d) for _, d in selection)
    if mode == "trash" and not ctx.trash:
        ctx.log("No Trash mechanism available (install `gio`/glib tools or trash-cli). Nothing was removed.")
        return res
    for keeper, dups in selection:
        ktxt = os.path.splitext(keeper)[0] + ".txt"
        for d in dups:
            if ctx.cancelled:
                ctx.log("Cancelled.")
                return res
            side = sidecars(d)
            if side and not os.path.exists(ktxt):
                try:
                    shutil.copy2(side[0], ktxt)
                except OSError:
                    pass
            size = os.path.getsize(d) if os.path.exists(d) else 0
            ok = True
            for fp in [d] + side:
                try:
                    if mode == "delete":
                        os.remove(fp)
                    elif not ctx.trash(fp):
                        raise OSError("could not move to Trash")
                except OSError as e:
                    ok = False
                    ctx.log(f"Could not remove {os.path.basename(fp)}: {e}")
            if ok:
                res.removed += 1
                res.freed += size
            else:
                res.failed += 1
            ctx.progress(res.removed + res.failed, total, "Removing duplicates")
    return res
