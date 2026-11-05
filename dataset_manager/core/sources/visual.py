"""'Visually similar images' search by seed image (not reverse-image identification).

    similar_urls(page, engine, seed, cap, ctx) -> list[str]   # full-size image URLs, thumbnails only as fallback

The seed (local path or http(s) URL) is always *uploaded* through the engine's file input (URL seeds are downloaded
first): the engines' URL-based flows are unreliable (Bing's imgurl: link just bounces to the homepage).
Verified engines (Playwright Chromium, no system Chrome needed):
  Bing   - the upload lands on a web results page; the visual-match grid is /images/search?bcid=…&vsa=2, which keeps
           loading more tiles as you scroll (hundreds of candidates).
  Bing   - works headless and headed. Headless Chromium is refused ("Unable to process this search") unless the
           'Headless' token is stripped from the user agent, which _humanize() does through CDP.
  Yandex - 'Similar images' shows ~40 tiles; the orange 'Show more' button below the grid (usually hidden under the
           cookie banner, which we dismiss) loads another pageful, up to a dozen rounds. Works headless and headed in
           tests, but 'Show more' can trigger a robot check: then we keep what we have, log it and stop (a system
           Chrome via open_browser(prefer_system=True) and fewer seeds make it rarer).
Google Lens is not offered: it is consent/bot blocked.
Every failure is logged and yields [] (or the partial list) instead of raising.
"""
from __future__ import annotations

import mimetypes
import random
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import human
from .search import YANDEX_ITEM, YANDEX_JS, _wait_tiles

SIMILAR_ENGINES = ("Bing", "Yandex")


async def _payload(page, seed: str) -> dict:
    if seed.startswith(("http://", "https://")):
        res = await page.request.get(seed, timeout=20000)
        if not res.ok:
            raise RuntimeError(f"seed download failed (HTTP {res.status})")
        ext = mimetypes.guess_extension(res.headers.get("content-type", "").split(";")[0]) or ".jpg"
        return {"name": "seed" + ext, "mimeType": mimetypes.guess_type("x" + ext)[0] or "image/jpeg",
                "buffer": await res.body()}
    p = Path(seed)
    return {"name": p.name, "mimeType": mimetypes.guess_type(p.name)[0] or "image/jpeg", "buffer": p.read_bytes()}


async def _humanize(page) -> None:
    """Big viewport + user agent without 'HeadlessChrome' (Bing rejects uploads from headless UAs)."""
    if not await page.evaluate("/Headless/.test(navigator.userAgent)"):
        return                                        # headed: keep the real window size (no_viewport) and UA
    try:
        await page.set_viewport_size({"width": 1400, "height": 900})
        cdp = await page.context.new_cdp_session(page)
        ua = await page.evaluate("navigator.userAgent")
        await cdp.send("Network.setUserAgentOverride", {"userAgent": ua.replace("Headless", "")})
    except Exception:
        pass


def _q(url: str, key: str) -> str:
    return (parse_qs(urlparse(url).query).get(key) or [""])[0]


async def _click_more(page, selectors) -> None:
    """Press a 'show / view / see more' control if there is one (they only exist on some layouts)."""
    for sel in selectors:
        try:
            for el in await page.query_selector_all(sel):
                if await el.is_visible():
                    await human.click(page, el)
                    return
        except Exception:
            pass


async def _scroll_collect(page, extract, cap, ctx, more=()) -> list[str]:
    """Scroll (and press 'more' buttons) until `cap` URLs are known or the list stops growing."""
    best: list[str] = []
    stagnant = 0
    for _ in range(40):
        if ctx.cancelled or page.is_closed():
            break
        try:
            urls = list(dict.fromkeys(u for u in await extract() if u and u.startswith("http")))
        except Exception:
            urls = best                       # page navigated under us; keep what we have
        if len(urls) > len(best):
            best, stagnant = urls, 0
        else:
            stagnant += 1
        if cap and len(best) >= cap:
            break
        if stagnant >= 4:
            break
        for _ in range(random.randint(2, 4)):
            await human.scroll(page, random.randint(400, 900))
            await human.pause(0.1, 0.35)
        await _click_more(page, more)
        await page.wait_for_timeout(random.randint(700, 1500))
    return best


async def _dismiss_cookies(page) -> None:
    """Cookie banners sit on top of the results, including the 'Show more' button."""
    for label in ("Allow all", "Allow essential cookies"):
        try:
            btn = page.locator(f"button:has-text('{label}')").first
            if await btn.count() and await btn.is_visible():
                await human.click(page, btn)
                await human.pause(0.4, 0.9)
                return
        except Exception:
            pass


