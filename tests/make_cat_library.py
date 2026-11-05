"""Build a throwaway demo library of CC0 cat photos, for screenshots and manual testing.

    python3 tests/make_cat_library.py [DEST]      # default /tmp/catlib

Then point the app at it:  ./run.sh --library /tmp/catlib   (or set it in Preferences).
Keeps private datasets out of screenshots. Needs only Pillow; images are CC0 from Wikimedia Commons.
"""

import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

from PIL import Image

UA = {"User-Agent": "dataset-manager-demo-library/1.0"}
TERMS = ["ginger cat street", "tabby cat", "cat portrait", "cat sleeping", "siamese cat", "kitten"]
TAGS = {"Ginger cat on the street": "cat, ginger, tabby, street, outdoors, whiskers, pavement",
        "Tabby cat with blue eyes": "cat, tabby, portrait, blue eyes, stripes, close-up",
        "Domestic shorthair cat portrait in grass": "cat, shorthair, portrait, grass, green, outdoors, bokeh",
        "Cat-sleeping-curled-up": "cat, sleeping, curled up, indoors, soft light, fur",
        "Siamese cat": "cat, siamese, portrait, blue eyes, creamy fur, studio",
        "Tet-Cat-Moomin2": "cat, indoors, sitting, bookshelf, collar"}


def search(term: str) -> list[dict]:
    query = urllib.parse.urlencode({"action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {term}",
                                    "gsrlimit": 10, "gsrnamespace": 6, "prop": "imageinfo",
                                    "iiprop": "url|size|extmetadata", "iiurlwidth": 1400, "format": "json"})
    req = urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + query, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        pages = json.load(r).get("query", {}).get("pages", {}) or {}
    return [p["imageinfo"][0] | {"title": p["title"].removeprefix("File:")} for p in pages.values()]


def main(dest: str) -> int:
    seen, done = set(), 0
    for term in TERMS:
        for info in search(term):
            if done >= 16 or info["title"] in seen:
                break
            if info["extmetadata"].get("LicenseShortName", {}).get("value") != "CC0":
                continue
            if min(info["width"], info["height"]) < 900:
                continue
            seen.add(info["title"])
            try:
                data = urllib.request.urlopen(urllib.request.Request(info.get("thumburl") or info["url"],
                                                                     headers=UA), timeout=60).read()
                img = Image.open(io.BytesIO(data)).convert("RGB")
            except OSError as exc:
                print(f"  skip {info['title']}: {exc}")
                continue
            w, h = img.size
            orient = "Portrait" if h > w * 1.05 else "Landscape" if w > h * 1.05 else "Square"
            collection = "Street Cats" if "Ginger cat on the street" in info["title"] else "Cat Portraits"
            folder = os.path.join(dest, collection, orient)
            os.makedirs(folder, exist_ok=True)
            base = re.sub(r"[^\w.-]+", "_", info["title"])[:70]
            path = os.path.join(folder, os.path.splitext(base)[0] + ".jpg")
            img.save(path, "JPEG", quality=92)
            tags = next((t for k, t in TAGS.items() if info["title"].startswith(k)), "cat, portrait, indoors")
            with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as f:
                f.write(tags + "\n")
            done += 1
            print(f"  {collection}/{orient}/{os.path.basename(path)}")
    print(f"{done} images in {dest}")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/catlib"))
