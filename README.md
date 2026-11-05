<p align="center">
  <img src="dataset_manager/resources/dataset-manager.svg" alt="Dataset Manager icon" width="128">
</p>

<h1 align="center">Dataset Manager</h1>

<p align="center">Collect, organize, caption and de-duplicate image datasets.<br>
A native GNOME app (GTK 4 + libadwaita), also at home on COSMIC and other desktops.</p>

<p align="center"><img width="1128" height="919" alt="Untitled" src="https://github.com/user-attachments/assets/4b9dc87f-a9c5-4f10-ad84-1b072f966c52" /></p>

## Install

### Flatpak (recommended; works on Wayland and X11/Xorg)

    ./build-flatpak.sh                  # build + install for your user; run again whenever you want to update
    flatpak run io.github.intosica.DatasetManager

Needs `flatpak` and `flatpak-builder`; the script installs the GNOME 50 runtime/SDK from Flathub if missing. Python
packages and Playwright's Chromium are downloaded during the build (several GB of cache in
`~/.cache/dataset-manager-flatpak`, override with `FLATPAK_BUILD_DIR`).

| Option | Effect |
|---|---|
| `--extras "pkg …"` | pip packages baked in. Default `"imageprism onnxruntime transformers"` (light ONNX similarity models); add `torch open_clip_torch torchvision` for TinyCLIP / OpenCLIP / MobileNet, `torch spandrel` for AI upscaling |
| `--bundle` | also writes a single-file `dataset-manager.flatpak` |
| `--clean` | wipes the build cache first |

The flatpak can read and write your home folder, `/media`, `/run/media` and `/mnt`. Its settings and sessions live in
`~/.var/app/io.github.intosica.DatasetManager/`.

### Without Flatpak

    sudo apt install python3-gi gir1.2-adw-1 gir1.2-gtk-4.0       # PyGObject + libadwaita (Debian/Ubuntu/Pop!_OS)
    python3 -m venv --system-site-packages venv
    venv/bin/pip install -r requirements.txt
    venv/bin/playwright install chromium                          # only if you have no Chrome/Chromium
    ./run.sh
    ./install-launcher.sh                                         # optional: application menu entry

## Layout

The pages are in the header bar (Ctrl+1 … Ctrl+5); the left sidebar holds your **sessions**.

| Page | What it does |
|---|---|
| Collect | Image search (Bing, DuckDuckGo, Yahoo, Yandex, Pinterest, Internet Archive) and card-art galleries (ArtofMTG, MTG Nexus) |
| Library | Per-collection statistics and thumbnail previews |
| Duplicates | Exact + near-duplicate finder; review groups, then move to Trash or delete |
| Maintenance | Optional steps: dedupe, sort, rename, JPEG, resize, low-quality flagging, captions |
| Tag Tools | Bulk caption / tag editing |

The window adapts to narrow sizes: the sidebar collapses and the page switcher moves to the bottom.

### Sessions

A session is a saved set of Collect settings (source, query, filters, options, seed images, similarity filter).

* There is always one active session. Whatever you change on the Collect page is saved into it straight away, and a
  session named after nothing follows the search terms until you rename it.
* **+** starts a new blank session; click a session to load it; Start Collection keeps working in the active one.
* Drag sessions to reorder them or into folders; use the ⋮ menu to rename, duplicate or delete; ⋮ in the header sorts A–Z.
* Sessions are stored in `sessions.json` in the config folder.

## Collecting

* **Search filters** follow the selected engine (size, colour, type, layout, people, date, license, …). Unlike on the
  websites you can tick several options per filter: the scraper visits every combination in random order
  (a random 64 if there are more) and splits the image budget between them.
* **Turn off safe search** works for Bing, DuckDuckGo, Yahoo and Yandex.
* **Sort into Portrait / Landscape / Square folders** can be switched off to save everything into the collection folder.
* **Visually similar images (Bing, Yandex)** — give a seed: an image file, a folder of images (a random few are used), or an
  image URL. The scraper uploads each seed to the engine's visual search and downloads only the
  *visually similar* results, not the identification of the picture. The search terms are optional; with them, normal
  results are added too.
* **Keep only images that look like my examples** — a per-session similarity filter, always editable. Choose a model, a
  folder of images to go for and (optionally) a folder of images to avoid. Each download is scored as
  `mean similarity to "look like" − mean similarity to "don't look like"`; images at or below the threshold are skipped.

  | Model | Size | Install |
  |---|---|---|
  | imgbeddings (CLIP ViT-B/32, ONNX) | ~88 MB | `pip install onnxruntime transformers` |
  | TinyCLIP ViT-8M | ~40 MB | `pip install torch transformers` |
  | imageprism — semantic / style / instance | 14–90 MB | `pip install imageprism` |
  | OpenCLIP ViT-B/32 | ~600 MB | `pip install torch open_clip_torch` |
  | MobileNetV3 | ~20 MB | `pip install torch torchvision` |

  Models download on first use; example embeddings are cached.

Preferences (Ctrl+,) holds the library folder, browser, upscale model, model folders, resize defaults and the caption
template; changes apply immediately.

## Where things are stored

Settings and sessions `~/.config/dataset-manager/`, cache `~/.cache/dataset-manager/`, browser profile
`~/.local/share/dataset-manager/` (inside `~/.var/app/…` for the flatpak). Create an empty file named `portable` in
this folder to keep everything in `./data` instead. Upscale models are searched in `./models`, `./upscaler` and any
folders added in Preferences. The command line accepts `--library PATH`.
