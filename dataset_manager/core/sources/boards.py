"""Imageboard sources: Danbooru, Gelbooru and Rule34.xxx.

The list pages already carry the tags, score and rating, so a search is one Playwright page load. The browser is
used instead of requests because Danbooru/Rule34 put a Cloudflare/CAPTCHA challenge in front of plain HTTP clients.
It always runs hidden (new headless mode, normal user agent), whatever the Headless preference says.
Full images are fetched with the browser context's request object, which shares cookies and the user agent.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse

from . import BOARD_LABELS, ygoprodeck

RATING = {"": "", "general": "general", "sensitive": "sensitive", "questionable": "questionable", "explicit": "explicit"}
DANBOORU_RATING = {"": "", "general": "g", "sensitive": "s", "questionable": "q", "explicit": "e"}
SORTS = {"new": "Newest", "score": "Highest score", "random": "Random"}
CAPTION_ORDERS = [
    ("default", "Board order"),
    ("front", "Search tags first"),
    ("back", "Search tags last"),
    ("alpha", "Alphabetical"),
]
CAPTION_ORDER_KEYS = tuple(k for k, _l in CAPTION_ORDERS)


@dataclass
class BoardQuery:
    board: str
    positive: str = ""
    negative: str = ""
    score: int = 0
    rating: str = ""
    sort: str = "new"
    limit: int = 0
    caption_order: str = "default"
    archetype: str = ""                       # YGOPRODeck only
    filters: dict = field(default_factory=dict)   # YGOPRODeck only: {group: ["param=value"]}

    @property
    def label(self) -> str:
        return BOARD_LABELS.get(self.board, self.board)


@dataclass
class BoardPost:
    board: str
    post_id: str
    page_url: str
    thumb_url: str = ""
    full_url: str = ""
    tags: list[str] = field(default_factory=list)
    score: int | None = None
    rating: str = ""
    width: int = 0
    height: int = 0
    thumb_file: str = ""


@dataclass
class BoardPage:
    board: str
    offset: int
    results: list[BoardPost]
    next_offset: int | None = None
    notice: str = ""


# ---------------------------------------------------------------- query -> URL
ANIMATED_TAGS = {"animated", "video", "animated_gif", "animated_png", "webm", "mp4", "sound", "has_audio", "flash"}
DANBOORU_TAG_LIMIT = 2          # anonymous Danbooru accounts may search at most two tags


def _terms(q: BoardQuery) -> list[str]:
    """Every filter as one search term, most important first: positive tags, rating, score, then exclusions."""
    positive = [t for t in re.split(r"[,\s]+", q.positive.strip()) if t]
    low = " ".join(positive).lower()
    parts = list(positive)
    if q.rating and "rating:" not in low:
        parts.append(f"rating:{DANBOORU_RATING[q.rating] if q.board == 'danbooru' else RATING[q.rating]}")
    if q.score and "score:" not in low:
        parts.append(f"score:>={q.score}")
    seen = set()
    for tok in re.split(r"[,\s]+", q.negative.strip()):
        tok = tok.lstrip("-")
        if tok and tok.casefold() not in seen:
            seen.add(tok.casefold())
            parts.append("-" + tok)
    return parts


def _split_terms(q: BoardQuery) -> tuple[list[str], list[str]]:
    """(terms the board gets, terms we apply ourselves). Only Danbooru has a hard tag cap."""
    terms = _terms(q)
    if q.board == "danbooru":
        return terms[:DANBOORU_TAG_LIMIT], terms[DANBOORU_TAG_LIMIT:]
    return terms, []


def _search_tags(q: BoardQuery) -> str:
    parts = _split_terms(q)[0]
    low = q.positive.lower()
    if q.sort in ("score", "random") and "sort:" not in low and "order:" not in low:
        if q.board == "danbooru":
            parts.append("order:" + q.sort)
        else:
            parts.append("sort:score:desc" if q.sort == "score" else "sort:random")
    return " ".join(parts)


def _matches(post: BoardPost, term: str) -> bool | None:
    """Does the post satisfy one search term? None when the term cannot be checked from a list page."""
    neg = term.startswith("-")
    t = term.lstrip("-")
    low = t.lower()
    if low.startswith("score:"):
        m = re.fullmatch(r"score:(>=|<=|>|<|=)?(-?\d+)", low)
        if not m or post.score is None:
            return None
        n, op = int(m.group(2)), m.group(1) or "="
        ok = {">=": post.score >= n, "<=": post.score <= n, ">": post.score > n, "<": post.score < n, "=": post.score == n}[op]
    elif low.startswith("rating:"):
        if not post.rating:
            return None
        ok = post.rating[:1].lower() == low.split(":", 1)[1][:1]
    elif ":" in low or "*" in low or low.startswith("~"):
        return None
    else:
        ok = low in {x.lower() for x in post.tags}
    return (not ok) if neg else ok


def apply_local(posts: list[BoardPost], terms: list[str]) -> tuple[list[BoardPost], int]:
    """Filter by the terms the board couldn't take. Returns (kept, count of terms that couldn't be checked)."""
    skipped = sum(1 for t in terms if all(_matches(p, t) is None for p in posts[:1]))
    kept = [p for p in posts if all(_matches(p, t) is not False for t in terms)]
    return kept, skipped


def is_animated(post: BoardPost) -> bool:
    ext = os.path.splitext(urlparse(post.full_url).path)[1].lower()
    return ext in (".gif", ".webm", ".mp4", ".zip", ".swf") or bool({t.lower() for t in post.tags} & ANIMATED_TAGS)


def search_url(q: BoardQuery, offset: int) -> str:
    tags = quote(_search_tags(q), safe="")
    if q.board == "danbooru":
        page = max(0, offset) // 42 + 1
        return f"https://danbooru.donmai.us/posts?tags={tags}&limit=42&page={page}"
    if q.board == "gelbooru":
        return f"https://gelbooru.com/index.php?page=post&s=list&tags={tags}&pid={max(0, offset)}"
    if q.board == "rule34":
        return f"https://rule34.xxx/index.php?page=post&s=list&tags={tags}&pid={max(0, offset)}"
    raise ValueError(f"Unknown imageboard: {q.board}")


# ---------------------------------------------------------------- parsing
def _title_meta(title: str, alt: str = "") -> tuple[list[str], int | None, str]:
    tags, score, rating = [], None, ""
    for tok in (title or "").split():
        low = tok.lower()
        if low.startswith("score:"):
            try:
                score = int(tok.split(":", 1)[1])
            except ValueError:
                pass
        elif low.startswith("rating:"):
            rating = tok.split(":", 1)[1]
        else:
            tags.append(tok)
    if not tags and alt:
        # Gelbooru's alt is "Rule 34 | tag one, tag two"; Rule34's is comma-separated tags.
        tail = alt.split("|", 1)[-1]
        tags = [t.strip().replace(" ", "_") for t in tail.split(",") if t.strip()]
    return tags, score, rating


def _num(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def _attr(el, name: str) -> str:
    return (await el.get_attribute(name) or "") if el else ""


async def _parse_list(board: str, page) -> list[BoardPost]:
    posts: list[BoardPost] = []
    if board == "danbooru":
        for art in await page.query_selector_all("article.post-preview, article.thumbnail-preview"):
            a = await art.query_selector("a[href*='/posts/']")
            href = await _attr(a, "href")
            m = re.search(r"/posts/(\d+)", href)
            pid = await _attr(art, "data-id") or (m.group(1) if m else "")
            if not pid:
                continue
            tags = (await _attr(art, "data-tags")).split()
            img = await art.query_selector("img")
            posts.append(BoardPost(
                board, pid, urljoin(page.url, href),
                thumb_url=urljoin(page.url, await _attr(img, "src")),
                full_url=await _attr(art, "data-file-url"),
                tags=tags, score=_num(await _attr(art, "data-score")),
                rating=await _attr(art, "data-rating"),
                width=_num(await _attr(art, "data-image-width")),
                height=_num(await _attr(art, "data-image-height")),
            ))
    else:                                           # gelbooru / rule34
        selector = "article.thumbnail-preview" if board == "gelbooru" else "span.thumb"
        for art in await page.query_selector_all(selector):
            a = await art.query_selector("a[id^='p']")
            pid = (await _attr(a, "id")).removeprefix("p")
            if not pid:
                continue
            img = await art.query_selector("img")
            tags, score, rating = _title_meta(await _attr(img, "title"), await _attr(img, "alt"))
            posts.append(BoardPost(
                board, pid, urljoin(page.url, await _attr(a, "href")),
                thumb_url=urljoin(page.url, await _attr(img, "src")),
                tags=tags, score=score, rating=rating,
            ))
    return posts


async def _next_offset(board: str, page, offset: int, count: int) -> int | None:
    if not count:
        return None
    href = ""
    for sel in ("a[rel='next']", "a.next_page", "a.nextlink", "a[alt='next']", "link[rel='next']"):
        el = await page.query_selector(sel)
        href = await _attr(el, "href")
        if href:
            break
    m = re.search(r"[?&](pid|page)=(\d+)", href)
    if m:
        n = int(m.group(2))
        if m.group(1) == "page":
            return max(0, (n - 1) * 42)
        return n
    if count >= 40:
        return offset + count
    return None


def _blocked(board: str, title: str, body: str, status: int) -> str:
    name = BOARD_LABELS.get(board, board)
    low = (title or "").lower()
    if "just a moment" in low or "security verification" in low or "attention required" in low:
        return (f"{name} is showing a Cloudflare \"verify you are human\" check, which can't be completed while the "
                "browser is hidden. Try again later or use another board (Gelbooru usually works).")
    if "captcha" in low:
        return f"{name} asked for a CAPTCHA, which can't be solved while the browser is hidden. Try another board."
    if status == 503:
        return f"{name} is unavailable right now (HTTP 503). Try again later."
    if status in (401, 403, 429):
        return f"{name} refused the request (HTTP {status}). Wait a bit, or try another board."
    return ""


async def _settle(page) -> None:
    try:
        await page.wait_for_selector("article.post-preview, article.thumbnail-preview, span.thumb",
                                     timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(1000)


# ---------------------------------------------------------------- public async API
async def _fetch_thumbs(context, posts: list[BoardPost], ctx) -> None:
    """One browser request per thumbnail, reusing the search's cookies/UA. Failures are cosmetic."""
    if not any(p.thumb_url for p in posts):
        return
    cache = os.path.join(tempfile.gettempdir(), "dataset-manager-board-thumbs")
    os.makedirs(cache, exist_ok=True)
    for post in posts:
        if ctx.cancelled:
            break
        if not post.thumb_url:
            continue
        ext = os.path.splitext(urlparse(post.thumb_url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        dest = os.path.join(cache, hashlib.sha1(post.thumb_url.encode()).hexdigest() + ext)
        if os.path.isfile(dest):
            post.thumb_file = dest
            continue
        try:
            r = await context.request.get(post.thumb_url, headers={"Referer": post.page_url}, timeout=30000)
            if not r.ok:
                continue
            data = await r.body()
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            post.thumb_file = dest
        except Exception as exc:
            ctx.log(f"  thumbnail {post.post_id}: {exc}")


async def _search_page(q: BoardQuery, offset: int, settings, ctx, thumbs: bool = True) -> BoardPage:
    from ..browser import open_browser
    url = search_url(q, offset)
    ctx.log(f"{q.label}: {url}")
    async with open_browser(settings, ctx, hidden=True) as (context, page):
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _settle(page)
        body = await page.content()
        title = await page.title()
        blocked = _blocked(q.board, title, body, resp.status if resp else 0)
        if blocked:
            raise RuntimeError(blocked)
        results = await _parse_list(q.board, page)
        nxt = await _next_offset(q.board, page, offset, len(results))
        raw = len(results)
        results = [r for r in results if not is_animated(r)]
        notes = []
        if raw - len(results):
            notes.append(f"{raw - len(results)} video/animated post(s) hidden.")
        results, skipped = apply_local(results, _split_terms(q)[1])
        if q.board == "danbooru" and len(_terms(q)) > DANBOORU_TAG_LIMIT:
            notes.append(f"Danbooru only searches {DANBOORU_TAG_LIMIT} tags for guests; the other filters are applied "
                         "to each page here, so pages can look sparse." + (f" {skipped} filter(s) can't be checked and are ignored." if skipped else ""))
        elif raw == 0 and len(_terms(q)) > 6:
            notes.append(f"No results — {q.label} may limit how many tags a search can use. Try fewer tags.")
        if thumbs:
            await _fetch_thumbs(context, results, ctx)
        ctx.log(f"{q.label}: {len(results)} results on this page.")
        return BoardPage(q.board, offset, results, nxt, " ".join(notes))


async def _full_from_page(page, board: str) -> str:
    selectors = ["a:has-text('Original image')", "a:has-text('File Only')"]
    for sel in selectors:
        el = await page.query_selector(sel)
        href = await _attr(el, "href")
        if href:
            return urljoin(page.url, href)
    for sel in ("img#image", "img#main_image"):
        el = await page.query_selector(sel)
        href = await _attr(el, "src")
        if href:
            return urljoin(page.url, href)
    return ""


async def _image_data(context, page, post: BoardPost) -> bytes:
    full = post.full_url
    if not full:
        await page.goto(post.page_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(500)
        full = await _full_from_page(page, post.board)
        if not full:
            raise RuntimeError(f"Could not find the full image on {post.page_url}")
    r = await context.request.get(full, headers={"Referer": post.page_url}, timeout=60000)
    if not r.ok:
        raise RuntimeError(f"Image download failed: HTTP {r.status} ({full})")
    ctype = r.headers.get("content-type", "")
    if ctype and not ctype.startswith("image/"):
        raise RuntimeError(f"The imageboard returned {ctype} instead of an image.")
    return await r.body()


async def _fetch_image(post: BoardPost, settings, ctx) -> bytes:
    from ..browser import open_browser
    async with open_browser(settings, ctx, hidden=True) as (context, page):
        return await _image_data(context, page, post)


# ---------------------------------------------------------------- sync wrappers
def _ygo_page(q: BoardQuery, offset: int, ctx, thumbs: bool) -> BoardPage:
    data, nxt = ygoprodeck.search(ygoprodeck.api_url(q.positive, q.archetype, q.filters, offset))
    results = ygoprodeck.parse(data, q.board, BoardPost)
    if thumbs:
        for r in results:
            if ctx.cancelled:
                break
            try:
                r.thumb_file = ygoprodeck.thumb(r.thumb_url)
            except Exception as exc:
                ctx.log(f"  thumbnail {r.post_id}: {exc}")
    ctx.log(f"{q.label}: {len(results)} cards on this page.")
    return BoardPage(q.board, offset, results, nxt)


def search_page(q: BoardQuery, offset: int, settings, ctx, thumbs: bool = True) -> BoardPage:
    if q.board == "ygoprodeck":
        try:
            return _ygo_page(q, offset, ctx, thumbs)
        except Exception as exc:
            raise RuntimeError(f"YGOPRODeck search failed: {exc}") from exc
    try:
        return asyncio.run(_search_page(q, offset, settings, ctx, thumbs))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{q.label} search failed: {type(exc).__name__}: {exc}") from exc


def fetch_image(post: BoardPost, settings, ctx) -> bytes:
    if post.board == "ygoprodeck":
        try:
            return ygoprodeck._get(post.full_url)
        except Exception as exc:
            raise RuntimeError(f"YGOPRODeck image failed: {exc}") from exc
    try:
        return asyncio.run(_fetch_image(post, settings, ctx))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{post.board} image failed: {type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------- captions / saving
def _search_words(q: BoardQuery) -> list[str]:
    words = []
    for tok in re.split(r"[,\s]+", q.positive.strip()):
        if not tok or tok.startswith("-") or ":" in tok:
            continue
        tok = tok.lstrip("~")
        if tok and tok.casefold() not in {w.casefold() for w in words}:
            words.append(tok)
    return words


def caption_for(q: BoardQuery, post: BoardPost) -> str:
    """The board's own tags, with the search terms ordered how the user asked."""
    tags = list(dict.fromkeys(post.tags))
    if not tags:
        return ""
    order = q.caption_order if q.caption_order in CAPTION_ORDER_KEYS else "default"
    if order == "default":
        picked = tags
    else:
        search = [t for t in _search_words(q)
                  if any(t.casefold() == x.casefold() for x in tags)]
        used = {s.casefold() for s in search}
        rest = [t for t in tags if t.casefold() not in used]
        if order == "alpha":
            picked = sorted(tags, key=str.casefold)
        elif order == "front":
            picked = search + sorted(rest, key=str.casefold)
        else:
            picked = sorted(rest, key=str.casefold) + search
    return ", ".join(picked)


def save_result(q: BoardQuery, post: BoardPost, sink, settings, ctx, want_caption: bool) -> str:
    if sink.is_seen(post.page_url):
        return "already saved"
    name = f"{post.board}_{post.post_id}"
    if sink.has_name(name):
        return "already saved"
    data = fetch_image(post, settings, ctx)
    caption = caption_for(q, post) if want_caption else ""
    status = sink.ingest(data, name, caption)
    if status == "saved":
        sink.mark_seen(post.page_url)
    return status


async def _save_many(q: BoardQuery, posts: list[BoardPost], sink, settings, ctx, want_caption: bool) -> None:
    from ..browser import open_browser
    async with open_browser(settings, ctx, hidden=True) as (context, page):
        for i, post in enumerate(posts):
            if ctx.cancelled:
                break
            if sink.is_seen(post.page_url) or sink.has_name(f"{post.board}_{post.post_id}"):
                status = "already saved"
            else:
                try:
                    data = await _image_data(context, page, post)
                    caption = caption_for(q, post) if want_caption else ""
                    status = sink.ingest(data, f"{post.board}_{post.post_id}", caption)
                    if status == "saved":
                        sink.mark_seen(post.page_url)
                except Exception as exc:
                    status = f"failed ({type(exc).__name__}: {exc})"
                    ctx.log(f"  {post.page_url}: {exc}")
            ctx.log(f"{i + 1}/{len(posts)} {post.board} #{post.post_id}: {status}")
            ctx.progress(i + 1, len(posts), f"Saved {i + 1} / {len(posts)}")


def save_all(q: BoardQuery, posts: list[BoardPost], sink, settings, ctx, want_caption: bool, limit: int = 0) -> None:
    todo = posts[:limit] if limit else posts
    if not todo:
        return
    if q.board == "ygoprodeck":
        for i, post in enumerate(todo):
            if ctx.cancelled:
                break
            try:
                status = save_result(q, post, sink, settings, ctx, want_caption)
            except Exception as exc:
                status = f"failed ({exc})"
            ctx.log(f"{i + 1}/{len(todo)} {post.board} #{post.post_id}: {status}")
            ctx.progress(i + 1, len(todo), f"Saved {i + 1} / {len(todo)}")
        return
    try:
        asyncio.run(_save_many(q, todo, sink, settings, ctx, want_caption))
    except Exception as exc:
        raise RuntimeError(f"Saving the imageboard results failed: {type(exc).__name__}: {exc}") from exc
