"""Image search engines, Pinterest boards and Internet Archive items, via a real browser."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from urllib.parse import parse_qs, quote, urlparse

from .. import paths
from ..browser import has_display, open_browser
from ..collector import CollectOptions
from ..settings import Settings
from ..sink import ImageSink
from ..tasks import TaskContext

SEARCH_URLS = {
    "Bing": "https://www.bing.com/images/search?q={q}&qft=+filterui:imagesize-large",
    "DuckDuckGo": "https://duckduckgo.com/?q={q}&iax=images&ia=images",
    "Yahoo": "https://images.search.yahoo.com/search/images?p={q}",
    "Yandex": "https://yandex.com/images/search?text={q}&isize=large",
    "Pinterest": "https://www.pinterest.com/search/pins/?q={q}",
    "Internet Archive": "https://archive.org/search?query={q}",
}
YANDEX_ITEM = ".SerpItem, .serp-item"          # SerpItem = current markup, serp-item = the old one

YANDEX_JS = """(sel) => {
    const fix = u => !u ? '' : (u.startsWith('//') ? 'https:' + u : u);
    let out = [];
    document.querySelectorAll(sel).forEach(el => {
        let original = '', preview = '';
        const img = el.querySelector('img');
        if (img) preview = fix(img.currentSrc || img.getAttribute('src') || '');
        const a = el.querySelector('a[href*="img_url="]');   // current markup: original lives in the link
        if (a) {
            try { original = new URL(a.href, location.href).searchParams.get('img_url') || ''; } catch (e) {
                const m = /[?&]img_url=([^&]+)/.exec(a.href);
                original = m ? decodeURIComponent(m[1]) : '';
            }
        }
        if (!original) {                                      // old markup kept the data in data-bem
            try {
                const d = (JSON.parse(el.getAttribute('data-bem') || '{}')['serp-item']) || {};
                original = fix(d.img_href || (d.dups && d.dups[0] && d.dups[0].url) || '');
                preview = preview || fix((d.preview && d.preview[0] && d.preview[0].url)
                                         || (d.thumb && d.thumb.url) || '');
            } catch (e) {}
        }
        if (original || preview) out.push([original, preview]);
    });
    return out;
}"""

JUNK_URL = re.compile(r"(^|[/_.\-])(logo|avatar|icon|favicon|sprite|banner|thumb(nail)?)s?([/_.\-?]|$)|base64", re.I)

YAHOO_JS = """() => {
    let urls = [];
    document.querySelectorAll('li').forEach(li => {
        try {
            let img = li.querySelector('img'); let a = li.querySelector('a');
            if (!img) return;
            let url = '';
            if (a && a.href && a.href.includes('imgurl=')) {
                let m = a.href.match(/imgurl=([^&]+)/);
                if (m) url = decodeURIComponent(m[1]);
            }
            if (!url && li.hasAttribute('data')) {
                let d = JSON.parse(li.getAttribute('data'));
                if (d && d.iurl) url = d.iurl;
            }
            if (!url) url = img.getAttribute('src') || img.getAttribute('data-src');
            if (url && url.startsWith('http')) urls.push(url);
        } catch(e){}
    });
    return urls;
}"""


async def _auto_scroll(page, engine: str, limit: int, ctx: TaskContext) -> None:
    rounds = 20 if limit == 0 else max(3, min(30, limit // 8 + 3))
    last_h, stagnant = 0, 0
    for _ in range(rounds):
        if ctx.cancelled:
            break
        for _ in range(random.randint(3, 6)):
            await page.evaluate("(d) => window.scrollBy(0, d)", random.randint(300, 800))
            await page.wait_for_timeout(random.randint(150, 450))
            if random.random() > 0.6:
                await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
        if engine == "Bing":
            try:
                btn = await page.query_selector("a.btn_seemore")
                if btn and await btn.is_visible():
                    await btn.click()
            except Exception:
                pass
        await page.wait_for_timeout(random.randint(900, 2200))
        h = await page.evaluate("document.documentElement.scrollHeight")
        stagnant = stagnant + 1 if h == last_h else 0
        last_h = h
        if stagnant >= 2:
            break


async def _wait_tiles(page, timeout_s: int = 12) -> bool:
    """Are result tiles on screen? Yandex reloads the page under us, which kills the execution context."""
    for _ in range(timeout_s * 2):
        try:
            if page.is_closed():
                return False
            if await page.query_selector(YANDEX_ITEM):
                return True
        except Exception:
            pass                              # navigated mid-query; just look again
        try:
            await page.wait_for_timeout(500)
        except Exception:
            return False                      # the window was closed
    return False


async def _wait_for_yandex_results(page, settings: Settings, ctx: TaskContext, timeout_s: int = 90) -> bool:
    """Wait until result tiles exist. If Yandex shows a robot check the window stays open for the user."""
    warned = False
    for i in range(timeout_s):
        if ctx.cancelled:
            return False
        if await _wait_tiles(page, 1):
            return True
        if page.is_closed():
            return False
        if not warned and i >= 5:
            warned = True
            if settings.headless or not has_display():
                ctx.log("Yandex is not showing results (probably a robot check) and the browser has no window. "
                        "Turn off 'Run the browser without a window' in Preferences and try again.")
                return False
            ctx.log("Yandex is not showing results yet — probably a robot check.")
            ctx.log("Complete it in the browser window. Collection continues automatically; Cancel stops it.")
            ctx.log("Your answer is remembered in the browser profile, so later runs usually skip the check.")
            ctx.progress(0, 0, "Waiting for the robot check")
        await page.wait_for_timeout(1000)
    ctx.log("Timed out waiting for Yandex results.")
    return False


async def _human_move(page, x: float, y: float) -> None:
    """Approach the target in two legs with a pause, so the pointer never travels in one dead-straight line."""
    await page.mouse.move(x - random.randint(60, 200), y - random.randint(30, 130), steps=random.randint(8, 16))
    await page.wait_for_timeout(random.randint(60, 200))
    await page.mouse.move(x, y, steps=random.randint(12, 26))


async def _human_click(page, selector: str) -> bool:
    """Click slightly off-centre with real (trusted) mouse events, after a pointer move and a short dwell."""
    try:
        el = await page.query_selector(selector)
        box = await el.bounding_box() if el else None
        if not box:
            return False
        x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
        y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        await _human_move(page, x, y)
        await page.wait_for_timeout(random.randint(120, 420))
        await page.mouse.click(x, y)
        return True
    except Exception:
        return False


YANDEX_VIEWER_JS = """() => {
    const src = i => i && i.naturalWidth > 100 ? (i.currentSrc || i.src) : '';
    let url = '';
    try { url = new URL(location.href).searchParams.get('img_url') || ''; } catch (e) {}
    return {
        url,
        origin: src(document.querySelector('img[class*="MMImage-Origin"]')),
        preview: src(document.querySelector('img[class*="MMImage-Preview"]')),
    };
}"""

YANDEX_TILE = ".SerpItem a[href*='img_url='], .serp-item a"


async def _fetch(page, *candidates: str):
    for cand in candidates:
        if not cand or not cand.startswith("http"):
            continue
        try:
            res = await page.request.get(cand, timeout=15000)
            ct = res.headers.get("content-type", "")
            if res.ok and not any(t in ct for t in ("text", "html", "json")):
                return await res.body()
        except Exception:
            continue
    return None


async def _yandex_viewer(page, sink: ImageSink, ctx: TaskContext, limit: int) -> None:
    """Open the first result and walk the viewer at a human pace, saving each full-size image."""
    if not await _human_click(page, YANDEX_TILE):
        return
    await page.wait_for_timeout(random.randint(1500, 2800))
    ctx.log("Stepping through the Yandex viewer...")
    max_steps = limit * 3 if limit else 300
    stuck = 0
    for step in range(max_steps):
        if ctx.cancelled or (limit and sink.counts["saved"] >= limit):
            break
        try:
            info = await page.evaluate(YANDEX_VIEWER_JS)
        except Exception:
            break                             # viewer closed or the page navigated away
        url = info.get("url") or info.get("origin") or ""
        if not url or sink.is_seen(url):
            stuck += 1
            if stuck >= 5:
                break
        else:
            stuck = 0
            data = await _fetch(page, url, info.get("preview", ""))
            if data:
                name = f"image_{hashlib.md5(url.encode()).hexdigest()[:12]}"
                if await asyncio.to_thread(sink.ingest, data, name) in ("saved", "duplicate"):
                    sink.mark_seen(url)
            done = sink.counts["saved"]
            ctx.progress(done if limit else step + 1, limit or max_steps, f"{done} images saved")
        await page.wait_for_timeout(random.randint(900, 1900))     # dwell on the image like a person would
        await page.keyboard.press("ArrowRight")
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


async def _yandex_pages(page, url: str, want: int, ctx: TaskContext):
    """Walk the result pages with ?p=N. Yandex's own "Show more" button always ends in a robot check."""
    raw, fallbacks, seen, empty, blocked = [], {}, set(), 0, 0
    for p in range(25):                     # ponytail: 25 pages ≈ 750 candidates; raise if anyone needs more
        if ctx.cancelled:
            break
        page_url = url if p == 0 else f"{url}&p={p}"
        try:
            await page.goto(page_url, wait_until="domcontentloaded")
        except Exception as exc:
            ctx.log(f"Could not load page {p + 1}: {str(exc).splitlines()[0]}")
            continue
        await page.wait_for_timeout(random.randint(1200, 2400))
        await _wait_tiles(page)
        if "showcaptcha" in page.url:
            blocked += 1
            ctx.log(f"Page {p + 1} came back as a robot check; skipping it.")
            if blocked >= 2:
                ctx.log(f"Yandex keeps asking for a robot check; keeping the {len(raw)} images found so far.")
                break
            continue
        blocked = 0
        found = 0
        try:
            extracted = await page.evaluate(YANDEX_JS, YANDEX_ITEM)
        except Exception:
            await _wait_tiles(page)               # the page reloaded under us; give it a moment and retry
            try:
                extracted = await page.evaluate(YANDEX_JS, YANDEX_ITEM)
            except Exception:
                ctx.log(f"Page {p + 1} would not settle; moving on.")
                continue
        for original, preview in extracted:
            best = original or preview      # full-size image, falling back to Yandex's own preview
            if best and best not in seen:
                seen.add(best)
                raw.append(best)
                found += 1
                if original and preview and original != preview:
                    fallbacks[original] = preview
        ctx.log(f"Page {p + 1}: {found} images ({len(raw)} so far).")
        empty = empty + 1 if not found else 0
        if want and len(raw) >= want:
            break
        if empty >= 2:
            break
    return raw, fallbacks


