"""Image search engines, Pinterest boards and Internet Archive items, via a real browser."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
from urllib.parse import parse_qs, quote, urlparse

from .. import paths
from ..browser import has_display, open_browser
from ..collector import CollectOptions
from ..settings import Settings
from ..sink import ImageSink
from ..tasks import TaskContext
from . import human
from .filters import NO_SAFE_SEARCH, build_url, combos, describe

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
        for _ in range(random.randint(2, 4)):
            await human.scroll(page, random.randint(300, 800))
            await human.pause(0.1, 0.4)
            if random.random() > 0.7:
                await human.move_to(page, random.randint(100, 800), random.randint(100, 600))
        if engine == "Bing":
            try:
                btn = await page.query_selector("a.btn_seemore")
                if btn and await btn.is_visible():
                    await human.click(page, btn)
            except Exception:
                pass
        await human.pause(0.9, 2.2)
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


async def _wait_captcha(page, settings: Settings, ctx: TaskContext, timeout_s: int = 300) -> bool:
    """Robot check on screen: leave the window for the user to solve (we never auto-solve it). True once it clears."""
    if settings.headless or not has_display():
        return False
    ctx.log("Yandex wants a robot check. Solve it in the browser window; collection resumes automatically.")
    ctx.progress(0, 0, "Waiting for the robot check")
    for _ in range(timeout_s):
        if ctx.cancelled or page.is_closed():
            return False
        if "showcaptcha" not in page.url:
            ctx.log("Robot check passed.")
            return True
        await page.wait_for_timeout(1000)
    return False


# Yandex serves the first 30 results of any search freely, but every further page (?p=N, "Show more", or walking
# past the end in the viewer) is a robot check, every time, even after solving one. A filter variant is a new search,
# so its first page is free too and holds a largely different 30: ~20 new images per variant.
_ORIENT = ["&iorient=horizontal", "&iorient=vertical", "&iorient=square"]
_COLOR = [f"&icolor={c}" for c in ("gray", "red", "orange", "yellow", "green", "cyan", "blue", "violet", "white", "black")]
YANDEX_VARIANTS = _ORIENT + ["&type=clipart", "&type=lineart"] + _COLOR + [o + c for o in _ORIENT for c in _COLOR]


async def _yandex_variants(page, urls: list[str], want: int, ctx: TaskContext, settings: Settings):
    """Read the first result page of each URL; urls[0] is already loaded."""
    raw, fallbacks, seen, dry, blocked = [], {}, set(), 0, 0
    for i, url in enumerate(urls):
        if ctx.cancelled:
            break
        if i:
            await page.wait_for_timeout(random.randint(2500, 6000))     # unhurried, like someone trying filters
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                ctx.log(f"Could not load a result page: {str(exc).splitlines()[0]}")
                continue
            await _wait_tiles(page)
            if "showcaptcha" in page.url and await _wait_captcha(page, settings, ctx):
                await _wait_tiles(page)
            if "showcaptcha" in page.url:
                blocked += 1
                if blocked >= 2:
                    ctx.log(f"Yandex keeps asking for a robot check; keeping the {len(raw)} images found so far.")
                    break
                continue
            blocked = 0
            for _ in range(random.randint(2, 4)):                     # glance down the page
                await page.mouse.wheel(0, random.randint(300, 700))
                await page.wait_for_timeout(random.randint(250, 700))
        try:
            extracted = await page.evaluate(YANDEX_JS, YANDEX_ITEM)
        except Exception:
            await _wait_tiles(page)                                     # the page reloaded under us
            try:
                extracted = await page.evaluate(YANDEX_JS, YANDEX_ITEM)
            except Exception:
                continue
        found = 0
        for original, preview in extracted:
            best = original or preview      # full-size image, falling back to Yandex's own preview
            if best and best not in seen:
                seen.add(best)
                raw.append(best)
                found += 1
                if original and preview and original != preview:
                    fallbacks[original] = preview
        ctx.log(f"Result set {i + 1}: {found} new images ({len(raw)} so far).")
        ctx.progress(0, 0, f"Reading results: {len(raw)} found")
        dry = dry + 1 if found < 3 else 0
        if (want and len(raw) >= want) or (dry >= 4 and len(urls) > 8):
            break
    return raw, fallbacks


async def _yandex_safe_search(page, off: bool, ctx: TaskContext) -> None:
    """Set Yandex's "Filter search results" to No filter / Moderate. It lives in the browser profile, so it is applied
    on every run; with the filter on, Yandex blurs adult results and a robot clicking blurred tiles gets captchas."""
    want = "free" if off else "mild"
    try:
        await page.goto("https://yandex.com/tune/search/", wait_until="domcontentloaded")
        if await page.query_selector("input[name=filter_search_results]") is None:
            return ctx.log("Could not reach Yandex's search settings; leaving safe search as it is.")
        if await page.evaluate("v => document.querySelector(`input[name=filter_search_results][value=${v}]`).checked", want):
            return
        await human.click(page, page.get_by_text("No filter" if off else "Moderate search", exact=True))
        await human.click(page, page.locator(".button-save__button"))
        await human.pause(0.8, 1.6)
        await page.wait_for_timeout(random.randint(1200, 2200))
        ctx.log(f"Yandex safe search: {'off' if off else 'moderate'}.")
    except Exception as exc:
        ctx.log(f"Could not change Yandex safe search: {str(exc).splitlines()[0]}")


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



async def _safe_search_off(page, engine: str, query: str, ctx: TaskContext) -> None:
    """Safe search off for engines that keep it as a preference; the URL parameter alone is not always honoured."""
    try:
        if engine == "Bing":
            await page.context.add_cookies([{"name": "SRCHHPGUSR", "value": "ADLT=OFF", "domain": ".bing.com", "path": "/"}])
        elif engine == "DuckDuckGo":
            await page.context.add_cookies([{"name": "p", "value": "-2", "domain": ".duckduckgo.com", "path": "/"}])
        elif engine == "Yahoo":       # the "SafeSearch off" menu entry is a link that stores the preference
            await page.goto(build_url("Yahoo", query, {}), wait_until="domcontentloaded")
            href = await page.evaluate("""() => { const a = [...document.querySelectorAll('a[href*="vm=p"]')]
                .find(a => /safesearch off/i.test(a.textContent)); return a ? a.href : ''; }""")
            if href:
                await page.goto(href, wait_until="domcontentloaded")
                await page.wait_for_timeout(random.randint(800, 1600))
        elif engine in NO_SAFE_SEARCH:
            return ctx.log(f"{engine} has no safe-search setting; nothing to turn off.")
        ctx.log(f"{engine} safe search: off.")
    except Exception as exc:
        ctx.log(f"Could not turn off {engine} safe search: {str(exc).splitlines()[0]}")


async def run(query: str, opts: CollectOptions, settings: Settings, sink: ImageSink, ctx: TaskContext) -> None:
    engine = opts.source
    direct_pin = query.startswith("http") and "pinterest." in query.lower()
    direct_ia = query.startswith("http") and "archive.org/details/" in query.lower()
    if direct_pin:
        engine = "Pinterest"
    elif direct_ia:
        engine = "Internet Archive"
    limit = int(opts.limit)
    sets = [{}] if (direct_pin or direct_ia) else combos(engine, opts.filters or {})
    urls_to_visit = [query] if (direct_pin or direct_ia) else [build_url(engine, query, c, opts.safe_search_off) for c in sets]
    if len(sets) > 1:
        ctx.log(f"{len(sets)} filter combinations; visiting them in random order.")

    async with open_browser(settings, ctx, prefer_system=(engine == "Yandex")) as (_browser, page):
        async def fetch(u: str):
            return await _fetch(page, u, *([fallbacks[u]] if u in fallbacks else []))

        async def download(raw: list[str], cap: int) -> None:
            """Fetch and save candidates until `cap` images are saved in total (0 = no cap)."""
            urls = [u for u in dict.fromkeys(raw) if u and u.startswith("http")
                    and not (JUNK_URL.search(u) and "archive.org/services/img/" not in u)]
            if opts.randomize:
                random.shuffle(urls)
            if raw:
                ctx.log(f"{len(urls)} candidate images found.")
            parallel, i = max(1, int(opts.parallel)), 0
            while i < len(urls) and not ctx.cancelled:
                if cap and sink.counts["saved"] >= cap:
                    break
                batch = [u for u in urls[i:i + parallel] if not sink.is_seen(u)]
                i += parallel
                for data, u in zip(await asyncio.gather(*(fetch(u) for u in batch)), batch):
                    if cap and sink.counts["saved"] >= cap:
                        break
                    if not data:
                        continue
                    name = f"image_{hashlib.md5(u.encode()).hexdigest()[:12]}"
                    status = await asyncio.to_thread(sink.ingest, data, name)
                    if status in ("saved", "duplicate", "rejected"):
                        sink.mark_seen(u)
                done = sink.counts["saved"]
                ctx.progress(done if limit else min(i, len(urls)), limit or len(urls), f"{done} images saved")

        fallbacks: dict = {}
        if opts.safe_search_off and engine != "Yandex" and not (direct_pin or direct_ia):
            await _safe_search_off(page, engine, query, ctx)
        if engine == "Yandex":
            await _yandex_safe_search(page, opts.safe_search_off, ctx)
            if sets == [{}]:                    # no filters chosen: walk our own variants for more than 30 results
                variants = YANDEX_VARIANTS[:]
                if opts.randomize:
                    random.shuffle(variants)
                urls_to_visit += [urls_to_visit[0] + v for v in variants]
        if opts.seeds:                          # visual search: only the 'visually similar' results, never a term search
            from .visual import similar_urls
            per_seed = -(-limit // len(opts.seeds)) if limit else 0
            want = max(40, per_seed * 6) if per_seed else 0      # candidates: some downloads always drop out
            for n, seed in enumerate(opts.seeds, 1):
                if ctx.cancelled or (limit and sink.counts["saved"] >= limit):
                    break
                ctx.log(f"Visual search {n}/{len(opts.seeds)}: {os.path.basename(seed) if not seed.startswith('http') else seed}")
                found = await similar_urls(page, engine, seed, want, ctx)
                await download(found, sink.counts["saved"] + per_seed if per_seed else 0)
            return
        ctx.progress(0, 0, "Loading page")
        ctx.log("Loading page...")
        await page.goto(urls_to_visit[0], wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(2500, 4000))
        if engine == "Yandex":
            if not await _wait_for_yandex_results(page, settings, ctx):
                if not ctx.cancelled:
                    await _debug_shot(page, ctx)
                    ctx.log("Yandex did not show any results.")
                return
            want = limit * (4 if opts.randomize else 2) if limit else 0     # randomize: draw from a wider pool
            raw, fallbacks = await _yandex_variants(page, urls_to_visit, want, ctx, settings)
            return await download(raw, limit)

        for n, (url, combo) in enumerate(zip(urls_to_visit, sets)):
            if ctx.cancelled or (limit and sink.counts["saved"] >= limit):
                break
            if n:
                await page.wait_for_timeout(random.randint(1500, 4000))
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                except Exception as exc:
                    ctx.log(f"Could not load a result page: {str(exc).splitlines()[0]}")
                    continue
                await page.wait_for_timeout(random.randint(1500, 3000))
            if len(sets) > 1:
                ctx.log(f"Filter set {n + 1}/{len(sets)}: {describe(engine, combo)}")
            share = -(-(limit - sink.counts["saved"]) // (len(sets) - n)) if limit else 0   # fair share of what is left
            ctx.progress(0, 0, "Scrolling for more results")
            await _auto_scroll(page, engine, share, ctx)
            raw, more = await _extract_urls(page, engine, query, direct_ia, share * 3, ctx)
            fallbacks.update(more)
            await download(raw, sink.counts["saved"] + share if limit else 0)
