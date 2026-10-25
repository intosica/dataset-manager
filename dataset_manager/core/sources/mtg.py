"""Trading-card art galleries: ArtofMTG (plain HTTP) and MTG Nexus (browser)."""
from __future__ import annotations

import random
import re

from ..browser import open_browser
from ..collector import CollectOptions
from ..settings import Settings
from ..sink import ImageSink
from ..tasks import TaskContext
from ..utils import clean_tag

HEADERS = {"User-Agent": "Mozilla/5.0"}


def run_artofmtg(query: str, opts: CollectOptions, sink: ImageSink, ctx: TaskContext) -> None:
    import requests
    from bs4 import BeautifulSoup

    limit = int(opts.limit)
    slug = query.lower().strip().replace(" ", "-")
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        page = session.get(f"https://www.artofmtg.com/artist/{slug}/", timeout=20)
        page.raise_for_status()
    except requests.RequestException as e:
        ctx.log(f"Could not load the artist page: {e}")
        return
    links = list(dict.fromkeys(a["href"] for a in BeautifulSoup(page.text, "html.parser").find_all("a", href=True)
                               if "/art/" in a["href"]))
    if opts.randomize:
        random.shuffle(links)
    ctx.log(f"{len(links)} artworks listed.")
    for n, url in enumerate(links, 1):
        if ctx.cancelled or (limit and sink.counts["saved"] >= limit):
            break
        ctx.progress(sink.counts["saved"] if limit else n, limit or len(links), f"{sink.counts['saved']} images saved")
        try:
            soup = BeautifulSoup(session.get(url, timeout=20).text, "html.parser")
            h1 = soup.find("h1")
            title = h1.get_text().strip().replace("MtG Art", "").strip() if h1 else url.rstrip("/").split("/")[-1]
            stem = clean_tag(title)
            if not stem or sink.has_name(stem):
                continue
            for img in soup.find_all("img"):
                src = img.get("data-src") or img.get("src")
                if src and "wp-content/uploads" in src and not any(x in src for x in ("logo", "avatar")):
                    data = session.get(src.split("?")[0], timeout=30).content
                    if sink.ingest(data, stem) == "saved":
                        ctx.log(f"Saved: {title}")
                    break
        except requests.RequestException as e:
            ctx.log(f"Skipped {url}: {e}")


async def run_nexus(query: str, opts: CollectOptions, settings: Settings, sink: ImageSink, ctx: TaskContext) -> None:
    limit = int(opts.limit)
    slug = query.lower().strip().replace(" ", "-")
    async with open_browser(settings, ctx) as (_browser, page):
        await page.goto(f"https://www.mtgnexus.com/gallery/artists/{slug}/", wait_until="domcontentloaded")
        slugs = list(dict.fromkeys(re.findall(r"/gallery/art/(\d+-[^\"'>\s]+)", await page.content())))
        if opts.randomize:
            random.shuffle(slugs)
        ctx.log(f"{len(slugs)} artworks listed.")
        for n, s in enumerate(slugs, 1):
            if ctx.cancelled or (limit and sink.counts["saved"] >= limit):
                break
            ctx.progress(sink.counts["saved"] if limit else n, limit or len(slugs), f"{sink.counts['saved']} images saved")
            try:
                await page.goto(f"https://www.mtgnexus.com/gallery/art/{s}", wait_until="domcontentloaded")
                h1 = await page.query_selector("h1")
                raw = await h1.inner_text() if h1 else s
                card = re.split(r" - | by ", raw, flags=re.IGNORECASE)[0].strip()
                stem = clean_tag(card)
                if not stem or sink.has_name(stem):
                    continue
                img_el = await page.query_selector('img.gallery-image, img[src*="/images/gallery/"]')
                if not img_el:
                    continue
                src = await img_el.get_attribute("src")
                res = await page.request.get(f"https://www.mtgnexus.com{src}" if src.startswith("/") else src)
                if sink.ingest(await res.body(), stem) == "saved":
                    ctx.log(f"Saved: {card}")
            except Exception as e:
                ctx.log(f"Skipped {s}: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
