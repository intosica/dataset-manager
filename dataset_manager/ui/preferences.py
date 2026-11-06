"""Preferences: an Adw.PreferencesDialog whose changes apply immediately (no OK / Cancel)."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from ..core import capabilities, models, paths, pkgs
from ..core.browser import find_browser
from .widgets import CaptionEditor, ModelPicker, NoteRow, PathRow, TaskPanel, alert, hint, icon_button, run_background

DIRECTIONS = [("Enlarge and shrink", "both"), ("Enlarge only", "up"), ("Shrink only", "down")]


def _apply_row(row: Adw.EntryRow, fn) -> None:
    row.set_show_apply_button(True)
    row.connect("apply", lambda _r: fn())


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, state):
        super().__init__(title="Preferences", search_enabled=False)
        self.state = state
        s = state.settings

        # ---- General ----
        self.library = PathRow("Library folder")
        self.library.set_text(s.library_dir)
        self.browser = PathRow("Browser (Chrome or Chromium; empty = automatic)", mode="file")
        self.browser.set_text(s.browser_path)
        self.profile = PathRow("Browser profile (keeps logins and cookies)", mode="dir")
        self.profile.set_text(s.browser_profile_dir)
        self.headless = Adw.SwitchRow(title="Run the browser without a window",
                                      subtitle="Imageboards always run hidden, whatever this says.", active=s.headless)
        self.mature = Adw.SwitchRow(title="Mature content",
                                    subtitle="Shows Rule34.xxx in the Collect page's source list.", active=s.mature_content)
        g1 = Adw.PreferencesGroup(title="Library")
        g1.add(self.library)
        self.follow = Adw.SwitchRow(title="Tag Editor follows the Library page",
                                    subtitle="Open whichever collection is selected on the Library page.",
                                    active=s.tag_editor_follow_library)
        g1.add(self.follow)
        g1.add(self.mature)
        g2 = Adw.PreferencesGroup(title="Browser", description="Used for image searches.")
        for w in (self.browser, self.profile, self.headless):
            g2.add(w)
        general = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        general.add(g1)
        general.add(g2)
        for row in (self.library, self.browser, self.profile):
            _apply_row(row, self._general_changed)
        for w in (self.headless, self.follow, self.mature):
            w.connect("notify::active", lambda *_: self._general_changed())

        # ---- Upscaling ----
        self.target = Adw.SpinRow.new_with_range(64, 8192, 64)
        self.target.set_title("Default longer side (px)")
        self.target.set_value(s.target_dim)
        self.direction = Adw.ComboRow(title="Default direction", model=Gtk.StringList.new([d[0] for d in DIRECTIONS]))
        keys = [d[1] for d in DIRECTIONS]
        self.direction.set_selected(keys.index(s.scale_direction) if s.scale_direction in keys else 0)
        self.snap = Adw.SwitchRow(title="Round sizes to multiples of 64", active=s.snap_to_64)
        g3 = Adw.PreferencesGroup(title="Upscaling")
        for w in (ModelPicker(state), self.target, self.direction, self.snap):
            g3.add(w)
        for w, sig in ((self.target, "notify::value"), (self.direction, "notify::selected"), (self.snap, "notify::active")):
            w.connect(sig, lambda *_: self._upscale_changed())
        self.dirs_group = Adw.PreferencesGroup(title="Model folders",
                                               description="Extra folders searched for models (.pth, .pt, .safetensors).")
        add = icon_button("list-add-symbolic", "Add folder…", self._add_dir)
        self.dirs_group.set_header_suffix(add)
        self._dir_rows: list = []
        self._fill_dirs()
        up = Adw.PreferencesPage(title="Upscaling", icon_name="image-x-generic-symbolic")
        up.add(g3)
        up.add(self.dirs_group)

        # ---- Captions ----
        self.captions = CaptionEditor()
        self.captions.load(s.caption_spec())
        self.captions.changed.append(self._captions_changed)
        cap = Adw.PreferencesPage(title="Captions", icon_name="document-edit-symbolic")
        cap.add(self.captions)

        # ---- System ----
        sysp = Adw.PreferencesPage(title="System", icon_name="computer-symbolic", name="system")
        for g in self._system_groups():
            sysp.add(g)
        sysp.add(self._locations)

        for p in (general, up, cap, self._models_page(), sysp):
            self.add(p)

    # ---- apply ----
    def _general_changed(self) -> None:
        s = self.state.settings
        lib_changed = s.library_dir != self.library.get_text()
        s.library_dir, s.browser_path = self.library.get_text(), self.browser.get_text()
        s.browser_profile_dir, s.headless = self.profile.get_text(), self.headless.get_active()
        s.tag_editor_follow_library = self.follow.get_active()
        s.mature_content = self.mature.get_active()
        self.state.save()
        self.state.settings_changed.emit()
        if lib_changed:
            self.state.library_changed.emit()

    def _upscale_changed(self) -> None:
        s = self.state.settings
        s.target_dim, s.snap_to_64 = int(self.target.get_value()), self.snap.get_active()
        s.scale_direction = DIRECTIONS[self.direction.get_selected()][1]
        self.state.save()

    def _captions_changed(self) -> None:
        s = self.state.settings
        spec = self.captions.spec()
        if (spec.template, spec.good_tags, spec.low_tags, spec.extra_tags) == (
                s.caption_template, s.good_quality_tags, s.low_quality_tags, s.extra_tags):
            return
        s.caption_template, s.good_quality_tags = spec.template, spec.good_tags
        s.low_quality_tags, s.extra_tags = spec.low_tags, spec.extra_tags
        self.state.save()

    # ---- model folders ----
    def _fill_dirs(self) -> None:
        for r in self._dir_rows:
            self.dirs_group.remove(r)
        self._dir_rows = []
        for d in self.state.settings.model_dirs:
            row = Adw.ActionRow(title=GLib.markup_escape_text(str(d)))
            row.add_suffix(icon_button("user-trash-symbolic", "Remove", lambda d=d: self._remove_dir(d)))
            self.dirs_group.add(row)
            self._dir_rows.append(row)

    def _add_dir(self) -> None:
        dlg = Gtk.FileDialog(title="Add Model Folder")

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.state.settings.model_dirs = [*self.state.settings.model_dirs, f.get_path()]
                self.state.save()
                self._fill_dirs()
        dlg.select_folder(self.get_root() if isinstance(self.get_root(), Gtk.Window) else None, None, done)

    def _remove_dir(self, d) -> None:
        self.state.settings.model_dirs = [x for x in self.state.settings.model_dirs if x != d]
        self.state.save()
        self._fill_dirs()

    # ---- system ----
    def _system_groups(self) -> list[Adw.PreferencesGroup]:
        s = self.state.settings

        # where packages go
        self.install_target = Adw.ComboRow(
            title="Install optional packages into",
            model=Gtk.StringList.new(["Virtual environment", "System site-packages"]))
        self.install_target.set_selected(1 if s.install_target == "system" else 0)
        self.venv_row = PathRow("Virtual environment folder")
        self.venv_row.set_text(s.venv_dir)
        self.venv_row.add_suffix(icon_button("edit-undo-symbolic", "Use the default folder", self._reset_venv))
        self.env_note = NoteRow()
        self.venv_row.connect("changed", lambda *_a: self._env_changed())
        self.install_target.connect("notify::selected", lambda *_a: self._env_changed())
        g_env = Adw.PreferencesGroup(
            title="Install location",
            description="Both targets work. A virtual environment is created next to the app's data and leaves the "
                        "flatpak runtime untouched; system installs go into your user's site-packages. Either way the "
                        "app finds the packages on start-up without a restart.")
        for w in (self.install_target, self.venv_row, self.env_note):
            g_env.add(w)
        self._env_changed()

        # one row per optional component
        self.feat_group = Adw.PreferencesGroup(
            title="Optional components",
            description="Install what you want from here; nothing is required to start the app.")
        self._feature_rows = []
        self._fill_features()

        loc = Adw.PreferencesGroup(title="Locations")
        for title, val in (("Application folder", paths.APP_DIR), ("Settings", s.file()),
                           ("Cache", paths.cache_dir()),
                           ("Browser", find_browser(s.browser_path) or "Playwright bundled Chromium"),
                           ("Portable mode", "yes" if paths.portable() else "no")):
            r = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(str(val)), subtitle_selectable=True)
            r.add_css_class("property")
            loc.add(r)
        self._locations = loc
        return [g_env, self.feat_group]

    def _env_changed(self) -> None:
        s = self.state.settings
        s.install_target = "system" if self.install_target.get_selected() else "venv"
        s.venv_dir = self.venv_row.get_text().strip()
        self.venv_row.set_sensitive(s.install_target == "venv")
        pkgs.activate(s)
        self.env_note.set_text("In use: " + pkgs.describe(s))
        self.state.save()

    def _reset_venv(self) -> None:
        self.venv_row.set_text("")

    def _fill_features(self) -> None:
        for r in self._feature_rows:
            self.feat_group.remove(r)
        self._feature_rows = []
        for f in capabilities.report():
            cmd = "pip install " + " ".join(f["pip"]) + (f" · then `{f['post']}`" if f.get("post") else "")
            row = Adw.ActionRow(title=GLib.markup_escape_text(f["what"]),
                                subtitle=GLib.markup_escape_text(cmd), subtitle_selectable=True)
            if f["ok"]:
                row.add_suffix(hint("installed"))
                icon = Gtk.Image(icon_name="object-select-symbolic")
                icon.add_css_class("success")
                row.add_suffix(icon)
            else:
                btn = Gtk.Button(label="Install")
                btn.add_css_class("pill")
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", lambda _b, mod=f["module"]: self._install(mod))
                row.add_suffix(btn)
            self.feat_group.add(row)
            self._feature_rows.append(row)

    def _install(self, module: str) -> None:
        f = capabilities.FEATURES[module]
        s = self.state.settings

        def job(ctx):
            ok = pkgs.install(s, f["pip"], ctx, extra_index=f.get("extra_index", ""))
            if ok and f.get("post"):
                ok = pkgs.run_extra(f["post"], ctx, settings=s)
            capabilities.refresh()
            return ok

        def done(_ok):
            self.state.settings_changed.emit()
            self._fill_features()
            self._fill_models()
        JobDialog(f"Install {f['pip'][0]}", pkgs.describe_target(s), job, done).present(self)

    # ---- models ----
    def _models_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Models", icon_name="system-search-symbolic", name="models")
        self._model_groups = []
        tag = Adw.PreferencesGroup(
            title="Tagging and captioning",
            description="Pick the folder (or file) each model lives in — an existing folder from another tool works. "
                        "Download saves into that folder, or into the app's own data folder when it is empty.")
        self._model_rows = {}
        for key in ("hydra", "wd", "joycaption"):
            m = models.MODELS[key]
            row = PathRow(m["name"] if key != "joycaption" else "JoyCaption",
                          mode="dir", placeholder=str(models.location(key, self.state.settings)))
            row.set_text(getattr(self.state.settings, m["setting"]))
            _apply_row(row, lambda k=key, r=row: self._model_path_changed(k, r))
            tag.add(row)
            note = NoteRow()
            tag.add(note)
            self._model_rows[key] = (row, note)
        self.florence_row = PathRow("Florence 2 models folder", mode="dir",
                                    placeholder="Auto-detect ComfyUI → models/LLM")
        self.florence_row.set_text(self.state.settings.florence_path)
        _apply_row(self.florence_row, self._florence_path_changed)
        tag.add(self.florence_row)
        self.florence_note = NoteRow()
        tag.add(self.florence_note)
        page.add(tag)

        self.dl_group = Adw.PreferencesGroup(title="Downloads", description="From Hugging Face; interrupted downloads resume.")
        page.add(self.dl_group)
        self._dl_rows = []

        self.florence_group = Adw.PreferencesGroup(
            title="Florence 2 downloads",
            description="The same files ComfyUI-Florence2 uses. They are saved into the ComfyUI models/LLM folder "
                        f"({models.florence_download_root(self.state.settings)}).")
        page.add(self.florence_group)
        self._florence_rows = []

        self.cache_row = PathRow("Cache folder for CLIP, similarity and aesthetic models",
                                 placeholder="App cache (default)")
        self.cache_row.set_text(self.state.settings.hf_cache_dir)
        _apply_row(self.cache_row, self._cache_changed)
        self.cache_note = NoteRow()
        other = Adw.PreferencesGroup(title="Other models",
                                     description="Fetched by their libraries on first use. Download them now so the "
                                                 "first collection doesn't stall.")
        other.add(self.cache_row)
        other.add(self.cache_note)
        self.prefetch_group = other
        self._prefetch_rows = []
        page.add(other)
        self._fill_models()
        return page

    def _model_path_changed(self, key: str, row) -> None:
        setattr(self.state.settings, models.MODELS[key]["setting"], row.path())
        self.state.save()
        self._fill_models()

    def _florence_path_changed(self) -> None:
        self.state.settings.florence_path = self.florence_row.path()
        self.state.save()
        self._fill_models()

    def _cache_changed(self) -> None:
        self.state.settings.hf_cache_dir = self.cache_row.path()
        self.state.save()
        self.cache_note.set_text("Takes effect the next time the app starts.")

    def _fill_models(self) -> None:
        s = self.state.settings

        def look():                        # folder walks: off the main thread
            j = models.find_joycaption(s)
            found = {k: models.status(k, s) for k in models.MODELS}
            found["joycaption_path"] = j and (j.get("model") or j.get("path"))
            found["florence"] = models.find_florence_models(s)
            return found
        self._models_tok = tok = getattr(self, "_models_tok", 0) + 1
        run_background(look, lambda f: self._fill_models_now(f) if tok == self._models_tok else None)

    def _fill_models_now(self, have_all: dict) -> None:
        s = self.state.settings
        for key, (_row, note) in self._model_rows.items():
            found = have_all.get("joycaption_path") if key == "joycaption" else have_all.get(key)
            note.set_text(f"Found: {found}" if found else f"Not found in {models.location(key, s)}")
        florence = have_all.get("florence", [])
        self.florence_group.set_description(
            "The same files ComfyUI-Florence2 uses. They are saved into the ComfyUI models/LLM folder "
            f"({models.florence_download_root(self.state.settings)}).")
        self.florence_note.set_text(
            "Found: " + ", ".join(m.name for m in florence) if florence else
            "No Florence 2 models found. ComfyUI's models/LLM folder is scanned automatically; download one below.")
        for r in self._dl_rows + self._prefetch_rows + self._florence_rows:
            (self.dl_group if r in self._dl_rows else
             self.prefetch_group if r in self._prefetch_rows else self.florence_group).remove(r)
        self._dl_rows, self._prefetch_rows, self._florence_rows = [], [], []
        needs = {"hydra": ["torch", "einops", "safetensors"], "wd": ["torch", "timm", "safetensors"],
                 "joycaption": ["llama_cpp"], "joycaption_hf": ["torch", "accelerate"]}
        for key, m in models.MODELS.items():
            have = have_all.get(key)
            row = Adw.ActionRow(title=GLib.markup_escape_text(m["name"]),
                                subtitle=GLib.markup_escape_text(f"{m['size']} · {m['repo']}"))
            missing = [n for n in needs[key] if not capabilities.has(n)]
            if missing:
                row.add_suffix(hint("needs " + ", ".join(missing)))
            row.add_suffix(self._state_suffix(have, lambda k=key: self._download(k)))
            self.dl_group.add(row)
            self._dl_rows.append(row)
        for key, (label, mods) in models.PREFETCH.items():
            row = Adw.ActionRow(title=GLib.markup_escape_text(label))
            missing = [n for n in mods if not capabilities.has(n)]
            if missing:
                row.set_subtitle("Install first: " + ", ".join(missing) + " (System page)")
            btn = self._state_suffix(False, lambda k=key: self._prefetch(k))
            btn.set_sensitive(not missing)
            row.add_suffix(btn)
            self.prefetch_group.add(row)
            self._prefetch_rows.append(row)
        needed = ["torch", "transformers", "timm", "einops"]
        missing = [n for n in needed if not capabilities.has(n)]
        for base, (repo, label, size) in models.FLORENCE_REPOS.items():
            have = any(m.repo == repo or Path(m.path).name == base for m in florence)
            row = Adw.ActionRow(title=GLib.markup_escape_text(label),
                                subtitle=GLib.markup_escape_text(f"{size} · {repo}"))
            if missing:
                row.add_suffix(hint("needs " + ", ".join(missing)))
            row.add_suffix(self._state_suffix(have, lambda r=repo: self._download_florence(r)))
            self.florence_group.add(row)
            self._florence_rows.append(row)

    @staticmethod
    def _state_suffix(done, on_click) -> Gtk.Widget:
        if done:
            icon = Gtk.Image(icon_name="object-select-symbolic", tooltip_text="Downloaded")
            icon.add_css_class("success")
            return icon
        btn = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        btn.add_css_class("pill")
        btn.connect("clicked", lambda _b: on_click())
        return btn

    def _download(self, key: str) -> None:
        s = self.state.settings
        JobDialog(f"Download {models.MODELS[key]['name']}", str(models.download_dir(key, s)),
                  lambda ctx: models.download(key, s, ctx), lambda _ok: self._fill_models()).present(self)

    def _download_florence(self, repo: str) -> None:
        label = next((v[1] for v in models.FLORENCE_REPOS.values() if v[0] == repo), repo)
        JobDialog(f"Download {label}", str(models.florence_download_root(self.state.settings)),
                  lambda ctx: models.download_florence(repo, self.state.settings, ctx),
                  lambda _ok: self._fill_models()).present(self)

    def _prefetch(self, key: str) -> None:
        JobDialog("Download model", models.PREFETCH[key][0], lambda ctx: models.prefetch(key, ctx)).present(self)


class JobDialog(Adw.Dialog):
    """Runs one job (pip install, model download, …) with a live log. Closing it while busy asks first."""

    def __init__(self, title: str, subtitle: str, job, on_done=None):
        super().__init__(title=title, content_width=640, content_height=440)
        self.job, self.on_done = job, on_done
        toolbar = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        bar.set_title_widget(Adw.WindowTitle(title=title, subtitle=subtitle))
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.connect("clicked", lambda _b: self.panel.cancel())
        bar.pack_start(self.cancel_btn)
        toolbar.add_top_bar(bar)
        self.panel = TaskPanel(min_height=280)
        self.panel.on_busy(lambda b: (self.cancel_btn.set_visible(b), self.set_can_close(not b)))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12,
                       margin_bottom=12)
        body.append(self.panel)
        toolbar.set_content(body)
        self.set_child(toolbar)
        self.connect("close-attempt", self._close_attempt)
        GLib.idle_add(self._start)

    def _start(self):
        title = self.get_title()

        def done(ok):
            self.panel.log("Done." if ok else "Did not finish — see the log above.")
            if self.on_done:
                self.on_done(ok)
        if not self.panel.run(self.job, done, name=title):
            self.set_can_close(True)
        return False

    def _close_attempt(self, _d) -> None:
        def answered(rid):
            if rid == "stop":
                self.panel.cancel()
        alert(self, "Stop this task?", f"“{self.get_title()}” is still running.",
              [("keep", "Keep Running"), ("stop", "Stop")], destructive="stop", default="keep", callback=answered)
