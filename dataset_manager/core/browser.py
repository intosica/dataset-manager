"""Browser discovery and a Playwright session helper."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
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
    cache_dirs += [str(paths.data_dir() / "ms-playwright"), str(paths.APP_DIR / "pw-browsers"), str(Path.home() / ".cache" / "ms-playwright"),
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
        except Exception:
            return False
    except Exception:                       # a stealth bug must not stop the run
        return False


def _chrome_ua(exe: str | None) -> str:
    """A plain-Chrome user agent matching the installed browser (new headless otherwise says "HeadlessChrome")."""
    major = "131"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout if exe else ""
        m = re.search(r"(\d+)\.\d+\.\d+", out)
        major = m.group(1) if m else major
    except Exception:
        pass
    return (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")


@asynccontextmanager
async def open_browser(settings: Settings, ctx: TaskContext, prefer_system: bool = False, hidden: bool = False):
    """Yields (context, page) for a persistent browser profile.

    hidden=True runs the full browser with Chrome's "new headless" mode and a normal user agent: no window opens, and
    bot checks treat it like a windowed browser (the old headless shell gets a Cloudflare 403). Ignores settings.headless.
    """
    from playwright.async_api import async_playwright

    configure_environment()
    exe = find_browser(settings.browser_path, prefer_system)
    headless = False if hidden else (settings.headless or not has_display())
    if headless and not settings.headless:
        ctx.log("No display detected; running the browser headless.")
    ctx.log(f"Browser: {exe or 'Playwright bundled Chromium'} ({'hidden' if hidden else 'headless' if headless else 'windowed'})")
    async with async_playwright() as p:
        kwargs = dict(user_data_dir=settings.profile_path(), headless=headless,
                      no_viewport=True,   # real window size; an emulated viewport smaller than the window is a bot tell
                      args=["--disable-blink-features=AutomationControlled"],
                      ignore_default_args=["--enable-automation"])
        if hidden:
            kwargs.update(no_viewport=False, viewport={"width": 1366, "height": 900}, user_agent=_chrome_ua(exe))
            kwargs["args"] = kwargs["args"] + ["--headless=new"]
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
            page = browser.pages[0] if browser.pages else await browser.new_page()
            if not await apply_stealth(page):
                ctx.log("Tip: `pip install playwright-stealth` reduces bot detection.")
            yield browser, page
        finally:
            try:
                await browser.close()
            except Exception:
                pass                          # window already closed by the user
