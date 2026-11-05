from __future__ import annotations

import os

from gi.repository import Adw, Gio, GLib, Gtk

from ..core import capabilities
from ..core.collector import CollectOptions, run_collection
from ..core.similarity import BACKENDS, SimilaritySpec
from ..core.sink import SinkOptions
from ..core.sources import SOURCES
from ..core.sources.filters import NO_SAFE_SEARCH
from .filter_panel import FilterPanel
from .widgets import ModelPicker, PathRow, TaskPanel, icon_button

try:
    from ..core.sources.visual import SIMILAR_ENGINES
except ImportError:                                  # visual search module not available
    SIMILAR_ENGINES = ()

OFF = "Off: keep everything"


def _spin(title: str, lo: int, hi: int, step: int, value: int, subtitle: str = "") -> Adw.SpinRow:
    row = Adw.SpinRow.new_with_range(lo, hi, step)
    row.set_title(title)
    row.set_subtitle(subtitle)
    row.set_value(value)
    return row


def _switch(title: str, active: bool, subtitle: str = "") -> Adw.SwitchRow:
    return Adw.SwitchRow(title=title, subtitle=subtitle, active=active)


class SeedRow(Adw.EntryRow):
    """Seed image(s) for visual search: a file, a folder of images, or an image URL."""

    def __init__(self):
        super().__init__(title="Seed image, folder or image URL")
        self.add_suffix(icon_button("image-x-generic-symbolic", "Choose an image…", lambda: self._browse(False)))
        self.add_suffix(icon_button("folder-open-symbolic", "Choose a folder of images…", lambda: self._browse(True)))

    def _browse(self, folder: bool) -> None:
        dlg = Gtk.FileDialog()

        def done(d, res):
            try:
                f = d.select_folder_finish(res) if folder else d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.set_text(f.get_path())
        (dlg.select_folder if folder else dlg.open)(self.get_root(), None, done)


