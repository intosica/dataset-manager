"""Image sources. Each source feeds downloaded bytes into an ImageSink."""

SEARCH_ENGINES = ["Bing", "DuckDuckGo", "Yahoo", "Yandex", "Pinterest", "Internet Archive"]
ARTIST_GALLERIES = ["ArtofMTG", "MTG Nexus"]

# (key, label shown in the UI, takes an artist name instead of search terms)
SOURCES = (
    [(name, f"{name} — image search", False) for name in SEARCH_ENGINES]
    + [("ArtofMTG", "ArtofMTG — card art by artist", True), ("MTG Nexus", "MTG Nexus — card art by artist", True)]
)
