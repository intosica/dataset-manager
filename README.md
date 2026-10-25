# Dataset Manager
<img width="1128" height="919" alt="Untitled" src="https://github.com/user-attachments/assets/4b9dc87f-a9c5-4f10-ad84-1b072f966c52" />

Collect, organize, caption and de-duplicate image datasets. A native Qt 6 desktop app that follows your
desktop theme on KDE Plasma and GNOME.

## Install

    python3 -m venv venv
    venv/bin/pip install -r requirements.txt
    venv/bin/playwright install chromium        # only if you have no Chrome/Chromium installed
    ./install-launcher.sh                       # optional: adds it to the application menu

Already have a venv with torch / spandrel / transformers? Create it as `venv/` (or `.venv/`) here, or copy it in,
and `run.sh` will use it. Then:

    ./run.sh

## Pages

| Page | What it does |
|---|---|
| Collect | Image search (Bing, DuckDuckGo, Yahoo, Yandex, Pinterest, Internet Archive) and card-art galleries (ArtofMTG, MTG Nexus) |
| Library | Per-collection statistics and thumbnail previews |
| Duplicates | Exact + near-duplicate finder; review groups, then move to Trash or delete |
| Maintenance | Optional steps: dedupe, sort, rename, JPEG, resize, low-quality flagging, captions |

Preferences (Ctrl+,) holds the library folder, browser, upscale model (native file dialog), model folders, resize
defaults and the caption template.

## Where things are stored

Settings `~/.config/dataset-manager/`, cache `~/.cache/dataset-manager/`, browser profile
`~/.local/share/dataset-manager/`. Create an empty file named `portable` in this folder to keep everything in `./data` instead.

Upscale models are searched in `./models`, `./upscaler`, and any folders added in Preferences.

## Tips

* GNOME: for matching widget styling install a Qt6 Adwaita style (`adwaita-qt6`, or `qt6-gtk-platformtheme`).
* The command line accepts `--library PATH`.
