"""Self-check for the Yandex result extraction: both markup variants must yield the original URL."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from dataset_manager.core.browser import find_browser
from dataset_manager.core.sources.search import YANDEX_ITEM, YANDEX_JS

CURRENT = """
<div class="SerpItem"><a class="Link" href="https://yandex.com/images/search?pos=0&amp;img_url=https%3A%2F%2Fexample.com%2Fbig.jpg">
<img src="//avatars.mds.yandex.net/i?id=abc&amp;n=13"></a></div>
<div class="SerpItem"><a class="Link" href="/images/search?pos=1&amp;img_url=https%3A%2F%2Fexample.com%2Frel.jpg">
<img src="https://avatars.mds.yandex.net/i?id=rel&amp;n=13"></a></div>
"""
LEGACY = """
<div class="serp-item" data-bem='{"serp-item": {"img_href": "//example.com/old.png",
     "preview": [{"url": "https://avatars.mds.yandex.net/i?id=def"}]}}'></div>
"""


def main() -> int:
    with sync_playwright() as p:
        page = p.chromium.launch(executable_path=find_browser()).new_page()
        page.set_content(CURRENT + LEGACY)
        assert page.eval_on_selector_all(YANDEX_ITEM, "els => els.length") == 3, "current and legacy variants must match"
        found = {o: pr for o, pr in page.evaluate(YANDEX_JS, YANDEX_ITEM)}
        assert found.get("https://example.com/big.jpg") == "https://avatars.mds.yandex.net/i?id=abc&n=13", found
        assert found.get("https://example.com/rel.jpg") == "https://avatars.mds.yandex.net/i?id=rel&n=13", found
        assert found.get("https://example.com/old.png") == "https://avatars.mds.yandex.net/i?id=def", found
        print("ok:", found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
