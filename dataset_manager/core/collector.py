"""Entry point for collecting images from any source."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import capabilities, paths
from .settings import Settings
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

    query = opts.query.strip()
    if not query:
        ctx.log("Enter search terms or a URL.")
        return {}
    needs = {"ArtofMTG": ["requests", "bs4"]}.get(opts.source, ["playwright"])
    missing = [m for m in needs if not capabilities.has(m)]
    if missing:
        hint = {m: capabilities.FEATURES[m][1] for m in missing}
        ctx.log("Missing packages: " + ", ".join(missing))
        for cmd in dict.fromkeys(hint.values()):
            ctx.log(f"  {cmd}")
        return {}

    tag, folder = derive_names(query, opts.collection_name)
    save_dir = os.path.join(settings.library_path(), folder)
    if os.path.commonpath([os.path.abspath(settings.library_path()), os.path.abspath(save_dir)]) \
            != os.path.abspath(settings.library_path()):
        ctx.log("Invalid collection name.")
        return {}
    sink = ImageSink(save_dir, tag, opts.sink)
    ctx.log(f"Source: {opts.source}   Query: {query}")
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