class CollectPage(Gtk.Box):
    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        s = state.settings
        self.changed = []          # callbacks: parameters changed by the user
        self._loading = False
        self.keys = [k for k, _l, _a in SOURCES]
        self.artist = {k: a for k, _l, a in SOURCES}

        # ---- source ----
        self.source = Adw.ComboRow(title="Source", model=Gtk.StringList.new([lbl for _k, lbl, _a in SOURCES]))
        self.query = Adw.EntryRow(title="Search terms or URL")
        self.name = Adw.EntryRow(title="Collection name (defaults to the search terms)")
        g_source = Adw.PreferencesGroup(title="Source")
        for w in (self.source, self.query, self.name):
            g_source.add(w)

        # ---- search ----
        self.no_safe = _switch("Turn off safe search", False)
        self.g_safe = Adw.PreferencesGroup()
        self.g_safe.add(self.no_safe)
        self.filters = FilterPanel()

        # ---- visual search ----
        self.seed = SeedRow()
        self.seed_count = _spin("Seed images to use", 1, 20, 1, 3, "When the seed is a folder, this many are picked at random")
        self.g_seed = Adw.PreferencesGroup(
            title="Find visually similar images",
            description="Uploads your seed image to the engine's “visually similar images” view and downloads those "
                        "results: more images that look like it, not an identification of it. Can be combined "
                        "with search terms.")
        self.g_seed.add(self.seed)
        self.g_seed.add(self.seed_count)

        # ---- options ----
        self.limit = _spin("Maximum images", 0, 5000, 10, 100, "0 means unlimited")
        self.min_side = _spin("Minimum image size (px)", 0, 4096, 32, s.min_image_side)
        self.parallel = _spin("Parallel downloads", 1, 8, 1, s.parallel_downloads)
        self.randomize = _switch("Randomize order", False)
        self.skip_dups = _switch("Skip duplicates and previously downloaded images", True)
        self.sort_orient = _switch("Sort into Portrait / Landscape / Square folders", True,
                                   "Turn off to save everything straight into the collection folder")
        self.resize = _switch("Resize while downloading", False, "Direction, snapping and caption template are in Preferences")
        self.target = _spin("Longer side (px)", 64, 8192, 64, s.target_dim)
        self.model_picker = ModelPicker(state)
        self.captions = _switch("Write caption files (.txt)", True)
        self.extra = Adw.EntryRow(title="Extra caption tags (e.g. anime screencap)")
        self.extra.set_text(s.extra_tags)
        g_opts = Adw.PreferencesGroup(title="Options")
        for w in (self.limit, self.min_side, self.parallel, self.randomize, self.skip_dups, self.sort_orient,
                  self.resize, self.target, self.model_picker, self.captions, self.extra):
            g_opts.add(w)
        self.resize.connect("notify::active", self._resize_toggled)
        self.captions.connect("notify::active", lambda *_: self.extra.set_visible(self.captions.get_active()))
        self._resize_toggled()

        # ---- similarity filter (every field is always editable; the model row switches it on) ----
        self.backend = Adw.ComboRow(title="Model", model=Gtk.StringList.new([OFF] + [v[0] for v in BACKENDS.values()]))
        self.backend_keys = list(BACKENDS)
        self.pos_dir = PathRow("Look like (folder of example images)")
        self.neg_dir = PathRow("Don't look like (optional folder)")
        self.threshold = Adw.SpinRow(title="Threshold", adjustment=Gtk.Adjustment(
            value=0, lower=-1, upper=1, step_increment=0.01, page_increment=0.05), digits=2,
            subtitle="Score = similarity to the examples minus similarity to the ones to avoid. "
                     "Images at or below the threshold are skipped.")
        g_sim = Adw.PreferencesGroup(title="Keep only images that look like my examples",
                                     description="Pick a model to switch the filter on. Models download on first use.")
        for w in (self.backend, self.pos_dir, self.neg_dir, self.threshold):
            g_sim.add(w)
        self.backend.connect("notify::selected", self._backend_changed)

        # ---- actions + log ----
        self.start_btn = Gtk.Button(label="Start Collection")
        self.start_btn.add_css_class("suggested-action")
        self.start_btn.add_css_class("pill")
        self.cancel_btn = Gtk.Button(label="Cancel", sensitive=False)
        self.cancel_btn.add_css_class("pill")
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_bottom=6)
        buttons.append(self.cancel_btn)
        buttons.append(self.start_btn)
        self.panel = TaskPanel()
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=18, margin_end=18,
                         margin_bottom=12, margin_top=6)
        bottom.append(buttons)
        bottom.append(self.panel)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18,
                          margin_start=12, margin_end=12)
        for w in (g_source, self.g_safe, self.filters, self.g_seed, g_opts, g_sim):
            content.append(w)
        clamp = Adw.Clamp(maximum_size=860, tightening_threshold=600, child=content)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, resize_start_child=True,
                          resize_end_child=False, shrink_start_child=False, shrink_end_child=False,
                          start_child=scroll, end_child=bottom, position=520)
        self.append(paned)

        self.source.connect("notify::selected", self._source_changed)
        self.query.connect("entry-activated", lambda _r: self.start())
        self.start_btn.connect("clicked", lambda _b: self.start())
        self.cancel_btn.connect("clicked", lambda _b: self.panel.cancel())
        self.panel.on_busy(self._busy)
        state.settings_changed.connect(self._reload)

        for w, sig in [(self.source, "notify::selected"), (self.query, "changed"), (self.name, "changed"),
                       (self.no_safe, "notify::active"), (self.seed, "changed"), (self.seed_count, "notify::value"),
                       (self.limit, "notify::value"), (self.min_side, "notify::value"), (self.parallel, "notify::value"),
                       (self.randomize, "notify::active"), (self.skip_dups, "notify::active"),
                       (self.sort_orient, "notify::active"), (self.resize, "notify::active"),
                       (self.target, "notify::value"), (self.captions, "notify::active"), (self.extra, "changed"),
                       (self.backend, "notify::selected"), (self.pos_dir, "changed"), (self.neg_dir, "changed"),
                       (self.threshold, "notify::value")]:
            w.connect(sig, self._edited)
        self.filters.changed.append(self._edited)
        self._source_changed()
        self._backend_changed()

    # ---- session parameters ----
    def _edited(self, *_a) -> None:
        if not self._loading:
            for fn in self.changed:
                fn()

    def current_key(self) -> str:
        return self.keys[self.source.get_selected()]

    def params(self) -> dict:
        sel = self.backend.get_selected()
        return {
            "source": self.current_key(), "query": self.query.get_text(), "name": self.name.get_text(),
            "safe_search_off": self.no_safe.get_active(), "filters": self.filters.all_selections(),
            "seed": self.seed.get_text(), "seed_count": int(self.seed_count.get_value()),
            "limit": int(self.limit.get_value()), "min_side": int(self.min_side.get_value()),
            "parallel": int(self.parallel.get_value()), "randomize": self.randomize.get_active(),
            "skip_duplicates": self.skip_dups.get_active(), "sort_by_orientation": self.sort_orient.get_active(),
            "resize": self.resize.get_active(), "target_dim": int(self.target.get_value()),
            "captions": self.captions.get_active(), "extra_tags": self.extra.get_text(),
            "similarity": {"enabled": sel > 0, "backend": self.backend_keys[max(sel - 1, 0)],
                           "positive_dir": self.pos_dir.get_text(), "negative_dir": self.neg_dir.get_text(),
                           "threshold": round(self.threshold.get_value(), 3)},
        }

    def set_params(self, p: dict) -> None:
        """Replaces every field; missing keys fall back to the defaults, so old sessions still load."""
        d = self.defaults()
        d.update({k: v for k, v in (p or {}).items() if k != "similarity"})
        sim = dict(d["similarity"])
        sim.update((p or {}).get("similarity") or {})
        self._loading = True
        try:
            if d["source"] in self.keys:
                self.source.set_selected(self.keys.index(d["source"]))
            self.query.set_text(d["query"])
            self.name.set_text(d["name"])
            self.no_safe.set_active(bool(d["safe_search_off"]))
            self.filters.set_all_selections(d["filters"])
            self._source_changed()
            self.seed.set_text(d["seed"])
            self.seed_count.set_value(d["seed_count"])
            self.limit.set_value(d["limit"])
            self.min_side.set_value(d["min_side"])
            self.parallel.set_value(d["parallel"])
            self.randomize.set_active(bool(d["randomize"]))
            self.skip_dups.set_active(bool(d["skip_duplicates"]))
            self.sort_orient.set_active(bool(d["sort_by_orientation"]))
            self.resize.set_active(bool(d["resize"]))
            self.target.set_value(d["target_dim"])
            self.captions.set_active(bool(d["captions"]))
            self.extra.set_text(d["extra_tags"])
            idx = self.backend_keys.index(sim["backend"]) + 1 if sim["backend"] in self.backend_keys else 1
            self.backend.set_selected(idx if sim["enabled"] else 0)
            self.pos_dir.set_text(sim["positive_dir"])
            self.neg_dir.set_text(sim["negative_dir"])
            self.threshold.set_value(float(sim["threshold"]))
        finally:
            self._loading = False
        self._backend_changed()

    def defaults(self) -> dict:
        s = self.state.settings
        return {"source": self.keys[0], "query": "", "name": "", "safe_search_off": False, "filters": {},
                "seed": "", "seed_count": 3, "limit": 100, "min_side": s.min_image_side, "parallel": s.parallel_downloads,
                "randomize": False, "skip_duplicates": True, "sort_by_orientation": True, "resize": False,
                "target_dim": s.target_dim, "captions": True, "extra_tags": s.extra_tags,
                "similarity": {"enabled": False, "backend": self.backend_keys[0], "positive_dir": "",
                               "negative_dir": "", "threshold": 0.0}}

    # ---- reactions ----
    def _reload(self) -> None:
        if not self.panel.busy and self.extra.get_text() != self.state.settings.extra_tags:
            self.extra.set_text(self.state.settings.extra_tags)

    def _resize_toggled(self, *_a) -> None:
        on = self.resize.get_active()
        self.target.set_visible(on)
        self.model_picker.set_visible(on)

    def _source_changed(self, *_a) -> None:
        key = self.current_key()
        artist = self.artist[key]
        self.filters.set_engine(key)
        self.filters.set_visible(not artist)
        self.g_safe.set_visible(not artist and key not in NO_SAFE_SEARCH)
        self.g_seed.set_visible(key in SIMILAR_ENGINES)
        self.query.set_title("Artist name" if artist else "Search terms or URL")

    def _backend_changed(self, *_a) -> None:
        sel = self.backend.get_selected()
        if sel == 0:
            self.backend.set_subtitle("The filter is off; every image that passes the other checks is kept.")
            return
        _label, mods, cmd = BACKENDS[self.backend_keys[sel - 1]]
        missing = [m for m in mods if not capabilities.has(m)]
        if missing and os.environ.get("FLATPAK_ID"):
            cmd = f"./build-flatpak.sh --extras \"imageprism onnxruntime transformers {cmd.removeprefix('pip install ')}\""
        self.backend.set_subtitle(GLib.markup_escape_text(f"Not installed. Run: {cmd}") if missing else
                                  "Installed. The model is downloaded on first use and cached.")

    def _busy(self, busy: bool) -> None:
        self.start_btn.set_sensitive(not busy)
        self.cancel_btn.set_sensitive(busy)

    def start(self) -> None:
        if self.panel.busy:
            return
        s = self.state.settings
        p = self.params()
        s.min_image_side, s.parallel_downloads = p["min_side"], p["parallel"]
        s.target_dim, s.extra_tags = p["target_dim"], p["extra_tags"].strip()
        self.state.save()
        key, artist = p["source"], self.artist[p["source"]]
        simp = p["similarity"]
        sim = (SimilaritySpec(simp["backend"], simp["positive_dir"].strip(), simp["negative_dir"].strip(), simp["threshold"])
               if simp["enabled"] else SimilaritySpec())
        seeds = [] if key not in SIMILAR_ENGINES else _expand_seeds(p["seed"], p["seed_count"])
        if p["seed"].strip() and key in SIMILAR_ENGINES and not seeds:
            self.panel.clear()
            self.panel.log("The seed is not an image file, a folder with images, or an http(s) image URL.")
            return
        opts = CollectOptions(
            source=key, query=p["query"], collection_name=p["name"], limit=p["limit"], randomize=p["randomize"],
            parallel=p["parallel"], safe_search_off=p["safe_search_off"] and key not in NO_SAFE_SEARCH,
            filters={} if artist else self.filters.selection(), similarity=sim, seeds=seeds,
            sink=SinkOptions(min_side=p["min_side"], resize=p["resize"], target_dim=s.target_dim,
                             direction=s.scale_direction, snap_to_64=s.snap_to_64, skip_duplicates=p["skip_duplicates"],
                             duplicate_threshold=s.duplicate_threshold, write_captions=p["captions"],
                             caption=s.caption_spec(), sort_by_orientation=p["sort_by_orientation"]))
        self.panel.run(lambda ctx: run_collection(opts, s, ctx), lambda _r: self.state.content_changed.emit())


def _expand_seeds(text: str, count: int) -> list[str]:
    """A URL stays as is; a folder yields up to `count` random images; a file is itself."""
    import random

    from ..core.utils import iter_images
    text = text.strip().strip("\"'")
    if text.lower().startswith(("http://", "https://")):
        return [text]
    if os.path.isdir(text):
        files = list(iter_images(text))
        random.shuffle(files)
        return files[:count]
    return [text] if os.path.isfile(text) else []
