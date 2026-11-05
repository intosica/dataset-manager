# Agent handoff

Read this before you change anything. **Always update this file when your change is relevant** — new behaviour,
new settings, new files, fixed bugs, changed test steps, anything the next agent must know — and before you finish
record what you changed, what is still open, and what you could not test. Keep it short; put the detail in a
changelog entry and the lasting rules in the sections above it.

## Project

GTK 4 + libadwaita Python app. Mostly run as the flatpak `io.github.intosica.DatasetManager`, built with
`./build-flatpak.sh` (`--bundle`, `--extras "…"`, `--clean`). Source is in `dataset_manager/`, with `core/` (no GTK)
and `ui/`.

* Optional packages (torch, timm, llama-cpp-python, …) are **not** in the flatpak. Users install them from
  Preferences → System, either into a venv or into the user site at `~/.var/app/<id>/data/python`.
  `core/capabilities.py` lists them. `core/pkgs.py` runs pip and can be cancelled. `pkgs.kill_all()` runs on quit.
* `capabilities.has()` is cached. Call `capabilities.refresh()` after changing `sys.path` (`pkgs.activate` already
  does this).
* `TaskPanel` (ui/widgets.py) allows only one background job app-wide. Pass `name=` to `run()` so the quit dialog can
  say what is running. `TaskPanel.running()` ignores dead threads.
* `AppState.save()` only emits `settings_changed` when the settings actually changed.

## Models (core/models.py, Preferences → Models)

| key | used by | default location when the folder setting is blank |
|---|---|---|
| hydra | JTP Hydra 3.5 tagger (vendored code: `vendor/hydra`, needs torch + einops + safetensors) | `data_dir()/models/hydra` |
| wd | WD EVA02 Large v3 tagger (needs timm) | `data_dir()/models/wd`; Hugging Face caches are searched too |
| joycaption | JoyCaption: a GGUF + mmproj pair (llama-cpp-python, CPU wheel) **or** an HF folder (transformers + accelerate, GPU, 4-bit with bitsandbytes) | `data_dir()/models/joycaption` |

* Downloads go into the folder the user picked, or into the default location above. They resume from `.part` files.
* "Other models" (CLIP / imgbeddings / TinyCLIP / OpenCLIP / MobileNet / cafe_aesthetic / aesthetic v2.5) are
  pre-fetched into the HF cache. The cache folder is configurable (`hf_cache_dir`). `models.apply_cache_env()` runs at
  start-up, so changing that folder needs a restart.
* Only `hydra-3.5` is supported. `jtp-3-hydra.safetensors` (arch `rr_hydra`) needs legacy metadata, so it is not
  supported.
* llama-cpp CUDA wheels from abetlen's index lag behind for py3.13. The CPU wheel works but is slow (about 2 min per
  image with q8 on this machine). On a GPU, the HF JoyCaption build is the fast path.

## Tag Tools page

`ui/tags_page.py` has a ViewStack with two tabs:
* **Tag Editor**: `ui/tag_editor.py`. Its logic is in `core/tagedit.py`, which has a self-test:
  `python3 -m dataset_manager.core.tagedit`.
* **Imageboard Upload Helper**: the old tag processor.

## Testing without clicking

* GUI scripts run inside the flatpak:
  `flatpak run --command=python3 --filesystem=/tmp io.github.intosica.DatasetManager /tmp/script.py`.
  * Use `Adw.Application(application_id="io.github.intosica.DatasetManager", flags=Gio.ApplicationFlags.NON_UNIQUE)`.
    Other ids fail to register on D-Bus.
  * To screenshot a window: `Gtk.WidgetPaintable` → `renderer.render_texture` → `save_to_png`.
  * flatpak forces its own `XDG_CONFIG_HOME`, so a test script reads and writes the **real** app settings.
    Back up `~/.var/app/io.github.intosica.DatasetManager/config/dataset-manager/settings.json` before a test run
    that touches Preferences, and put it back afterwards.
* The host `/usr/bin/python3` has gi (Adw 1.5, no InlineViewSwitcher). Anaconda `python3` has torch/timm but no gi.
* Playwright tests:
  * `tests/test_yandex_extract.py`
  * A headless smoke test of stealth plus `human.click`/`human.scroll`. It must show `navigator.webdriver == False`
    and the click and scroll must land.
* Do not run `find /` on this machine. `/media/montel/Elements` is a slow USB disk.

## README screenshots

Never screenshot a real library. Build the throwaway demo library first and photograph that:

    python3 tests/make_cat_library.py /tmp/catlib     # 13–16 CC0 cat photos from Wikimedia Commons

Then, to photograph the app against it, back up the real config, write a demo one, run the screenshot script, and
put the real config back (flatpak overrides `XDG_CONFIG_HOME`, so there is no other way):

* save `~/.var/app/io.github.intosica.DatasetManager/config/dataset-manager/{settings,sessions}.json` somewhere;
* in `settings.json` set `library_dir` to the demo folder (and blank `hydra_path` / `wd_path` / `joycaption_path` /
  `hf_cache_dir`); rewrite the session names and queries in `sessions.json` to demo terms so the sidebar is clean;
* run the script under Xvfb with `--filesystem=/tmp`, take the shots, `app.quit()`, restore both files.

