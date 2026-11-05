"""Manual probe: ./venv/bin/python tests/probe_visual.py <Bing|Yandex> <seed path or url> [--headed] [--cap N]"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dataset_manager.core.browser import open_browser  # noqa: E402
from dataset_manager.core.settings import Settings  # noqa: E402
from dataset_manager.core.sources.visual import similar_urls  # noqa: E402
from dataset_manager.core.tasks import TaskContext  # noqa: E402


async def main(argv):
    engine, seed = argv[0], argv[1]
    cap = int(argv[argv.index("--cap") + 1]) if "--cap" in argv else 40
    settings = Settings(headless="--headed" not in argv)
    ctx = TaskContext(log=print)
    async with open_browser(settings, ctx, prefer_system=(engine == "Yandex")) as (_, page):
        urls = await similar_urls(page, engine, seed, cap, ctx)
    print(f"\n{len(urls)} urls")
    for u in urls:
        print(u)


if len(sys.argv) < 3:
    sys.exit(__doc__)
asyncio.run(main(sys.argv[1:]))
