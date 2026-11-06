"""Image sources. Each source feeds downloaded bytes into an ImageSink."""

SEARCH_ENGINES = ["Bing", "DuckDuckGo", "Yahoo", "Yandex", "Pinterest", "Internet Archive"]
ARTIST_GALLERIES = ["ArtofMTG", "MTG Nexus"]
BOARD_SOURCES = [
    ("danbooru", "Danbooru — imageboard", False),
    ("gelbooru", "Gelbooru — imageboard", False),
    ("rule34", "Rule34.xxx — imageboard", False),
    ("ygoprodeck", "YGOPRODeck — Yu-Gi-Oh! card art", False),
]
MATURE_KEYS = ("rule34",)          # hidden unless Settings.mature_content is on
BOARD_KEYS = tuple(k for k, _l, _a in BOARD_SOURCES)
BOARD_LABELS = {k: l.split(" — ")[0] for k, l, _a in BOARD_SOURCES}

# (key, label shown in the UI, takes an artist name instead of search terms)
SOURCES = (
    [(name, f"{name} — image search", False) for name in SEARCH_ENGINES]
    + BOARD_SOURCES
    + [("ArtofMTG", "ArtofMTG — card art by artist", True), ("MTG Nexus", "MTG Nexus — card art by artist", True)]
)


def visible_sources(mature: bool) -> list:
    return [s for s in SOURCES if mature or s[0] not in MATURE_KEYS]