async def _debug_shot(page, ctx: TaskContext) -> None:
    try:
        out = paths.cache_dir() / "yandex-debug.png"
        await page.screenshot(path=str(out))
        ctx.log(f"Saved a screenshot for troubleshooting: {out}")
    except Exception:
        pass


async def _extract_urls(page, engine: str, query: str, direct_ia: bool, cap: int, ctx: TaskContext):
    urls, fallbacks = [], {}
    if engine == "Bing":
        for el in await page.query_selector_all("a.iusc"):
            m_attr = await el.get_attribute("m")
            if m_attr:
                try:
                    u = json.loads(m_attr).get("murl")
                    if u:
                        urls.append(u)
                except ValueError:
                    continue
    elif engine == "DuckDuckGo":
        srcs = await page.evaluate("() => Array.from(document.querySelectorAll('img')).map(i => i.src || i.getAttribute('data-src'))")
        for src in srcs:
            if not src:
                continue
            if "duckduckgo.com/iu/?u=" in src:
                qs = parse_qs(urlparse(src).query)
                if "u" in qs:
                    urls.append(qs["u"][0])
            else:
                urls.append(src)
    elif engine == "Yahoo":
        urls.extend(await page.evaluate(YAHOO_JS))
    elif engine == "Pinterest":
        for src in await page.evaluate("() => Array.from(document.querySelectorAll('img')).map(i => i.src)"):
            if src and "pinimg.com" in src:
                hi = re.sub(r"/\d+x/", "/originals/", src)
                urls.append(hi)
                if hi != src:
                    fallbacks[hi] = src
    elif engine == "Internet Archive":
        if direct_ia:
            m = re.search(r"/details/([^/?#]+)", query)
            idents = [m.group(1)] if m else []
        else:
            hrefs = await page.evaluate("() => Array.from(document.querySelectorAll('a[href^=\"/details/\"]')).map(a => a.getAttribute('href'))")
            idents = []
            for href in hrefs:
                ident = href.split("/details/")[1].split("/")[0].split("?")[0]
                if ident and ident not in idents:
                    idents.append(ident)
        ctx.log(f"Found {len(idents)} Internet Archive items; reading file lists...")
        for ident in idents:
            if ctx.cancelled or (cap and len(urls) >= cap):
                break
            try:
                res = await page.request.get(f"https://archive.org/metadata/{ident}", timeout=8000)
                found = False
                if res.ok:
                    for f in (await res.json()).get("files", []):
                        name = f.get("name", "")
                        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                            urls.append(f"https://archive.org/download/{ident}/{quote(name)}")
                            found = True
                if not found:
                    urls.append(f"https://archive.org/services/img/{ident}")
            except Exception:
                urls.append(f"https://archive.org/services/img/{ident}")
    return urls, fallbacks



