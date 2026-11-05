<p align="center">
  <img src="dataset_manager/resources/dataset-manager.svg" alt="Dataset Manager icon" width="128">
</p>

<h1 align="center">Dataset Manager</h1>

<p align="center">Collect, organize, caption and de-duplicate image datasets.<br>
A native Qt 6 desktop app that follows your desktop theme on KDE Plasma, GNOME and COSMIC.</p>

<p align="center"><img width="1128" height="919" alt="Untitled" src="https://github.com/user-attachments/assets/4b9dc87f-a9c5-4f10-ad84-1b072f966c52" /></p>

## Install

    python3 -m venv venv
    venv/bin/pip install -r requirements.txt
    venv/bin/playwright install chromium        # only if you have no Chrome/Chromium installed
    ./install-launcher.sh                       # optional: adds it to the application menu

Already have a venv with torch / spandrel / transformers? Create it as `venv/` (or `.venv/`) here, or copy it in,
and `run.sh` will use it. Then:

    ./run.sh

### Flatpak (X11 / Xorg, e.g. COSMIC on Xorg)

    ./build-flatpak.sh                  # build + install for your user, run again whenever you want to update
    flatpak run io.github.intosica.DatasetManager

Needs `flatpak` and `flatpak-builder`; the script installs the freedesktop 25.08 runtime/SDK from Flathub if
missing. Python packages and Playwright's Chromium are downloaded during the build (several GB of build cache in
`~/.cache/dataset-manager-flatpak`, override with `FLATPAK_BUILD_DIR`). Options:

| Option | Effect |
|---|---|
| `--extras "pkg …"` | pip packages baked in. Default `"imageprism onnxruntime transformers"` (the light ONNX similarity models: imageprism and imgbeddings); add `torch transformers open_clip_torch torchvision` for TinyCLIP / OpenCLIP / MobileNet, `torch spandrel` for AI upscaling |
| `--bundle` | also writes a single-file `dataset-manager.flatpak` |
| `--clean` | wipes the build cache first |

The flatpak can read and write your home folder, `/media`, `/run/media` and `/mnt`. Its settings and sessions live in
`~/.var/app/io.github.intosica.DatasetManager/`, separate from the venv version.

## Layout

The pages are tabs along the top (Ctrl+1 … Ctrl+5):

| Page | What it does |
|---|---|
| Collect | Image search (Bing, DuckDuckGo, Yahoo, Yandex, Pinterest, Internet Archive) and card-art galleries (ArtofMTG, MTG Nexus) |
| Library | Per-collection statistics and thumbnail previews |
| Duplicates | Exact + near-duplicate finder; review groups, then move to Trash or delete |
| Maintenance | Optional steps: dedupe, sort, rename, JPEG, resize, low-quality flagging, captions |
| Tag Tools | Bulk caption / tag editing |

The left side holds your **sessions**: saved Collect parameters (source, query, filters, options, similarity filter).

* Starting a collection with no session selected saves it as a new one; while a session is selected every change is saved into it.
* Ctrl+N or the New-session button above the list saves the current settings as a session; click a session to load it.
* Drag to reorder or to move into folders, double-click (or F2) to rename, right-click to duplicate / delete, and sort A–Z.

## Collecting

* **Search filters** follow the selected engine (size, color, type, layout, people, date, license, …). Unlike on the
  website you can tick several options per filter: the scraper visits every combination in random order
  (a random 64 if there are more) and splits the image budget between them.
* **Turn off safe search** works for Bing, DuckDuckGo, Yahoo and Yandex.
* **Sort into Portrait / Landscape / Square folders** can be switched off to save everything into the collection folder.
* **Keep only images that look like my examples** — a per-session similarity filter. Point it at a folder of images
  to go for and (optionally) a folder of images to avoid. Each download is embedded and scored as
  `mean similarity to "look like" − mean similarity to "don't look like"`; images at or below the threshold are skipped.
  Choose the model per session:

  | Model | Size | Install |
  |---|---|---|
  | imgbeddings (CLIP ViT-B/32, ONNX) | ~88 MB | `pip install onnxruntime transformers` |
  | TinyCLIP ViT-8M | ~40 MB | `pip install torch transformers` |
  | imageprism — semantic / style / instance | 14–90 MB | `pip install imageprism` |
  | OpenCLIP ViT-B/32 | ~600 MB | `pip install torch open_clip_torch` |
  | MobileNetV3 | ~20 MB | `pip install torch torchvision` |

  Models download on first use. Example embeddings are cached, so later runs start quickly.

Preferences (Ctrl+,) holds the library folder, browser, upscale model (native file dialog), model folders, resize
defaults and the caption template.

## Where things are stored

Settings and sessions `~/.config/dataset-manager/`, cache `~/.cache/dataset-manager/`, browser profile
`~/.local/share/dataset-manager/`. Create an empty file named `portable` in this folder to keep everything in `./data` instead.

Upscale models are searched in `./models`, `./upscaler`, and any folders added in Preferences.

## Tips

* GNOME: for matching widget styling install a Qt6 Adwaita style (`adwaita-qt6`, or `qt6-gtk-platformtheme`).
* The command line accepts `--library PATH`.
