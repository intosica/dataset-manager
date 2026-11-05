"""Human-ish input: curved pointer paths, jittered clicks and uneven wheel scrolling.

Engines notice a perfectly regular scroll or a click on the exact centre of a button; these helpers spend a few
hundred milliseconds looking sloppier, which keeps bot checks (Yandex's especially) rarer. Nothing here is a
guarantee — it only removes the mechanical tells.
"""
from __future__ import annotations

import asyncio
import math
import random


async def pause(lo: float = 0.12, hi: float = 0.45) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)                      # smoothstep: slow out, fast middle, slow in


async def move_to(page, x: float, y: float) -> None:
    """Glide the pointer there along a slightly bowed path instead of teleporting it."""
    sx, sy = getattr(page, "_human_xy", None) or (random.uniform(80, 400), random.uniform(80, 300))
    dx, dy = x - sx, y - sy
    dist = math.hypot(dx, dy) or 1.0
    steps = max(4, min(24, int(dist / 35)))
    bow = random.uniform(-0.22, 0.22) * dist
    cx, cy = (sx + x) / 2 - dy / dist * bow, (sy + y) / 2 + dx / dist * bow
    for i in range(1, steps + 1):
        t = _ease(i / steps)
        bx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t * t * x
        by = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t * t * y
        try:
            await page.mouse.move(bx + random.uniform(-1.5, 1.5), by + random.uniform(-1.5, 1.5))
        except Exception:
            return                                   # window closed / page gone
        await asyncio.sleep(random.uniform(0.008, 0.03))
    page._human_xy = (x, y)


async def click(page, target, timeout: float = 8000) -> bool:
    """Move to a random spot inside the element, hover a moment, then press. False if it went away."""
    try:
        await target.scroll_into_view_if_needed(timeout=3000)
        box = await target.bounding_box()
    except Exception:
        box = None
    if not box or box["width"] < 4 or box["height"] < 4:
        try:
            await target.click(timeout=timeout)
            return True
        except Exception:
            return False
    x = box["x"] + box["width"] * random.uniform(0.32, 0.68)
    y = box["y"] + box["height"] * random.uniform(0.30, 0.70)
    await move_to(page, x, y)
    await pause(0.06, 0.22)
    try:
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.13))
        await page.mouse.up()
    except Exception:
        return False
    await pause(0.05, 0.18)
    return True


async def scroll(page, dy: float) -> None:
    """Wheel down in a few uneven nudges from a random spot, with the odd short glance back up."""
    try:
        w, h = await page.evaluate("[innerWidth, innerHeight]")
        mx, my = random.uniform(0.25 * w, 0.75 * w), random.uniform(0.3 * h, 0.7 * h)
        await move_to(page, mx, my)                   # keeps the pointer inside the page on small windows
        await asyncio.sleep(random.uniform(0.04, 0.12))
    except Exception:
        try:                                          # no mouse (closed window): fall back to the DOM
            await page.evaluate("(d) => window.scrollBy(0, d)", dy)
        except Exception:
            pass
        return
    left = float(dy)
    while left > 0:
        step = min(left, random.randint(120, 420))
        try:
            await page.mouse.wheel(0, step)
        except Exception:
            return
        left -= step
        await asyncio.sleep(random.uniform(0.06, 0.22))
        if random.random() < 0.12:                     # tiny scroll back, like re-reading
            try:
                await page.mouse.wheel(0, -random.randint(25, 90))
            except Exception:
                return
            await asyncio.sleep(random.uniform(0.1, 0.3))
