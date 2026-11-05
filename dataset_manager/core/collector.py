"""Entry point for collecting images from any source."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import capabilities, paths, pkgs
from .settings import Settings
from .aesthetic_score import AestheticFilter, AestheticSpec
from .similarity import SimilaritySpec, SimilarityFilter
from .sink import ImageSink, SinkOptions
from .tasks import TaskContext
from .utils import clean_tag, safe_name


@dataclass
class CollectOptions:
    source: str = "Bing"
    query: str = ""
    collection_name: str = ""
    limit: int = 100                 # 0 = unlimited
    randomize: bool = False
    parallel: int = 4
    safe_search_off: bool = False
    seeds: list = field(default_factory=list)            # image paths / URLs for 'visually similar' search
    filters: dict = field(default_factory=dict)          # group key -> [option values], see sources/filters.py
    similarity: SimilaritySpec = field(default_factory=SimilaritySpec)
    aesthetic: AestheticSpec = field(default_factory=AestheticSpec)
    sink: SinkOptions = field(default_factory=SinkOptions)


def derive_names(query: str, collection_name: str, fallback: str = "Gallery") -> tuple[str, str]:
    """(tag used in captions, folder name)"""
    name = (collection_name or "").strip()
    if name:
        label = name
    elif query.startswith("http"):
        parts = [p for p in urlparse(query).path.split("/") if p]
        label = (parts[-1] if parts else fallback).replace("-", " ")
    else:
        label = query.strip()
    return clean_tag(label), safe_name(label.title())


def run_collection(opts: CollectOptions, settings: Settings, ctx: TaskContext) -> dict:
    from .sources import mtg, search

    pkgs.activate(settings)                  # packages installed into a venv/system site while the app was running
    query = opts.query.strip()
    if not query and not opts.seeds:
        ctx.log("Enter search terms or a URL, or a seed image for visual search.")
        return {}
    if opts.seeds and opts.source not in ("Bing", "Yandex"):
        opts.seeds = []
    needs = {"ArtofMTG": ["requests", "bs4"]}.get(opts.source, ["playwright"])
    missing = [m for m in needs if not capabilities.has(m)]
    if missing:
        ctx.log("Missing packages: " + ", ".join(missing))
        ctx.log("  " + capabilities.pip_hint(missing) + "   (or Preferences → System → Install)")
        return {}

    if query:
        tag, folder = derive_names(query, opts.collection_name)
    else:                                        # seeds only: name the collection after the first seed
        seed = opts.seeds[0]
        stem = os.path.splitext(os.path.basename(urlparse(seed).path if seed.startswith("http") else seed))[0]
        tag, folder = derive_names("", opts.collection_name or f"Similar to {stem[:40] or 'seed'}")
    save_dir = os.path.join(settings.library_path(), folder)
    if os.path.commonpath([os.path.abspath(settings.library_path()), os.path.abspath(save_dir)]) \
            != os.path.abspath(settings.library_path()):
        ctx.log("Invalid collection name.")
        return {}
    if opts.similarity.enabled:
        try:
            opts.sink.similarity = SimilarityFilter(opts.similarity, ctx)
        except Exception as exc:
            ctx.log(f"Similarity filter unavailable: {exc}")
            return {}
        if ctx.cancelled:
            return {}
    if opts.aesthetic.enabled:
        if not AestheticFilter.available():
            ctx.log("The aesthetic filter needs aesthetic-predictor-v2-5.")
            ctx.log("  " + capabilities.pip_hint(["aesthetic_predictor_v2_5"])
                     + "   (or Preferences → System → Install)")
            return {}
        opts.sink.aesthetic = AestheticFilter(opts.aesthetic, ctx)
    sink = ImageSink(save_dir, tag, opts.sink)
    ctx.log(f"Source: {opts.source}   Query: {query or '(visual search)'}")
    ctx.log(f"Destination: {save_dir}")
    ctx.log("Indexing existing images..." if opts.sink.skip_duplicates else "Preparing...")
    sink.prepare()
    try:
        if opts.source == "ArtofMTG":
            mtg.run_artofmtg(query, opts, sink, ctx)
        elif opts.source == "MTG Nexus":
            asyncio.run(mtg.run_nexus(query, opts, settings, sink, ctx))
        else:
            asyncio.run(search.run(query, opts, settings, sink, ctx))
    finally:
        sink.flush()
    ctx.log("Cancelled. " + sink.summary() if ctx.cancelled else "Finished: " + sink.summary())
    return dict(sink.counts)
