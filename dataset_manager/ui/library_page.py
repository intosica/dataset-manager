from __future__ import annotations

import os

from gi.repository import Adw, Gtk

from ..core.imaging import make_thumbnails
from ..core.library import library_stats, preview_files
from ..core.utils import fmt_bytes
from .widgets import hint, icon_button, open_path, run_background

VIEWS = ["All", "Landscape", "Portrait", "Square", "Low quality"]


class LibraryPage(Gtk.Box):
    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        self._dirty, self._token, self._path = True, 0, ""

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
        self.rows.connect("row-selected", lambda _l, _r: self.load_previews())
        self.empty = Adw.StatusPage(icon_name="folder-pictures-symbolic", title="No collections yet",
                                    description="Collected images show up here.", vexpand=True)
        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.ScrolledWindow(child=self.rows, vexpand=True, propagate_natural_height=True,
                                                max_content_height=260), "rows")
        self.stack.add_named(self.empty, "empty")

        self.view = Gtk.DropDown.new_from_strings(VIEWS)
        self.count = Gtk.SpinButton.new_with_range(6, 300, 6)
        self.count.set_value(60)
        controls = Gtk.Box(spacing=8, margin_top=10, margin_bottom=6)
        controls.append(Gtk.Label(label="Preview"))
        controls.append(self.view)
        controls.append(self.count)
        controls.append(Gtk.Label(label="images"))
        shuffle = Gtk.Button(label="Shuffle")
        shuffle.connect("clicked", lambda _b: self.load_previews())
        controls.append(shuffle)
        self.view.connect("notify::selected", lambda *_: self.load_previews())
        self.count.connect("value-changed", lambda _s: self.load_previews())

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                                max_children_per_line=12, column_spacing=8, row_spacing=8, activate_on_single_click=True)
        self.grid.connect("child-activated", lambda _g, child: open_path(child.file_path))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=18, margin_end=18, margin_bottom=12)
        body.append(self.stack)
        body.append(controls)
        body.append(Gtk.ScrolledWindow(child=self.grid, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.append(top)
        self.append(body)

        state.library_changed.connect(self._mark_dirty)
        state.content_changed.connect(self._mark_dirty)
        self.connect("map", lambda _w: self.refresh() if self._dirty else None)

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
        run_background(lambda: library_stats(base), self._show_stats)

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
        path, view, n = row.path, VIEWS[self.view.get_selected()], int(self.count.get_value())
        self._token += 1
        token = self._token

        def job():
            files = preview_files(path, view, n, True)
            return token, files, make_thumbnails(files, 256)
        run_background(job, self._show_previews)

    def _show_previews(self, result) -> None:
        token, files, thumbs = result
        if token != self._token:
            return
        self._clear_grid()
        for f, t in zip(files, thumbs):
            if not t:
                continue
            pic = Gtk.Picture.new_for_filename(t)
            pic.set_size_request(150, 150)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.add_css_class("thumb")
            pic.set_tooltip_text(os.path.basename(f))
            child = Gtk.FlowBoxChild(child=pic)
            child.file_path = f
            self.grid.append(child)
