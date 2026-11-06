"""Pure checks for imageboard query building, caption ordering and caption sidecars. No network."""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from dataset_manager.core.sink import ImageSink, SinkOptions
from dataset_manager.core.sources import boards


def main() -> int:
    q = boards.BoardQuery("gelbooru", "cat_ears", "dog_ears", 50, "sensitive", "score", 100, "front")
    tags = boards._search_tags(q)
    assert "cat_ears" in tags and "score:>=50" in tags and "rating:sensitive" in tags
    assert "-dog_ears" in tags and "sort:score:desc" in tags
    assert boards.search_url(q, 0).endswith("pid=0")
    assert boards.search_url(boards.BoardQuery("danbooru", "cat_ears"), 42).endswith("page=2")
    dq = boards._search_tags(boards.BoardQuery("danbooru", "cat_ears", sort="score"))
    assert "order:score" in dq and "sort:score" not in dq
    assert boards._search_tags(boards.BoardQuery("gelbooru", "cat_ears, blue_eyes", "dog_ears")) == \
        "cat_ears blue_eyes -dog_ears"
    # Danbooru guests get 2 tags: the rest is filtered locally (and videos/gifs are always dropped).
    dq = boards.BoardQuery("danbooru", "cat solo", "dog", 5, "general")
    assert boards._search_tags(dq) == "cat solo"
    post = lambda **k: boards.BoardPost("danbooru", "1", "u", **{"tags": ["cat", "solo"], "score": 9, "rating": "g", **k})
    kept, _ = boards.apply_local([post(), post(tags=["cat", "solo", "dog"]), post(score=1), post(rating="e")],
                                 boards._split_terms(dq)[1])
    assert len(kept) == 1
    assert boards.is_animated(post(tags=["cat", "animated"])) and boards.is_animated(post(full_url="http://x/a.webm"))
    assert not boards.is_animated(post())
    from dataset_manager.core.sources import ygoprodeck
    u = ygoprodeck.api_url("", "Archfiend", {"attribute": ["attribute=DARK", "attribute=LIGHT"]}, 24)
    assert "archetype=Archfiend" in u and "attribute=DARK%2CLIGHT" in u and "offset=24" in u

    tags_, score, rating = boards._title_meta(" animal_ears cat_ears score:3 rating:sensitive")
    assert tags_ == ["animal_ears", "cat_ears"] and score == 3 and rating == "sensitive"

    sample = boards.BoardPost("gelbooru", "1", "u", tags=["animal_ears", "cat_ears", "blue_hair"])
    assert boards.caption_for(boards.BoardQuery("gelbooru", "cat_ears", caption_order="front"), sample) == \
        "cat_ears, animal_ears, blue_hair"
    assert boards.caption_for(boards.BoardQuery("gelbooru", "cat_ears", caption_order="back"), sample) == \
        "animal_ears, blue_hair, cat_ears"
    assert boards.caption_for(boards.BoardQuery("gelbooru", "cat_ears", caption_order="alpha"), sample) == \
        "animal_ears, blue_hair, cat_ears"

    with tempfile.TemporaryDirectory() as d:
        im = Image.new("RGB", (8, 8), "red")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        sink = ImageSink(d, "x", SinkOptions(min_side=0, write_captions=True, sort_by_orientation=False))
        sink.prepare()
        assert sink.ingest(buf.getvalue(), "board_1", "cat_ears, animal_ears") == "saved"
        sink.flush()
        assert Path(d, "board_1.jpg").exists()
        assert Path(d, "board_1.txt").read_text(encoding="utf-8") == "cat_ears, animal_ears"
    print("boards ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