Screenshots live in `docs/screenshots/` (1200 px wide, 256-colour PNG, about 150–250 KB each) and are referenced
from `README.md`: `library.png` is the hero image, `tag-editor.png` and `collect.png` sit side by side under
Layout. Quantise with `Image.quantize(colors=256)` before committing; plain PNGs are 3× larger.

## Spoofing (don't break)

* `core/browser.py`: persistent context, `--disable-blink-features=AutomationControlled`, `--enable-automation`
  removed, and playwright-stealth.
* `core/sources/human.py`: eased pointer paths, clicks and wheel scrolling.
* `core/sources/visual.py`: `_humanize` only touches headless UAs.

## Changelog

### 2026-11 (this session)
* Fixed the quit prompt showing when nothing was running. Cause: a worker that raised a BaseException (asyncio
  CancelledError on cancel) never cleared `TaskPanel.active`.
* pip:
  * Install runs can be cancelled even while pip is silent, and pip is killed on quit.
  * Added `--prefer-binary --no-warn-script-location`.
  * Playwright now runs via `python -m playwright`. `run_extra` used to look up the missing `ctx.settings`.
  * The browser path is now writable in the flatpak.
  * venvs are created with `--system-site-packages`.
* New: Tag Editor, Hydra/WD taggers, JoyCaption, Models preferences page, and model downloads.
* Performance:
  * Thumbnails and previews are decoded off the main thread.
  * There is one shared sqlite hash cache (WAL).
  * Thumbnails and the similarity cache are written atomically.
  * Duplicates results are capped at about 1500 rows, with no `stat()` per checkbox.
  * Library stats use a token and the spin button is debounced.
  * Seed folders are expanded in the worker.
  * `Node` uses `eq=False`.
  * The organizer only decodes pixels when needed, and orphaned captions are replaced.
  * Filter (model) failures are reported instead of being counted as "invalid".
  * More final URL statuses are marked as seen.
* Flatpak: `compileall` at build time. `/app` is read-only, so every launch used to recompile everything.

* Fixes from the review pass:
  * TaskPanel:
    * On cancel or failure, `on_success` is no longer called.
    * The busy callbacks always run, so dialogs can't get stuck.
    * The generation counter now ignores stale `_finished` calls.
  * JoyCaption: cancelled or empty captions are never written. Prose caption types are stored as raw text and not
    split on commas.
  * Tag Editor:
    * Editing, folder switching and trashing are locked while an AI job runs.
    * Nothing is written until Save Tags is pressed (see below).
    * One bad file no longer aborts auto-tagging.
  * Downloads:
    * A 416 response is checked against Content-Range.
    * A connection that closes early no longer marks the file complete.
    * Requests use `Accept-Encoding: identity`.
  * pip is killed by process group.
  * Changing the tagger threshold no longer reloads the model.
  * Model-folder lookups run off the main thread.
  * The organizer renames a clashing `.txt` to `.orphan` instead of deleting it.

* Tag Editor saves on demand (`ui/tag_editor.py`):
  * Caption edits live in `self.captions`; `self._disk` is what is on disk. `dirty()` diffs the two, and
    `_stage()` is the only writer of memory. Nothing touches a `.txt` until `save_all()` runs (Save Tags button
    or Ctrl+S). The button label carries the unsaved count; Revert restores just the current image from `_disk`.
  * `save_current()` now only stages the caption box, so clicking another image, reloading, or filtering never
    loses text. `reload()` keeps staged edits for files that still exist (`_reload_pending`).
  * Batch edit, auto-tag, JoyCaption and undo all stage too; the log says "press Save Tags to write them".
  * Switching to a folder with unsaved edits asks Save / Discard / Cancel. Closing the window with unsaved
    captions asks Save and Quit / Discard and Quit / Cancel (`main_window._close_request`).
* Library page (`ui/library_page.py`):
  * Shuffle is gone. A "Sort by" dropdown picks Random / Name A–Z / Newest / Oldest / Largest
    (`core/library.SORTS`, `preview_files(sort=…)`).
  * The collection list and the preview grid sit in a `Gtk.Paned`; drag the divider to give either more room.
  * "Upscale" rewrites the selected collection in place via `core/upscaler.upscale_folder` (AI model when one is
    configured, plain LANCZOS otherwise) up to Preferences → Upscaling target size, in a TaskPanel.
  * "Preserve originals" (remembered in `settings.upscale_preserve_originals`) copies each untouched image to an
    `original_images/` folder beside it before the upscale overwrites it.
* Preferences → Library: "Tag Editor follows the Library page" (`settings.tag_editor_follow_library`) makes the
  editor open whatever collection row is selected on the Library page (`state.library_selected`).

* README: the old GitHub-attachment screenshot (a private dataset) is gone. The hero image, the Tag Editor shot and
the Collect shot now come from an isolated demo library of CC0 cat photos (`tests/make_cat_library.py`, which
re-downloads it on demand); the README credits them and says they are CC0.
* Pushed as `95e81ed` (Tag Editor / taggers / JoyCaption / model downloads / Library sort+upscale+preserve) and
  `2553020` (screenshots, demo-library script).

### Open / ideas
* `sink.ingest` runs one image at a time per batch. Hashing and filtering could be parallelised, but the sink is not
  thread-safe.
* `sessions_panel.rebuild()` recreates every row. That is fine up to a few hundred sessions.
* The thumbnail cache is never pruned.