async def _bing(page, payload, cap, ctx):
    await page.goto("https://www.bing.com/images", wait_until="domcontentloaded")
    await human.click(page, page.locator("#sb_sbi"))
    await human.pause(0.4, 0.9)
    await page.set_input_files("input[type=file]", payload)
    try:
        await page.wait_for_url(re.compile(r"bcid="), timeout=30000)
    except Exception:
        ctx.log("Bing did not accept the image (still on the upload dialog).")
        return []
    # the upload lands on a *web* results page; the visual-match grid lives on the images endpoint
    await page.goto(f"https://www.bing.com/images/search?bcid={_q(page.url, 'bcid')}&vsa=2",
                    wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _dismiss_cookies(page)

    async def extract():
        hrefs = await page.evaluate("() => [...document.querySelectorAll('a[href*=\"mediaurl=\"]')].map(a => a.href)")
        return [_q(h, "mediaurl") or _q(h, "cdnurl") for h in hrefs]
    return await _scroll_collect(page, extract, cap, ctx, more=("a.btn_seemore",))


async def _yandex_open_similar(page, payload, ctx) -> bool:
    """Upload the seed and land on the 'Similar images' grid. Yandex refuses uploads now and then, so retry."""
    for attempt in range(3):
        if ctx.cancelled:
            return False
        try:
            await page.goto("https://yandex.com/images/", wait_until="domcontentloaded")
            await page.wait_for_timeout(random.randint(500, 1200))
            await human.click(page, page.locator(".HeaderDesktopActions-CbirButton"))
            await human.pause(0.4, 0.9)
            await page.set_input_files("input.CbirCore-FileInput", payload)
            await page.wait_for_url(re.compile(r"cbir_id="), timeout=30000)
        except Exception:
            if "captcha" in page.url.lower():
                ctx.log("Yandex asks for a robot check before the results; skipping this seed "
                        "(a headed browser or fewer seeds make that rarer).")
                return False
            if attempt == 2:
                return False
            continue
        await page.goto(re.sub(r"cbir_page=[^&]*", "cbir_page=similar", page.url), wait_until="domcontentloaded")
        if await _wait_tiles(page, 20):
            return True
    return False


async def _yandex(page, payload, cap, ctx):
    if not await _yandex_open_similar(page, payload, ctx):
        ctx.log("Yandex showed no similar images" + (" (robot check)." if "captcha" in page.url.lower() else "."))
        return []
    await _dismiss_cookies(page)
    urls: list[str] = []
    for _round in range(12):
        if ctx.cancelled or page.is_closed():
            break
        try:
            items = await page.evaluate(YANDEX_JS, YANDEX_ITEM)
        except Exception:
            items = []                       # page is reloading
        urls = list(dict.fromkeys(urls + [u for u in (o or p for o, p in items) if u]))
        if cap and len(urls) >= cap:
            break
        try:                                              # the button only renders near the end of the list
            for _ in range(random.randint(4, 7)):         # …and scroll there like a person would
                await human.scroll(page, random.randint(300, 700))
                await human.pause(0.2, 0.5)
            await human.pause(1.2, 2.5)
        except Exception:
            break
        more = page.locator(".FetchListButton").first     # the orange 'Show more' under the grid
        try:
            if not await more.count():
                break
            await more.scroll_into_view_if_needed(timeout=3000)
            before_url, before_n = page.url, len(urls)
            await human.click(page, more)
        except Exception:
            break
        grew = False
        for _ in range(12):
            await page.wait_for_timeout(1000)
            if "captcha" in page.url.lower():
                break
            if page.url != before_url:                        # next pageful loaded by navigation
                await _wait_tiles(page, 20)
                grew = True
                break
            try:                                             # …or appended in place
                if len(await page.evaluate(YANDEX_JS, YANDEX_ITEM)) > before_n:
                    grew = True
                    break
            except Exception:
                pass
        if "captcha" in page.url.lower():
            ctx.log(f"Yandex asked for a robot check on 'Show more'; keeping the {len(urls)} images found so far. "
                    "Fewer seed images (or a headed browser) make the check rarer.")
            break
        if not grew:
            break
    if not urls:
        ctx.log(f"Yandex returned no similar images (page: {page.url[:80]}).")
    return urls


async def similar_urls(page, engine: str, seed: str, cap: int, ctx) -> list[str]:
    fn = {"Bing": _bing, "Yandex": _yandex}.get(engine)
    if not fn:
        ctx.log(f"No visual-similarity search for {engine}; supported: {', '.join(SIMILAR_ENGINES)}.")
        return []
    try:
        await _humanize(page)
        urls = await fn(page, await _payload(page, seed), cap, ctx)
    except Exception as e:
        ctx.log(f"{engine} visual search failed: {str(e).splitlines()[0]}")
        return []
    out = list(dict.fromkeys(u for u in urls if u.startswith("http")))
    out = out[:cap] if cap else out
    ctx.log(f"{engine}: {len(out)} visually similar images.")
    return out
