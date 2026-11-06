"""YGOPRODeck card art. The site's own public card API does the filtering (plain HTTP, no browser needed);
we always take the "Cropped" illustration, never the full card scan."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

API = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
PAGE = 24                                       # the site's own "Limit 24"
UA = "dataset-manager (+https://github.com/intosica/dataset-manager)"


def _get(url: str, timeout: int = 40) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.read()


def api_url(name: str, archetype: str, filters: dict, offset: int) -> str:
    """filters = {group: ["param=value", ...]}; several ticked options in one group are OR'ed (comma-joined)."""
    params: dict[str, list[str]] = {}
    for values in filters.values():
        for v in values:
            k, _, val = v.partition("=")
            params.setdefault(k, []).append(val)
    q = {k: ",".join(v) for k, v in params.items()}
    if name.strip():
        q["fname"] = name.strip()
    if archetype.strip():
        q["archetype"] = archetype.strip()
    q.update(num=str(PAGE), offset=str(max(0, offset)), sort="name")
    return API + "?" + urllib.parse.urlencode(q)


def parse(data: dict, board: str, post_cls) -> list:
    posts = []
    for c in data.get("data", []):
        img = (c.get("card_images") or [{}])[0].get("image_url_cropped") or \
            f"https://images.ygoprodeck.com/images/cards_cropped/{c['id']}.jpg"
        tags = [c["name"]] + [str(c[k]) for k in ("type", "attribute", "race", "archetype") if c.get(k)]
        if c.get("level"):
            tags.append(f"Level {c['level']}")
        posts.append(post_cls(board, str(c["id"]), img, thumb_url=img, full_url=img, tags=tags,
                              width=624, height=624))
    return posts


def thumb(url: str) -> str:
    cache = os.path.join(tempfile.gettempdir(), "dataset-manager-board-thumbs")
    os.makedirs(cache, exist_ok=True)
    dest = os.path.join(cache, hashlib.sha1(url.encode()).hexdigest() + ".jpg")
    if not os.path.isfile(dest):
        with open(dest + ".part", "wb") as f:
            f.write(_get(url))
        os.replace(dest + ".part", dest)
    return dest


def search(url: str) -> tuple[dict, int | None]:
    try:
        data = json.loads(_get(url))
    except urllib.error.HTTPError as e:                 # the API answers 400 for "no card matched"
        if e.code == 400:
            return {"data": []}, None
        raise
    return data, (data.get("meta") or {}).get("next_page_offset")