async def run(query: str, opts: CollectOptions, settings: Settings, sink: ImageSink, ctx: TaskContext) -> None:
    engine = opts.source
    direct_pin = query.startswith("http") and "pinterest." in query.lower()
    direct_ia = query.startswith("http") and "archive.org/details/" in query.lower()
    if direct_pin:
        engine = "Pinterest"
    elif direct_ia:
        engine = "Internet Archive"
    limit = int(opts.limit)

    async with open_browser(settings, ctx, prefer_system=(engine == "Yandex")) as (_browser, page):
        url = query if (direct_pin or direct_ia) else SEARCH_URLS.get(engine, SEARCH_URLS["Bing"]).format(q=quote(query))
        ctx.progress(0, 0, "Loading page")
        ctx.log("Loading page...")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(2500, 4000))
        if engine == "Yandex":
            if not await _wait_for_yandex_results(page, settings, ctx):
                if not ctx.cancelled:
                    await _debug_shot(page, ctx)
                    ctx.log("Yandex did not show any results.")
                return
            ctx.progress(0, 0, "Opening the first result")
            await _yandex_viewer(page, sink, ctx, limit)
            raw, fallbacks = [], {}
            if not ctx.cancelled and (not limit or sink.counts["saved"] < limit):
                ctx.progress(0, 0, "Reading result pages")
                raw, fallbacks = await _yandex_pages(page, url, limit * 2 if limit else 0, ctx)
        else:
            ctx.progress(0, 0, "Scrolling for more results")
            await _auto_scroll(page, engine, limit, ctx)
            raw, fallbacks = await _extract_urls(page, engine, query, direct_ia, limit * 3 if limit else 0, ctx)
        urls = []
        for u in dict.fromkeys(raw):
            if not u or not u.startswith("http"):
                continue
            if JUNK_URL.search(u) and "archive.org/services/img/" not in u:
                continue
            urls.append(u)
        if opts.randomize:
            random.shuffle(urls)
        if raw:
            ctx.log(f"{len(urls)} candidate images found.")

        async def fetch(u: str):
            return await _fetch(page, u, *([fallbacks[u]] if u in fallbacks else []))

        parallel, i = max(1, int(opts.parallel)), 0
        while i < len(urls) and not ctx.cancelled:
            if limit and sink.counts["saved"] >= limit:
                break
            batch = [u for u in urls[i:i + parallel] if not sink.is_seen(u)]
            i += parallel
            for data, u in zip(await asyncio.gather(*(fetch(u) for u in batch)), batch):
                if limit and sink.counts["saved"] >= limit:
                    break
                if not data:
                    continue
                name = f"image_{hashlib.md5(u.encode()).hexdigest()[:12]}"
                status = await asyncio.to_thread(sink.ingest, data, name)
                if status in ("saved", "duplicate"):
                    sink.mark_seen(u)
            done = sink.counts["saved"]
            ctx.progress(done if limit else min(i, len(urls)), limit or len(urls), f"{done} images saved")
