"""Browser discovery and a Playwright session helper."""
from __future__ import annotations

import glob
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from . import paths
from .settings import Settings
from .tasks import TaskContext

_PLAYWRIGHT_SEARCH = ["chromium-*/chrome-linux*/chrome", "chromium-*/chrome-win*/chrome.exe",
                      "chromium-*/chrome-mac*/*.app/Contents/MacOS/*"]


def configure_environment() -> None:
    """Use a Playwright browser folder shipped next to the app, if there is one."""
    local = paths.APP_DIR / "pw-browsers"
    if local.is_dir() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local)


def find_browser(configured: str = "", prefer_system: bool = False) -> str | None:
    """Explicit path → Playwright browser caches → system Chrome/Chromium → None (Playwright's own)."""
    explicit = paths.resolve(configured or os.environ.get("DATASET_MANAGER_BROWSER", ""))
    if explicit and os.path.isfile(explicit):
        return explicit
    if prefer_system:                      # a real Chrome looks far more "human" to bot checks
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
            found = shutil.which(name)
            if found:
                return found
    cache_dirs = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        cache_dirs.append(env)
    cache_dirs += [str(paths.APP_DIR / "pw-browsers"), str(Path.home() / ".cache" / "ms-playwright"),
                   str(Path.home() / "Library" / "Caches" / "ms-playwright")]
    for d in cache_dirs:
        for pat in _PLAYWRIGHT_SEARCH:
            for hit in sorted(glob.glob(os.path.join(d, pat)), reverse=True):
                if os.path.isfile(hit) and os.access(hit, os.X_OK):
                    return hit
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def has_display() -> bool:
    return not sys.platform.startswith("linux") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


async def apply_stealth(page) -> bool:
    try:
        from playwright_stealth import Stealth
        await Stealth().apply_stealth_async(page)
        return True
    except ImportError:
        try:
            from playwright_stealth import stealth_async
            await stealth_async(page)
            return True
        except ImportError:
            return False


@asynccontextmanager
async def open_browser(settings: Settings, ctx: TaskContext, prefer_system: bool = False):
    """Yields (context, page) for a persistent browser profile."""
    import random

    from playwright.async_api import async_playwright

    configure_environment()
    exe = find_browser(settings.browser_path, prefer_system)
    headless = settings.headless or not has_display()
    if headless and not settings.headless:
        ctx.log("No display detected; running the browser headless.")
    ctx.log(f"Browser: {exe or 'Playwright bundled Chromium'} ({'headless' if headless else 'windowed'})")
    async with async_playwright() as p:
        kwargs = dict(user_data_dir=settings.profile_path(), headless=headless,
                      viewport={"width": random.randint(1200, 1600), "height": random.randint(800, 1080)},
                      args=["--disable-blink-features=AutomationControlled"],
                      ignore_default_args=["--enable-automation"])
        if exe:
            kwargs["executable_path"] = exe
        try:
            browser = await p.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            raise RuntimeError(
                f"Could not start the browser: {str(e).splitlines()[0]}\n"
                "Close other windows using the same profile, or run `playwright install chromium`, "
                "or choose a browser in Preferences.") from e
        try:
            await browser.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page = browser.pages[0] if browser.pages else await browser.new_page()
            if not await apply_stealth(page):
                ctx.log("Tip: `pip install playwright-stealth` reduces bot detection.")
            yield browser, page
        finally:
            await browser.close()
