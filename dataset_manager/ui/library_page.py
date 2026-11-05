from __future__ import annotations

import os

from gi.repository import Adw, Gdk, GLib, Gtk

from ..core.imaging import make_thumbnails
from ..core.library import SORTS, library_stats, preview_files
from ..core.upscaler import get_upscaler, upscale_folder
from ..core.utils import fmt_bytes
from .widgets import TaskPanel, alert, hint, icon_button, open_path, run_background

VIEWS = ["All", "Landscape", "Portrait", "Square", "Low quality"]


class LibraryPage(Gtk.Box):
    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        self._dirty, self._token, self._path = True, 0, ""
        self._stats_token, self._spin_id = 0, 0

        self.summary = hint("")
        top = Gtk.Box(spacing=6, margin_top=12, margin_start=18, margin_end=12, margin_bottom=6)
        title = Gtk.Label(label="Library", xalign=0)
        title.add_css_class("title-2")
        top.append(title)
        self.summary.set_hexpand(True)
        self.summary.set_margin_start(8)
        top.append(self.summary)
        top.append(icon_button("view-refresh-symbolic", "Refresh", self.refresh))
        top.append(icon_button("folder-open-symbolic", "Open library folder", lambda: open_path(state.library)))

        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.rows.add_css_class("boxed-list")
        self.rows.connect("row-selected", lambda _l, _r: self._row_selected())
        self.empty = Adw.StatusPage(icon_name="folder-pictures-symbolic", title="No collections yet",
                                    description="Collected images show up here.", vexpand=True)
        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.ScrolledWindow(child=self.rows, vexpand=True), "rows")
        self.stack.add_named(self.empty, "empty")

        self.view = Gtk.DropDown.new_from_strings(VIEWS)
        self.count = Gtk.SpinButton.new_with_range(6, 300, 6)
        self.count.set_value(60)
        self.sort = Gtk.DropDown.new_from_strings(SORTS)
        self.sort.set_tooltip_text("Order of the previews")
        controls = Gtk.Box(spacing=8, margin_top=10, margin_bottom=6)
        controls.append(Gtk.Label(label="Preview"))
        controls.append(self.view)
        controls.append(self.count)
        controls.append(Gtk.Label(label="images"))
        controls.append(Gtk.Label(label="Sort by"))
        controls.append(self.sort)
        self.upscale_btn = Gtk.Button(label="Upscale", tooltip_text="Enlarge the images in this collection")
        self.upscale_btn.connect("clicked", lambda _b: self._upscale())
        controls.append(self.upscale_btn)
        controls.append(Gtk.Label(label="Preserve originals"))
        self.preserve = Gtk.Switch(active=state.settings.upscale_preserve_originals, valign=Gtk.Align.CENTER)
        self.preserve.set_tooltip_text("Keep the untouched image in an original_images folder next to it")
        self.preserve.connect("notify::active", lambda *_a: self._preserve_changed())
        controls.append(self.preserve)
        self.view.connect("notify::selected", lambda *_: self.load_previews())
        self.sort.connect("notify::selected", lambda *_: self.load_previews())
        self.count.connect("value-changed", lambda _s: self._spin_changed())

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                                max_children_per_line=12, column_spacing=8, row_spacing=8, activate_on_single_click=True)
        self.grid.connect("child-activated", lambda _g, child: open_path(child.file_path))

        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview.append(controls)
        preview.append(Gtk.ScrolledWindow(child=self.grid, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=self.stack, end_child=preview,
                          resize_start_child=False, resize_end_child=True, vexpand=True)
        paned.set_position(240)                      # drag the divider: more collections or more previews
        self.panel = TaskPanel(min_height=70)
        self.panel.set_visible(False)                # only shown once an upscale run needs it
        self.panel.on_busy(lambda busy: self.upscale_btn.set_sensitive(not busy))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=18, margin_end=18, margin_bottom=12)
        body.append(paned)
        pbox = Gtk.Box(margin_top=6)
        pbox.append(self.panel)
        body.append(pbox)
        self.append(top)
        self.append(body)

        state.library_changed.connect(self._mark_dirty)
        state.content_changed.connect(self._mark_dirty)
        self.connect("map", lambda _w: self.refresh() if self._dirty else None)

    def _row_selected(self) -> None:
        row = self.rows.get_selected_row()
        if row is not None:
            self.state.selected_collection = row.path
            self.state.library_selected.emit(row.path)
        self.load_previews()

    def _preserve_changed(self) -> None:
        self.state.settings.upscale_preserve_originals = self.preserve.get_active()
        self.state.save()

    def _upscale(self) -> None:
        row = self.rows.get_selected_row()
        if row is None or self.panel.busy:
            return
        s = self.state.settings
        path, preserve = row.path, self.preserve.get_active()

        def answered(rid):
            if rid != "go":
                return

            def done(_r):
                self._mark_dirty()
            self.panel.run(lambda ctx: upscale_folder(path, ctx, s.target_dim, s.snap_to_64, preserve), done,
                           name="Upscaling images")
        alert(self.get_root(), "Upscale this collection?",
              f"Every image in “{row.get_title()}” is rewritten in place up to {s.target_dim} px on the longer side"
              + ("; the originals are copied into original_images/ folders first." if preserve
                 else " and the original resolution is lost.")
              + (" Requires an AI upscale model (Preferences → Upscaling); without one, images are resampled."
                 if not get_upscaler().available else ""),
              [("cancel", "Cancel"), ("go", "Upscale")], default="cancel", callback=answered)

    def _spin_changed(self) -> None:
        if self._spin_id:                  # holding the spin button down: reload once it settles
            GLib.source_remove(self._spin_id)

        def fire():
            self._spin_id = 0
            self.load_previews()
            return False
        self._spin_id = GLib.timeout_add(350, fire)

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self.get_mapped():
            self.refresh()

    # ---- statistics ----
    def refresh(self) -> None:
        self._dirty = False
        base = self.state.library
        if not os.path.isdir(base):
            self._show_stats([])
            self.summary.set_text(f"{base} does not exist yet. Collect some images to create it.")
            return
        self.summary.set_text("Reading library…")
        self._stats_token += 1
        tok = self._stats_token
        # an older, slower walk must not overwrite a newer one
        run_background(lambda: library_stats(base), lambda rows: self._show_stats(rows) if tok == self._stats_token else None)

    def _show_stats(self, rows) -> None:
        while (r := self.rows.get_row_at_index(0)) is not None:
            self.rows.remove(r)
        for s in rows:
            row = Adw.ActionRow(title=s.name, subtitle=(f"{s.landscape} landscape · {s.portrait} portrait · "
                                                        f"{s.square} square · {s.low_quality} low quality · {fmt_bytes(s.size)}"))
            row.path = s.path
            count = Gtk.Label(label=f"{s.total:,}")
            count.add_css_class("dim-label")
            row.add_suffix(count)
            self.rows.append(row)
        total, size = sum(r.total for r in rows), sum(r.size for r in rows)
        if rows:
            self.summary.set_text(f"{total:,} images in {len(rows)} collections · {fmt_bytes(size)}")
            self.stack.set_visible_child_name("rows")
            self.rows.select_row(self.rows.get_row_at_index(0))
        else:
            self.summary.set_text("")
            self.stack.set_visible_child_name("empty")
            self._clear_grid()

    # ---- previews ----
    def _clear_grid(self) -> None:
        while (c := self.grid.get_child_at_index(0)) is not None:
            self.grid.remove(c)

    def load_previews(self) -> None:
        row = self.rows.get_selected_row()
        if row is None:
            self._clear_grid()
            return
        path, view = row.path, VIEWS[self.view.get_selected()]
        n, sort = int(self.count.get_value()), SORTS[self.sort.get_selected()]
        self._token += 1
        token = self._token

        def job():
            files = preview_files(path, view, n, sort)
            texs = []
            for t in make_thumbnails(files, 256):
                try:
                    texs.append(Gdk.Texture.new_from_filename(t) if t else None)   # decoded off the main thread
                except GLib.Error:
                    texs.append(None)
            return token, files, texs
        run_background(job, self._show_previews)

    def _show_previews(self, result) -> None:
        token, files, thumbs = result
        if token != self._token:
            return
        self._clear_grid()
        for f, t in zip(files, thumbs):
            if not t:
                continue
            pic = Gtk.Picture.new_for_paintable(t)
            pic.set_size_request(150, 150)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.add_css_class("thumb")
            pic.set_tooltip_text(os.path.basename(f))
            child = Gtk.FlowBoxChild(child=pic)
            child.file_path = f
            self.grid.append(child)
