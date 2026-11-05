"""'Visually similar images' search by seed image (not reverse-image identification).

    similar_urls(page, engine, seed, cap, ctx) -> list[str]   # full-size image URLs, thumbnails only as fallback

The seed (local path or http(s) URL) is always *uploaded* through the engine's file input (URL seeds are downloaded
first): the engines' URL-based flows are unreliable (Bing's imgurl: link just bounces to the homepage).
Verified engines (Playwright Chromium, no system Chrome needed):
  Bing   - works headless and headed. Headless Chromium is refused ("Unable to process this search") unless the
           'Headless' token is stripped from the user agent, which _humanize() does through CDP.
  Yandex - works headless and headed in tests; it may show a robot check on heavy use (then we return what we have;
           a real system Chrome via open_browser(prefer_system=True) and a headed window make that rarer).
Google Lens is not offered: it is consent/bot blocked.
Every failure is logged and yields [] (or the partial list) instead of raising.
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .search import YANDEX_ITEM, YANDEX_JS, _auto_scroll, _wait_tiles

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
    try:
        await page.set_viewport_size({"width": 1400, "height": 900})
        cdp = await page.context.new_cdp_session(page)
        ua = await page.evaluate("navigator.userAgent")
        await cdp.send("Network.setUserAgentOverride", {"userAgent": ua.replace("Headless", "")})
    except Exception:
        pass


def _q(url: str, key: str) -> str:
    return (parse_qs(urlparse(url).query).get(key) or [""])[0]


async def _bing(page, payload, cap, ctx):
    await page.goto("https://www.bing.com/images", wait_until="domcontentloaded")
    await page.click("#sb_sbi", timeout=15000)
    await page.set_input_files("input[type=file]", payload)
    try:
        await page.wait_for_url(re.compile(r"/search\?.*(SBIIRP|bcid)"), timeout=30000)
    except Exception:
        ctx.log("Bing did not accept the image (still on the upload dialog).")
        return []
    await page.goto(page.url + "&vsa=2", wait_until="domcontentloaded")      # the 'Visual Matches' tab
    await page.wait_for_timeout(3000)
    await _auto_scroll(page, "Bing", cap, ctx)
    hrefs = await page.evaluate("() => [...document.querySelectorAll('a[href*=\"mediaurl=\"]')].map(a => a.href)")
    return [_q(h, "mediaurl") or _q(h, "cdnurl") for h in hrefs]


async def _yandex(page, payload, cap, ctx):
    await page.goto("https://yandex.com/images/", wait_until="domcontentloaded")
    await page.click(".HeaderDesktopActions-CbirButton", timeout=15000)
    await page.set_input_files("input.CbirCore-FileInput", payload)
    try:
        await page.wait_for_url(re.compile(r"cbir_id="), timeout=30000)
    except Exception:
        ctx.log("Yandex did not accept the image" + (" (robot check?)" if "captcha" in page.url else "") + ".")
        return []
    await page.goto(re.sub(r"cbir_page=[^&]*", "cbir_page=similar", page.url), wait_until="domcontentloaded")
    if not await _wait_tiles(page, 20):
        ctx.log("Yandex showed no similar images" + (" (robot check)" if "captcha" in page.url else "") + ".")
        return []
    await _auto_scroll(page, "Yandex", cap, ctx)
    try:
        items = await page.evaluate(YANDEX_JS, YANDEX_ITEM)
    except Exception:
        return []
    return [o or p for o, p in items]


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
