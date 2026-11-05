"""Preferences: an Adw.PreferencesDialog whose changes apply immediately (no OK / Cancel)."""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from ..core import capabilities, paths, pkgs
from ..core.browser import find_browser
from .widgets import CaptionEditor, ModelPicker, NoteRow, PathRow, TaskPanel, hint, icon_button

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
        self.headless = Adw.SwitchRow(title="Run the browser without a window", active=s.headless)
        g1 = Adw.PreferencesGroup(title="Library")
        g1.add(self.library)
        g2 = Adw.PreferencesGroup(title="Browser", description="Used for image searches.")
        for w in (self.browser, self.profile, self.headless):
            g2.add(w)
        general = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        general.add(g1)
        general.add(g2)
        for row in (self.library, self.browser, self.profile):
            _apply_row(row, self._general_changed)
        self.headless.connect("notify::active", lambda *_: self._general_changed())

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
        sysp = Adw.PreferencesPage(title="System", icon_name="computer-symbolic")
        for g in self._system_groups():
            sysp.add(g)
        sysp.add(self._locations)

        for p in (general, up, cap, sysp):
            self.add(p)

    # ---- apply ----
    def _general_changed(self) -> None:
        s = self.state.settings
        lib_changed = s.library_dir != self.library.get_text()
        s.library_dir, s.browser_path = self.library.get_text(), self.browser.get_text()
        s.browser_profile_dir, s.headless = self.profile.get_text(), self.headless.get_active()
        self.state.save()
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
        dlg = InstallDialog(self.state, module, self._fill_features)
        dlg.present(self.get_root() if isinstance(self.get_root(), Gtk.Widget) else None)


class InstallDialog(Adw.Dialog):
    """Runs pip (and any post-install step) for one optional component, with a live log."""

    def __init__(self, state, module: str, on_done=None):
        f = capabilities.FEATURES[module]
        super().__init__(title=f"Install {f['pip'][0]}", content_width=620, content_height=440)
        self.state, self.module, self.on_done = state, module, on_done
        toolbar = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        bar.set_title_widget(Adw.WindowTitle(title=f"Install {f['pip'][0]}",
                                             subtitle=pkgs.describe_target(state.settings)))
        toolbar.add_top_bar(bar)
        self.panel = TaskPanel(min_height=280)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12,
                       margin_bottom=12)
        body.append(self.panel)
        toolbar.set_content(body)
        self.set_child(toolbar)
        GLib.idle_add(self._start)

    def _start(self):
        f = capabilities.FEATURES[self.module]

        def job(ctx):
            ok = pkgs.install(self.state.settings, f["pip"], ctx)
            if ok and f.get("post"):
                ok = pkgs.run_extra(f["post"], ctx)
            return ok

        def done(ok):
            self.panel.log("Installed successfully." if ok else "Install failed — see the log above.")
            self.state.settings_changed.emit()
            if self.on_done:
                self.on_done()
        self.panel.run(job, done)
        return False
