"""Per-engine search filters, mirroring each site's own filter menus.

A site lets you pick one option per filter; we let the user pick several and the scraper rotates through every
combination (one option from each filter group) in random order, so one run covers e.g. red + blue + wide + tall.
"""
from __future__ import annotations

import itertools
import random
from urllib.parse import quote

# Option value formats: "filterui:…" → Bing's qft, "iaf:key:val" → DuckDuckGo's iaf, anything else is a plain
# "param=value" query fragment. Colours carry a swatch (hex) so the UI can draw them like the sites do.
Option = tuple  # (label, value, swatch_hex | None)

_C = {"red": "#e53935", "orange": "#fb8c00", "yellow": "#fdd835", "green": "#43a047", "teal": "#00897b",
      "cyan": "#4dd0e1", "blue": "#1e88e5", "purple": "#8e24aa", "violet": "#8e24aa", "pink": "#f48fb1",
      "brown": "#8d6e63", "black": "#212121", "gray": "#9e9e9e", "white": "#fafafa"}


def _swatches(fmt: str, names, value_case=str.lower) -> list:
    return [(n.title(), fmt.format(value_case(n)), _C[n]) for n in names]


_BING_COLORS = ["red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "brown", "black", "gray", "white"]
_LICENSES = ["All Creative Commons", "Public domain", "Free to share and use", "Free to share and use commercially",
             "Free to modify, share and use", "Free to modify, share and use commercially"]

def _plain(param: str, names) -> list:
    return [(n, f"{param}={n}", None) for n in names]


# YGOPRODeck's filter funnel (card type, attribute, monster type, level, format). Ticked options of one group are OR'ed.
_YGO = [
    ("type", "Card type", _plain("type", ["Normal Monster", "Effect Monster", "Ritual Monster", "Fusion Monster",
                                          "Synchro Monster", "XYZ Monster", "Link Monster", "Pendulum Effect Monster",
                                          "Spell Card", "Trap Card"])),
    ("attribute", "Attribute", _plain("attribute", ["DARK", "LIGHT", "EARTH", "WATER", "FIRE", "WIND", "DIVINE"])),
    ("race", "Monster type / Spell & Trap type", _plain("race", [
        "Dragon", "Spellcaster", "Warrior", "Fiend", "Zombie", "Machine", "Aqua", "Pyro", "Beast", "Beast-Warrior",
        "Winged Beast", "Fairy", "Insect", "Dinosaur", "Reptile", "Fish", "Sea Serpent", "Rock", "Plant", "Thunder",
        "Psychic", "Wyrm", "Cyberse", "Divine-Beast", "Illusion", "Quick-Play", "Continuous", "Equip", "Field",
        "Counter", "Normal", "Ritual"])),
    ("level", "Level / Rank", [(str(n), f"level={n}", None) for n in range(1, 13)]),
    ("banlist", "Format", [("TCG", "banlist=TCG", None), ("OCG", "banlist=OCG", None), ("Goat", "banlist=Goat", None)]),
]

# engine -> [(group key, group label, [options])]
FILTERS: dict[str, list] = {
    "ygoprodeck": _YGO,
    "Bing": [
        ("size", "Image size", [("Small", "filterui:imagesize-small", None), ("Medium", "filterui:imagesize-medium", None),
                                ("Large", "filterui:imagesize-large", None), ("Extra large", "filterui:imagesize-wallpaper", None)]),
        ("color", "Color", [("Color only", "filterui:color2-color", None), ("Black & white", "filterui:color2-bw", None)]
         + _swatches("filterui:color2-FGcls_{}", _BING_COLORS, str.upper)),
        ("type", "Type", [("Photograph", "filterui:photo-photo", None), ("Clipart", "filterui:photo-clipart", None),
                          ("Line drawing", "filterui:photo-linedrawing", None), ("Animated GIF", "filterui:photo-animatedgif", None),
                          ("Transparent", "filterui:photo-transparent", None)]),
        ("layout", "Layout", [("Square", "filterui:aspect-square", None), ("Wide", "filterui:aspect-wide", None),
                              ("Tall", "filterui:aspect-tall", None)]),
        ("people", "People", [("Just faces", "filterui:face-face", None), ("Head & shoulders", "filterui:face-portrait", None)]),
        ("date", "Date", [("Past 24 hours", "filterui:age-lt1440", None), ("Past week", "filterui:age-lt10080", None),
                          ("Past month", "filterui:age-lt43200", None), ("Past year", "filterui:age-lt525600", None)]),
        ("license", "License", list(zip(_LICENSES, ["filterui:licenseType-Any", "filterui:license-L1",
                                                     "filterui:license-L2_L3_L4_L5_L6_L7", "filterui:license-L2_L3_L4",
                                                     "filterui:license-L2_L3_L5_L6", "filterui:license-L2_L3"], [None] * 6))),
    ],
    "Yahoo": [
        ("size", "Size", [("Small", "imgsz=small", None), ("Medium", "imgsz=medium", None), ("Large", "imgsz=large", None),
                          ("Extra large", "imgsz=wallpaper", None)]),
        ("color", "Color", [("Color only", "imgc=color", None), ("Black & white", "imgc=bw", None)]
         + _swatches("imgc={}", _BING_COLORS)),
        ("type", "Type", [("Photo", "imgty=photo", None), ("Clipart", "imgty=clipart", None),
                          ("Line drawing", "imgty=linedrawing", None), ("Animated GIF", "imgty=gif", None),
                          ("Transparent", "imgty=transparent", None)]),
        ("layout", "Layout", [("Square", "imga=square", None), ("Wide", "imga=wide", None), ("Tall", "imga=tall", None)]),
        ("people", "People", [("Faces only", "imgf=face", None), ("Head & shoulders", "imgf=portrait", None),
                              ("No people", "imgf=nonprofit", None)]),
        ("time", "Time", [("Past 24 hours", "imgt=day", None), ("Past week", "imgt=week", None),
                          ("Past month", "imgt=month", None), ("Past year", "imgt=year", None)]),
        ("license", "Usage rights", list(zip(_LICENSES, ["imgl=cc", "imgl=pd", "imgl=fsu", "imgl=fsuc", "imgl=fmsu",
                                                          "imgl=fmsuc"], [None] * 6))),
    ],
    "Yandex": [
        ("size", "Size", [("Large", "isize=large", None), ("Medium", "isize=medium", None), ("Small", "isize=small", None)]),
        ("orient", "Orientation", [("Horizontal", "iorient=horizontal", None), ("Vertical", "iorient=vertical", None),
                                   ("Square", "iorient=square", None)]),
        ("type", "Type", [("Photo", "type=photo", None), ("White background", "type=clipart", None),
                          ("Drawings & sketches", "type=lineart", None), ("People", "type=face", None),
                          ("Demotivators", "type=demotivator", None)]),
        ("color", "Color", [("Color images only", "icolor=color", None), ("Black & white", "icolor=gray", None)]
         + _swatches("icolor={}", ["red", "orange", "yellow", "cyan", "green", "blue", "violet", "white", "black"])),
        ("format", "Format", [("JPG", "itype=jpg", None), ("PNG", "itype=png", None), ("GIF", "itype=gifan", None)]),
    ],
    "DuckDuckGo": [
        ("size", "Size", [("Small", "iaf:size:Small", None), ("Medium", "iaf:size:Medium", None),
                          ("Large", "iaf:size:Large", None), ("Wallpaper", "iaf:size:Wallpaper", None)]),
        ("color", "Color", [("Color only", "iaf:color:color", None), ("Monochrome", "iaf:color:Monochrome", None)]
         + _swatches("iaf:color:{}", _BING_COLORS, str.title)),
        ("type", "Type", [("Photograph", "iaf:type:photo", None), ("Clipart", "iaf:type:clipart", None),
                          ("Animated GIF", "iaf:type:gif", None), ("Transparent", "iaf:type:transparent", None),
                          ("Line drawing", "iaf:type:line", None)]),
        ("layout", "Layout", [("Square", "iaf:layout:Square", None), ("Tall", "iaf:layout:Tall", None),
                              ("Wide", "iaf:layout:Wide", None)]),
        ("time", "Time", [("Past day", "df=d", None), ("Past week", "df=w", None), ("Past month", "df=m", None),
                          ("Past year", "df=y", None)]),
        ("license", "License", list(zip(_LICENSES, ["iaf:license:Any", "iaf:license:Public", "iaf:license:Share",
                                                     "iaf:license:ShareCommercially", "iaf:license:Modify",
                                                     "iaf:license:ModifyCommercially"], [None] * 6))),
    ],
    "Internet Archive": [
        ("mediatype", "Media type", [("Images", 'and[]=mediatype:"image"', None), ("Texts", 'and[]=mediatype:"texts"', None),
                                     ("Movies", 'and[]=mediatype:"movies"', None), ("Collections", 'and[]=mediatype:"collection"', None)]),
    ],
}

BASE_URLS = {
    "Bing": "https://www.bing.com/images/search?q={q}",
    "DuckDuckGo": "https://duckduckgo.com/?q={q}&iax=images&ia=images",
    "Yahoo": "https://images.search.yahoo.com/search/images?p={q}",
    "Yandex": "https://yandex.com/images/search?text={q}",
    "Pinterest": "https://www.pinterest.com/search/pins/?q={q}",
    "Internet Archive": "https://archive.org/search?query={q}",
}
# Applied when the user picked nothing in that group (dataset images are wanted big).
DEFAULTS = {"Bing": {"size": "filterui:imagesize-large"}, "Yandex": {"size": "isize=large"}}
# URL parameters that switch safe search off; Yandex and Yahoo also need a preference saved (see search.py).
SAFE_OFF = {"Bing": "adlt=off", "DuckDuckGo": "kp=-2", "Yahoo": "vm=p", "Yandex": ""}
NO_SAFE_SEARCH = {"Pinterest", "Internet Archive"}       # these sites have no safe-search switch for visitors
MAX_COMBOS = 64


def combos(engine: str, selected: dict) -> list[dict]:
    """Every pick of one chosen option per group, shuffled. [{}] when nothing is selected."""
    groups = [(key, [v for v in selected.get(key, []) if v in {o[1] for o in opts}])
              for key, _label, opts in FILTERS.get(engine, [])]
    groups = [(k, vals) for k, vals in groups if vals]
    out = [dict(zip([k for k, _ in groups], pick)) for pick in itertools.product(*(v for _, v in groups))]
    random.shuffle(out)
    return out[:MAX_COMBOS] or [{}]   # ponytail: random 64 of a huge product; nobody scrapes 1000 variants


def build_url(engine: str, query: str, combo: dict, safe_off: bool = False) -> str:
    url = BASE_URLS.get(engine, BASE_URLS["Bing"]).format(q=quote(query))
    values = list({**DEFAULTS.get(engine, {}), **combo}.values())
    qft = [v for v in values if v.startswith("filterui:")]
    iaf = [v[4:] for v in values if v.startswith("iaf:")]
    plain = [v for v in values if not v.startswith(("filterui:", "iaf:"))]
    if qft:
        url += "&qft=" + quote("".join("+" + v for v in qft), safe=":+")
    if iaf:
        url += "&iaf=" + quote(",".join(iaf), safe=":,")
    for v in plain:
        k, _, val = v.partition("=")
        url += f"&{k}={quote(val, safe='')}"
    if safe_off and SAFE_OFF.get(engine):
        url += "&" + SAFE_OFF[engine]
    return url


def describe(engine: str, combo: dict) -> str:
    labels = {o[1]: o[0] for _k, _l, opts in FILTERS.get(engine, []) for o in opts}
    return ", ".join(labels.get(v, v) for v in combo.values()) or "no filters"


if __name__ == "__main__":
    sel = {"color": ["filterui:color2-FGcls_RED", "filterui:color2-bw"], "layout": ["filterui:aspect-wide"]}
    cs = combos("Bing", sel)
    assert len(cs) == 2 and all(c["layout"] == "filterui:aspect-wide" for c in cs)
    u = build_url("Bing", "red fox", cs[0], safe_off=True)
    assert "qft=+filterui:imagesize-large+filterui:" in u and u.endswith("&adlt=off"), u
    assert build_url("DuckDuckGo", "x", {"a": "iaf:size:Large", "b": "iaf:color:Red", "t": "df=w"}).endswith(
        "&iaf=size:Large,color:Red&df=w")
    assert "and[]=mediatype%3A%22image%22" in build_url("Internet Archive", "x", {"m": 'and[]=mediatype:"image"'})
    assert combos("Pinterest", {"x": ["y"]}) == [{}]
    print("ok")
