"""Tag Editor: browse a dataset folder, filter by tags, edit captions one by one or in batches, auto-tag with
JTP Hydra / WD EVA02 and caption with JoyCaption. Captions are the .txt files next to the images."""
from __future__ import annotations

import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from ..core import capabilities, joycaption, models, tagedit, taggers
from ..core.captions import split_tags
from ..core.imaging import make_thumbnail
from .widgets import NoteRow, TaskPanel, _once, alert, desktop_trash, hint, icon_button, run_background

THUMB = 148
TAGGERS = [("hydra", "JTP Hydra 3.5 · e621 tags"), ("wd", "WD EVA02 Large v3 · Danbooru tags")]
TAG_MODES = [("merge", "Add missing tags"), ("prepend", "Put new tags first"), ("replace", "Replace the caption"),
             ("skip", "Skip images that have tags")]
JOY_MODES = [("replace", "Replace the caption"), ("merge", "Append to the caption"),
             ("skip", "Skip images that have captions")]
_pool = ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 2), thread_name_prefix="thumbs")


class Item(GObject.Object):
    def __init__(self, path: str):
        super().__init__()
        self.path = path


def _combo(title: str, labels: list[str], selected: int = 0) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    row.set_selected(max(0, selected))
    return row


def _index(pairs, key) -> int:
    keys = [k for k, _ in pairs]
    return keys.index(key) if key in keys else 0


class TagEditor(Gtk.Box):
    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        self.folder = ""
        self.captions: dict[str, list[str]] = {}
        self.shown: list[str] = []
        self.current: str | None = None
        self._disk: dict[str, list[str]] = {}          # what is on disk; captions holds the edits
        self._reload_pending: dict[str, list[str]] = {}
        self._undo: dict[str, list[str]] | None = None
        self._tex: OrderedDict = OrderedDict()        # thumb path -> Gdk.Texture (LRU)
        self._wanted: set[str] = set()                 # images whose grid cell is on screen
        self._filter_id = self._tags_id = 0
        self._preview_tok = 0
        self._cells: dict = {}                         # image -> overlay of its bound grid cell

        # ---- toolbar ----
        bar = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        self.open_btn = open_btn = Gtk.Button(child=Adw.ButtonContent(icon_name="folder-open-symbolic", label="Open Folder…"))
        open_btn.connect("clicked", lambda _b: self._choose_folder())
        self.lib_btn = lib_btn = icon_button("folder-pictures-symbolic", "Open the library", lambda: self.load_folder(state.library))
        self.reload_btn = icon_button("view-refresh-symbolic", "Reload captions from disk", self.reload)
        self.search = Gtk.SearchEntry(hexpand=True,
                                      placeholder_text="Filter: 1girl, -solo, long*, *hair*, :untagged")
        self.search.set_tooltip_text("Comma-separated tags. -tag excludes, tag* matches a prefix, *part* matches "
                                     "inside tags, :untagged finds images without a caption.")
        self.search.connect("search-changed", lambda _e: self._apply_filter())
        self.count = Gtk.Label(css_classes=["dim-label", "numeric"])
        self.recursive = Gtk.CheckButton(label="Subfolders", active=True)
        self.recursive.connect("toggled", lambda _b: self.reload())
        self.save_all_btn = Gtk.Button(label="Save All Tags", sensitive=False)
        self.save_all_btn.connect("clicked", lambda _b: self.save_all())
        for w in (open_btn, lib_btn, self.reload_btn, self.recursive, self.search, self.count, self.save_all_btn):
            bar.append(w)

        # ---- grid ----
        self.store = Gio.ListStore.new(Item)
        self.sel = Gtk.MultiSelection.new(self.store)
        self.sel.connect("selection-changed", lambda *_a: self._selection_changed())
        fac = Gtk.SignalListItemFactory()
        fac.connect("setup", self._setup_cell)
        fac.connect("bind", self._bind_cell)
        fac.connect("unbind", self._unbind_cell)
        self.grid = Gtk.GridView(model=self.sel, factory=fac, max_columns=30, enable_rubberband=True)
        self.grid.add_css_class("tag-grid")
        grid_scroll = Gtk.ScrolledWindow(child=self.grid, vexpand=True, hexpand=True)
        self.empty = Adw.StatusPage(icon_name="folder-pictures-symbolic", title="Open a Dataset Folder",
                                    description="Images and their .txt captions are edited in place.")
        go = Gtk.Button(label="Open Folder…", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        go.connect("clicked", lambda _b: self._choose_folder())
        self.empty.set_child(go)
        self.left = Gtk.Stack()
        self.left.add_named(self.empty, "empty")
        self.left.add_named(grid_scroll, "grid")
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._grid_key)
        self.grid.add_controller(keys)

        # ---- side panel ----
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=12, margin_end=12,
                       margin_top=6, margin_bottom=12)
        side.append(self._caption_group())
        side.append(self._batch_group())
        side.append(self._tags_group())
        side.append(self._autotag_group())
        side.append(self._joy_group())
        side_scroll = Gtk.ScrolledWindow(child=Adw.Clamp(child=side, maximum_size=520), hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vexpand=True)
        self.panel = TaskPanel(min_height=90)
        self.panel.on_busy(self._busy)
        self.panel.scroll.set_vexpand(False)         # the log stays small; the editor gets the height
        self.panel.scroll.set_max_content_height(160)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.append(side_scroll)
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12, margin_bottom=8)
        pbox.append(self.panel)
        right.append(pbox)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=self.left, end_child=right,
                          resize_start_child=True, resize_end_child=False, shrink_end_child=False,
                          shrink_start_child=False, vexpand=True)
        paned.set_position(720)
        right.set_size_request(400, -1)
        self.toasts = Adw.ToastOverlay(child=paned, vexpand=True)
        self.append(bar)
        self.append(Gtk.Separator())
        self.append(self.toasts)
        self._selection_changed()
        self.connect("unrealize", lambda _w: self._flush())
        self.connect("map", self._first_map)
        state.library_selected.connect(self._library_selected)

    def _library_selected(self, path: str) -> None:
        if self.panel.busy:                        # never swap folders under a running job
            return
        if self.state.settings.tag_editor_follow_library and path and path != self.folder and os.path.isdir(path):
            self.load_folder(path, remember=False)

    def _flush(self) -> None:
        """Window is closing: keep unsaved caption text and pending option changes."""
        self.save_current()
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_settings()

    def _first_map(self, _w) -> None:
        # reopen the last folder only when the tab is first shown, so start-up stays fast
        self.disconnect_by_func(self._first_map)
        s, sel = self.state.settings, self.state.selected_collection
        if s.tag_editor_follow_library and sel and os.path.isdir(sel):
            self.load_folder(sel, remember=False)
            return
        last = s.tag_editor_dir
        if not self.folder and last and os.path.isdir(last):
            self.load_folder(last)

    # ================= widgets =================
    def _caption_group(self) -> Gtk.Widget:
        g = Adw.PreferencesGroup(title="Caption")
        self.preview = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, height_request=260, can_shrink=True)
        self.preview.add_css_class("card")
        self.preview.set_tooltip_text("Double-click for a closer look")
        dbl = Gtk.GestureClick()
        dbl.connect("pressed", lambda _g, n, _x, _y: self._zoom() if n == 2 else None)
        self.preview.add_controller(dbl)
        self.cap_name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=["dim-label"])
        self.cap_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                                     left_margin=8, right_margin=8, height_request=110)
        self.cap_buf = self.cap_view.get_buffer()
        self.cap_buf.connect("changed", lambda _b: self._cap_edited())
        frame = Gtk.ScrolledWindow(child=self.cap_view, css_classes=["card"], min_content_height=120)
        # drag the grip under the box to make it taller or shorter
        grip = Gtk.Image(icon_name="dm-resize-vertical-symbolic", pixel_size=18, halign=Gtk.Align.CENTER,
                         tooltip_text="Drag up or down to resize the caption box")   # own icon: always bundled
        grip.set_cursor(Gdk.Cursor.new_from_name("ns-resize"))
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda *_a: setattr(self, "_grip_h", frame.get_min_content_height()))
        drag.connect("drag-update", lambda _g, _x, dy: frame.set_min_content_height(max(60, int(self._grip_h + dy))))
        grip.add_controller(drag)
        self.save_btn = Gtk.Button(label="Save Tags", sensitive=False)
        self.save_btn.set_tooltip_text("Write this image's caption to disk")
        self.save_btn.connect("clicked", lambda _b: self.save_all(only=self.current))
        self.revert_btn = Gtk.Button(label="Revert", sensitive=False)
        self.revert_btn.connect("clicked", lambda _b: self._revert_current())
        self.trash_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Move the selected images to the Trash")
        self.trash_btn.connect("clicked", lambda _b: self._trash_selected())
        row = Gtk.Box(spacing=6)
        row.append(self.cap_name)
        self.cap_name.set_hexpand(True)
        for b in (self.trash_btn, self.revert_btn, self.save_btn):
            row.append(b)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for w in (self.preview, frame, grip, row):
            box.append(w)
        g.add(box)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._cap_key)
        self.cap_view.add_controller(keys)
        return g

    def _batch_group(self) -> Gtk.Widget:
        self.batch = Adw.PreferencesGroup(title="Batch Edit")
        self.add_row = Adw.EntryRow(title="Add tags (comma-separated)", show_apply_button=True)
        self.add_row.connect("apply", lambda _r: self._batch("add"))
        self.add_first = Adw.SwitchRow(title="Add at the start", active=False)
        self.remove_row = Adw.EntryRow(title="Remove tags", show_apply_button=True)
        self.remove_row.connect("apply", lambda _r: self._batch("remove"))
        self.find_row = Adw.EntryRow(title="Replace tag")
        self.repl_row = Adw.EntryRow(title="With (empty = remove)", show_apply_button=True)
        self.repl_row.connect("apply", lambda _r: self._batch("replace"))
        tools = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        for label, op in (("Sort A–Z", "sort"), ("Remove Duplicates", "dedupe")):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, o=op: self._batch(o))
            tools.append(b)
        self.undo_btn = Gtk.Button(label="Undo", sensitive=False)
        self.undo_btn.connect("clicked", lambda _b: self._undo_batch())
        tools.append(self.undo_btn)
        for w in (self.add_row, self.add_first, self.remove_row, self.find_row, self.repl_row):
            self.batch.add(w)
        self.batch.add(tools)
        return self.batch

    def _tags_group(self) -> Gtk.Widget:
        g = Adw.PreferencesGroup(title="Tags", description="Click a tag to filter by it.")
        self.tag_search = Gtk.SearchEntry(placeholder_text="Find a tag")
        self.tag_search.connect("search-changed", lambda _e: self._fill_tags())
        self.tag_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.tag_list.connect("row-activated", lambda _l, r: self._filter_add(r.tag))
        self.tag_more = hint()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for w in (self.tag_search, self.tag_list, self.tag_more):
            box.append(w)
        g.add(box)
        return g

    def _autotag_group(self) -> Gtk.Widget:
        s = self.state.settings
        g = Adw.PreferencesGroup(title="Auto-tag")
        self.tagger = _combo("Tagger", [l for _k, l in TAGGERS], _index(TAGGERS, s.tagger))
        self.tagger.connect("notify::selected", lambda *_a: (self._save_opts(), self._model_status()))
        self.threshold = Adw.SpinRow.new_with_range(0, 0.95, 0.05)
        self.threshold.set_title("Threshold")
        self.threshold.set_subtitle("0 = automatic (Hydra's calibrated per-tag values · 0.35 for WD)")
        self.threshold.set_digits(2)
        self.threshold.set_value(s.tag_threshold)
        self.threshold.connect("notify::value", lambda *_a: self._save_opts())
        self.tag_mode = _combo("Existing captions", [l for _k, l in TAG_MODES])
        self.tag_note = NoteRow()
        self.tag_btn = Gtk.Button(label="Tag Images", css_classes=["suggested-action", "pill"], halign=Gtk.Align.END,
                                  margin_top=6)
        self.tag_btn.connect("clicked", lambda _b: self._autotag())
        for w in (self.tagger, self.threshold, self.tag_mode, self.tag_note):
            g.add(w)
        g.add(self.tag_btn)
        return g

    def _joy_group(self) -> Gtk.Widget:
        s = self.state.settings
        g = Adw.PreferencesGroup(title="JoyCaption", description="Natural-language captions or tag lists from an image-"
                                                                 "aware language model.")
        types = list(joycaption.CAPTION_TYPE_MAP)
        self.joy_type = _combo("Caption type", types, types.index(s.joy_caption_type)
                               if s.joy_caption_type in types else 0)
        self.joy_len = _combo("Length", joycaption.LENGTHS, joycaption.LENGTHS.index(s.joy_caption_length)
                              if s.joy_caption_length in joycaption.LENGTHS else 0)
        self.joy_name = Adw.EntryRow(title="Character name (optional)")
        self.joy_name.set_text(s.joy_name)
        self.joy_extras = Adw.ExpanderRow(title="Extra instructions")
        self._extra_checks = []
        for text in joycaption.EXTRA_OPTIONS_LIST:
            r = Adw.ActionRow(title=GLib.markup_escape_text(text), title_lines=0)
            chk = Gtk.CheckButton(active=text in s.joy_extra, valign=Gtk.Align.CENTER)
            chk.connect("toggled", lambda *_a: self._save_opts())
            r.add_prefix(chk)
            r.set_activatable_widget(chk)
            self.joy_extras.add_row(r)
            self._extra_checks.append((text, chk))
        self.joy_temp = Adw.SpinRow.new_with_range(0, 2, 0.05)
        self.joy_temp.set_title("Temperature")
        self.joy_temp.set_subtitle("0 = always the same answer · 0.6 is a good balance")
        self.joy_temp.set_digits(2)
        self.joy_temp.set_value(s.joy_temperature)
        self.joy_tokens = Adw.SpinRow.new_with_range(32, 2048, 32)
        self.joy_tokens.set_title("Maximum length (tokens)")
        self.joy_tokens.set_value(s.joy_max_tokens)
        self.joy_mode = _combo("Existing captions", [l for _k, l in JOY_MODES])
        self.joy_prompt = Adw.ActionRow(title="Prompt", subtitle_lines=0, subtitle_selectable=True)
        self.joy_prompt.add_css_class("property")
        self.joy_note = NoteRow()
        for w in (self.joy_type, self.joy_len, self.joy_name):
            w.connect("notify::selected" if isinstance(w, Adw.ComboRow) else "changed", lambda *_a: self._save_opts())
        for w in (self.joy_temp, self.joy_tokens):
            w.connect("notify::value", lambda *_a: self._save_opts())
        btns = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_top=6)
        free = Gtk.Button(label="Free GPU Memory", tooltip_text="Unload the tagger and JoyCaption models")
        free.connect("clicked", lambda _b: self._free())
        self.joy_btn = Gtk.Button(label="Caption Images", css_classes=["suggested-action", "pill"])
        self.joy_btn.connect("clicked", lambda _b: self._caption())
        btns.append(free)
        btns.append(self.joy_btn)
        for w in (self.joy_type, self.joy_len, self.joy_name, self.joy_extras, self.joy_temp, self.joy_tokens,
                  self.joy_mode, self.joy_prompt, self.joy_note):
            g.add(w)
        g.add(btns)
        self._update_prompt()
        self._model_status()
        self.state.settings_changed.connect(self._model_status)
        return g

    # ================= grid cells =================
    def _setup_cell(self, _f, li) -> None:
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, width_request=THUMB,
                          height_request=THUMB, css_classes=["thumb"])
        badge = Gtk.Image(icon_name="document-edit-symbolic", halign=Gtk.Align.END, valign=Gtk.Align.END,
                          margin_end=6, margin_bottom=6, tooltip_text="No caption", css_classes=["osd"])
        ov = Gtk.Overlay(child=pic)
        ov.add_overlay(badge)
        li.set_child(ov)

    def _bind_cell(self, _f, li) -> None:
        item, ov = li.get_item(), li.get_child()
        pic, badge = ov.get_child(), ov.get_last_child()
        pic.path = item.path
        self._cells[item.path] = ov
        self._update_cell(item.path, ov)
        self._wanted.add(item.path)
        tex = self._tex.get(item.path)
        if tex is not None:
            self._tex.move_to_end(item.path)
            pic.set_paintable(tex)
            return
        pic.set_paintable(None)
        _pool.submit(self._load_thumb, item.path, pic)

    def _update_cell(self, path: str, ov) -> None:
        tags = self.captions.get(path, [])
        ov.get_last_child().set_visible(not tags)
        ov.set_tooltip_text(f"{os.path.basename(path)}\n{len(tags)} tags")

    def _unbind_cell(self, _f, li) -> None:
        pic = li.get_child().get_child()
        path = getattr(pic, "path", None)
        self._wanted.discard(path)
        if self._cells.get(path) is li.get_child():
            del self._cells[path]
        pic.path = None

    def _load_thumb(self, path: str, pic) -> None:
        if path not in self._wanted:          # scrolled past before we got to it
            return
        t = make_thumbnail(path, 256)
        tex = None
        if t:
            try:
                tex = Gdk.Texture.new_from_filename(t)      # decoded here, off the main thread
            except GLib.Error:
                pass
        if tex is not None:
            _once(self._thumb_ready, path, pic, tex)

    def _thumb_ready(self, path, pic, tex) -> None:
        self._tex[path] = tex
        while len(self._tex) > 800:
            self._tex.popitem(last=False)
        if getattr(pic, "path", None) == path:
            pic.set_paintable(tex)

    # ================= loading / filtering =================
    def _choose_folder(self) -> None:
        dlg = Gtk.FileDialog(title="Open Dataset Folder")
        start = self.folder or self.state.settings.tag_editor_dir or self.state.library
        if start and os.path.isdir(start):
            dlg.set_initial_folder(Gio.File.new_for_path(start))

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.load_folder(f.get_path())
        dlg.select_folder(self.get_root(), None, done)

    def load_folder(self, folder: str, remember: bool = True) -> None:
        if not folder or not os.path.isdir(folder):
            return
        self.save_current()
        n = self.unsaved()
        if n and folder != self.folder:            # don't silently drop captions staged for the old folder
            def answered(rid):
                if rid == "cancel":
                    return
                if rid == "save":
                    self.save_all()
                elif rid == "discard":
                    self._discard()
                self._open(folder, remember)
            alert(self.get_root(), f"Save {n} unsaved caption{'s' if n != 1 else ''} first?",
                  "They belong to the folder you are leaving.",
                  [("cancel", "Cancel"), ("discard", "Discard"), ("save", "Save and Open")],
                  destructive="discard", default="save", callback=answered)
            return
        self._open(folder, remember)

    def _open(self, folder: str, remember: bool) -> None:
        self.folder = folder
        if remember and self.state.settings.tag_editor_dir != folder:
            self.state.settings.tag_editor_dir = folder
            self.state.save()
        self.reload()

    def reload(self) -> None:
        if not self.folder:
            return
        self.save_current()
        self._reload_pending = self.dirty()
        self.count.set_text("Loading…")
        folder, rec = self.folder, self.recursive.get_active()
        run_background(lambda: (folder, tagedit.load(folder, rec)), self._loaded,
                       lambda exc: (self.count.set_text(""), self._toast(f"Could not read the folder: {exc}")))

    def _loaded(self, result) -> None:
        folder, caps = result
        if folder != self.folder:
            return
        self._disk = dict(caps)                    # staged edits survive a reload
        self.captions = dict(caps)
        self.captions.update({p: t for p, t in self._reload_pending.items() if p in self.captions})
        self._reload_pending = {}
        self._undo = None
        self.undo_btn.set_sensitive(False)
        self._update_save_ui()
        self.left.set_visible_child_name("grid" if caps else "empty")
        if not caps:
            self.empty.set_title("No Images Here")
            self.empty.set_description(GLib.markup_escape_text(folder))
        self._apply_filter(now=True)

    def _apply_filter(self, now: bool = False) -> None:
        if self._filter_id:
            GLib.source_remove(self._filter_id)
            self._filter_id = 0
        if not now:                                       # debounce typing
            self._filter_id = GLib.timeout_add(200, self._filter_timeout)
            return
        q = self.search.get_text()
        keep = self.current
        self.shown = [p for p, t in self.captions.items() if tagedit.matches(t, q)]
        self.store.splice(0, self.store.get_n_items(), [Item(p) for p in self.shown])
        self.count.set_text(f"{len(self.shown):,} / {len(self.captions):,}")
        if keep in self.shown:
            self.sel.select_item(self.shown.index(keep), True)
        self._selection_changed()

    def _filter_timeout(self) -> bool:
        self._filter_id = 0
        self._apply_filter(True)
        return False

    def _filter_add(self, tag: str) -> None:
        cur = [t.strip() for t in self.search.get_text().split(",") if t.strip()]
        if tag.lower() not in {c.lower() for c in cur}:
            self.search.set_text(", ".join(cur + [tag]))

    # ================= selection =================
    def selected(self) -> list[str]:
        bits = self.sel.get_selection()
        return [self.shown[bits.get_nth(i)] for i in range(bits.get_size()) if bits.get_nth(i) < len(self.shown)]

    def targets(self) -> list[str]:
        """Selected images, or every image currently shown when nothing is selected."""
        return self.selected() or list(self.shown)

    def _selection_changed(self) -> None:
        sel = self.selected()
        one = sel[0] if len(sel) == 1 else None
        if one != self.current:
            self.save_current()
            self.current = one
            self._show_caption()
        n = len(self.targets())
        what = f"{len(sel):,} selected images" if sel else f"all {n:,} shown images"
        self.batch.set_description(f"Applies to {what}.")
        busy = self.panel.busy
        self.tag_btn.set_label(f"Tag {n:,} Images" if n != 1 else "Tag 1 Image")
        self.joy_btn.set_label(f"Caption {n:,} Images" if n != 1 else "Caption 1 Image")
        for b in (self.tag_btn, self.joy_btn):
            b.set_sensitive(n > 0 and not busy)
        self.trash_btn.set_sensitive(bool(sel) and not busy)
        self._fill_tags()

    def _show_caption(self) -> None:
        self._loading_caption = True
        p = self.current
        self.cap_view.set_sensitive(p is not None and not self.panel.busy)
        self.cap_buf.set_text(", ".join(self.captions.get(p, [])) if p else "")
        self.cap_name.set_text(os.path.basename(p) if p else
                               ("Select one image to edit its caption" if self.captions else ""))
        self._loading_caption = False
        self._update_save_ui()
        self._preview_tok += 1
        tok = self._preview_tok
        if not p:
            self.preview.set_paintable(None)
            return

        def job():
            t = make_thumbnail(p, 768)
            try:
                return tok, Gdk.Texture.new_from_filename(t) if t else None
            except GLib.Error:
                return tok, None
        run_background(job, lambda r: self.preview.set_paintable(r[1]) if r[0] == self._preview_tok else None)

    def _cap_edited(self) -> None:
        if getattr(self, "_loading_caption", False) or not self.current:
            return
        text = self.cap_buf.get_text(self.cap_buf.get_start_iter(), self.cap_buf.get_end_iter(), False)
        self._stage({self.current: split_tags(text)}, refresh=False)
        if self.current in self._cells:
            self._update_cell(self.current, self._cells[self.current])

    # ---- staged captions: memory until Save Tags ----
    def dirty(self) -> dict[str, list[str]]:
        return {p: t for p, t in self.captions.items() if t != self._disk.get(p, [])}

    def unsaved(self) -> int:
        return len(self.dirty())

    def _update_save_ui(self) -> None:
        n = self.unsaved()
        cur = self.current
        cur_dirty = bool(cur) and self.captions.get(cur, []) != self._disk.get(cur, [])
        self.save_all_btn.set_label(f"Save All Tags ({n:,})" if n else "Save All Tags")
        self.save_all_btn.set_sensitive(n > 0)
        self.save_all_btn.set_tooltip_text(f"Write {n:,} changed caption{'s' if n != 1 else ''} to disk · Ctrl+S"
                                           if n else "Every caption is saved")
        # highlight only when there really is something to save; an insensitive blue button reads as "unsaved"
        for b, on in ((self.save_all_btn, n > 0), (self.save_btn, cur_dirty)):
            (b.add_css_class if on else b.remove_css_class)("suggested-action")
        self.save_btn.set_sensitive(cur_dirty)
        self.revert_btn.set_sensitive(cur_dirty)

    def _discard(self) -> None:
        """Drop every staged edit (the caption box included) and go back to what is on disk."""
        self.captions = dict(self._disk)
        self._show_caption()
        self._refresh_cells()

    def _stage(self, new: dict[str, list[str]], refresh: bool = True) -> int:
        """Keep caption changes in memory; only Save Tags and the close prompt write them."""
        changed = 0
        for p, tags in new.items():
            if self.captions.get(p, []) != tags:
                self.captions[p] = list(tags)
                changed += 1
        if changed:
            if refresh:
                self._refresh_cells()
                self._fill_tags()
            self._update_save_ui()
        return changed

    def save_current(self) -> None:
        """Stage the caption box in memory (picking another image must not lose it)."""
        if not self.current:
            return
        text = self.cap_buf.get_text(self.cap_buf.get_start_iter(), self.cap_buf.get_end_iter(), False)
        self._stage({self.current: split_tags(text)}, refresh=False)

    def _revert_current(self) -> None:
        p = self.current
        if not p:
            return
        self._stage({p: self._disk.get(p, [])})
        self._show_caption()

    def save_all(self, only: str | None = None) -> int:
        """Write every staged caption, or just `only`'s."""
        self.save_current()
        pending = self.dirty()
        if only:
            pending = {only: pending[only]} if only in pending else {}
        if not pending:
            self._update_save_ui()
            return 0
        failed = []
        for p, tags in pending.items():
            try:
                tagedit.write_tags(p, tags)
                self._disk[p] = tags
            except OSError as exc:
                failed.append(f"{os.path.basename(p)}: {exc}")
        self._update_save_ui()
        if failed:
            alert(self.get_root(), "Could Not Save Some Captions",
                  "\n".join(failed[:8]) + (f"\n… and {len(failed) - 8} more" if len(failed) > 8 else ""),
                  [("ok", "OK")])
        saved = len(pending) - len(failed)
        if saved:
            self._toast(f"Saved {saved:,} caption{'s' if saved != 1 else ''}.")
            self._fill_tags()
        return saved

    def _zoom(self) -> None:
        """Closer look at the current image in a dialog (X or Escape closes it)."""
        p = self.current
        if not p:
            return
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, vexpand=True, hexpand=True,
                          paintable=self.preview.get_paintable())          # the preview until the full image loads
        view = Adw.ToolbarView(content=pic)
        view.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title=os.path.basename(p))))
        root = self.get_root()
        w, h = root.get_width(), root.get_height()
        dlg = Adw.Dialog(child=view, content_width=int(w * 0.85), content_height=int(h * 0.9))

        def job():
            t = make_thumbnail(p, 2048)
            try:
                return Gdk.Texture.new_from_filename(t) if t else None
            except GLib.Error:
                return None
        run_background(job, lambda tex: pic.set_paintable(tex) if tex else None)
        dlg.present(root)

    def _cap_key(self, _c, keyval, _code, mods) -> bool:
        if keyval == Gdk.KEY_s and mods & Gdk.ModifierType.CONTROL_MASK:
            self.save_all()
            return True
        return False

    def _grid_key(self, _c, keyval, _code, mods) -> bool:
        if keyval == Gdk.KEY_Delete:
            self._trash_selected()
            return True
        if keyval == Gdk.KEY_a and mods & Gdk.ModifierType.CONTROL_MASK:
            self.sel.select_all()
            return True
        if keyval == Gdk.KEY_Escape:
            self.sel.unselect_all()
            return True
        return False

    # ================= writing =================

    def _refresh_cells(self) -> None:
        """Update badges / tooltips of the visible cells (items_changed would drop the selection)."""
        for path, ov in list(self._cells.items()):
            self._update_cell(path, ov)

    def _batch(self, op: str) -> None:
        self.save_current()
        files = self.targets()
        if not files:
            return
        if op == "add":
            new = split_tags(self.add_row.get_text())
            fn = lambda t: tagedit.add(t, new, self.add_first.get_active())
        elif op == "remove":
            drop = split_tags(self.remove_row.get_text())
            fn = lambda t: tagedit.remove(t, drop)
        elif op == "replace":
            find, repl = self.find_row.get_text().strip(), self.repl_row.get_text()
            if not find:
                return
            fn = lambda t: tagedit.replace(t, find, repl)
        elif op == "sort":
            fn = lambda t: sorted(t, key=str.lower)
        else:
            fn = lambda t: split_tags(", ".join(t))
        changed = {}
        for p in files:
            old = self.captions.get(p, [])
            nt = fn(old)
            if nt != old:
                changed[p] = nt
        if not changed:
            self._toast("Nothing to change.")
            return
        self._undo = {p: list(self.captions.get(p, [])) for p in changed}
        n = self._stage(changed)
        self.undo_btn.set_sensitive(True)
        self._show_caption()
        self._toast(f"Changed {n:,} captions · press Save All Tags to write them.")
        for row in (self.add_row, self.remove_row):
            if (op == "add" and row is self.add_row) or (op == "remove" and row is self.remove_row):
                row.set_text("")

    def _undo_batch(self) -> None:
        self.save_current()
        if self._undo:
            self._stage(self._undo)
            self._undo = None
            self.undo_btn.set_sensitive(False)
            self._show_caption()
            self._toast("Undone.")

    def _trash_selected(self) -> None:
        sel = self.selected()
        if not sel:
            return

        def answered(rid):
            if rid != "trash":
                return
            for p in sel:
                if desktop_trash(p):
                    sc = tagedit.sidecar(p)
                    if os.path.exists(sc):
                        desktop_trash(sc)
                    self.captions.pop(p, None)
                    self._disk.pop(p, None)
                    if self._undo:
                        self._undo.pop(p, None)
            self.current = None
            self._show_caption()
            self._apply_filter(now=True)
            self.state.content_changed.emit()
        alert(self.get_root(), f"Move {len(sel)} images to the Trash?", "Their captions go with them.",
              [("cancel", "Cancel"), ("trash", "Move to Trash")], destructive="trash", default="cancel",
              callback=answered)

    def _toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=2))

    # ================= tag list =================
    def _fill_tags(self) -> None:
        if not self._tags_id:                    # selection changes arrive in bursts (rubber-band, Ctrl+A)
            self._tags_id = GLib.timeout_add(120, self._fill_tags_now)

    def _fill_tags_now(self) -> bool:
        self._tags_id = 0
        self.tag_list.remove_all()
        files = self.targets()
        c = tagedit.counts(self.captions, files)
        q = self.tag_search.get_text().strip().lower()
        items = [(t, n) for t, n in c.most_common() if q in t.lower()] if q else c.most_common()
        for tag, n in items[:150]:
            row = Adw.ActionRow(title=GLib.markup_escape_text(tag), activatable=True)
            row.tag = tag
            row.add_suffix(Gtk.Label(label=f"{n:,}", css_classes=["dim-label", "numeric"]))
            row.add_suffix(icon_button("list-remove-symbolic", "Remove this tag from these images",
                                       lambda t=tag: self._remove_tag(t)))
            self.tag_list.append(row)
        self.tag_list.set_visible(bool(items))
        more = len(items) - 150
        self.tag_more.set_text(f"{more:,} more — type to find one." if more > 0 else
                               ("" if items else "No tags in these images yet."))
        return False

    def _remove_tag(self, tag: str) -> None:
        self.remove_row.set_text(tag)
        self._batch("remove")

    # ================= AI =================
    def _save_opts(self) -> None:
        s = self.state.settings
        s.tagger = TAGGERS[self.tagger.get_selected()][0]
        s.tag_threshold = round(self.threshold.get_value(), 2)
        s.joy_caption_type = list(joycaption.CAPTION_TYPE_MAP)[self.joy_type.get_selected()]
        s.joy_caption_length = joycaption.LENGTHS[self.joy_len.get_selected()]
        s.joy_name = self.joy_name.get_text().strip()
        s.joy_extra = [t for t, chk in self._extra_checks if chk.get_active()]
        s.joy_temperature = round(self.joy_temp.get_value(), 2)
        s.joy_max_tokens = int(self.joy_tokens.get_value())
        self._update_prompt()
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(600, self._save_settings)   # don't rewrite settings.json per keystroke

    _save_id = 0

    def _save_settings(self) -> bool:
        self._save_id = 0
        self.state.save()
        return False

    def _update_prompt(self) -> None:
        self.joy_prompt.set_subtitle(GLib.markup_escape_text(joycaption.prompt_for(self.state.settings)))

    def _model_status(self, *_a) -> None:
        # model lookups walk folders (slow on USB disks / big HF caches): do them off the main thread
        s, key = self.state.settings, self.state.settings.tagger
        self._status_tok = tok = getattr(self, "_status_tok", 0) + 1
        run_background(lambda: (tok, key, models.status(key, s), models.find_joycaption(s)), self._show_status)

    def _show_status(self, r) -> None:
        tok, key, found, j = r
        if tok != self._status_tok:
            return
        s = self.state.settings
        need = ["torch", "einops", "safetensors"] if key == "hydra" else ["torch", "timm", "safetensors"]
        missing = [m for m in need if not capabilities.has(m)]
        if missing:
            self.tag_note.set_text("Needs " + ", ".join(missing) + ": Preferences → System.")
        elif not found:
            self.tag_note.set_text(f"Model not found in {models.location(key, s)}. Set its folder or download it "
                                   "in Preferences → Models.")
        else:
            self.tag_note.set_text(f"Model: {found}")
        if not j:
            self.joy_note.set_text(f"JoyCaption not found in {models.location('joycaption', s)}. Set its folder or "
                                   "download it in Preferences → Models.")
        elif j["kind"] == "gguf" and not capabilities.has("llama_cpp"):
            self.joy_note.set_text("This GGUF model needs llama-cpp-python: Preferences → System.")
        elif j["kind"] == "hf" and not capabilities.has("accelerate"):
            self.joy_note.set_text("Needs accelerate + transformers: Preferences → System.")
        else:
            self.joy_note.set_text("Model: " + (j.get("model") or j["path"]) +
                                   (" · runs on the CPU (slow)" if j["kind"] == "gguf" else ""))

    def _busy(self, busy: bool) -> None:
        # the AI jobs write captions in the background: no manual edits or folder switches meanwhile
        for w in (self.batch, self.reload_btn, self.open_btn, self.lib_btn, self.recursive, self.cap_view,
                  self.trash_btn):
            w.set_sensitive(not busy)
        self.cap_view.set_sensitive(not busy and self.current is not None)
        self._selection_changed()

    def _autotag(self) -> None:
        self.save_current()
        files, s = self.targets(), self.state.settings
        mode = TAG_MODES[self.tag_mode.get_selected()][0]
        if mode == "skip":
            files = [f for f in files if not self.captions.get(f)]
        if not files:
            self._toast("Every image already has tags.")
            return
        key, folder = s.tagger, self.folder
        old = {f: list(self.captions.get(f, [])) for f in files}

        def job(ctx):
            res = taggers.tag_files(key, s, files, ctx)
            out = {}
            for f, tags in res.items():
                new = tagedit.merge(old[f], list(tags), mode)
                if new is not None and new != old[f]:
                    out[f] = new
            ctx.log(f"Tagged {len(out):,} images — press Save All Tags to write them.")
            return out

        def done(out):
            if self.folder != folder:
                return
            self._undo = {f: old[f] for f in out}
            self.undo_btn.set_sensitive(bool(out))
            self._stage(out)
            self._show_caption()
        self.panel.run(job, done, name="Auto-tagging")

    def _caption(self) -> None:
        self.save_current()
        files, s = self.targets(), self.state.settings
        mode = JOY_MODES[self.joy_mode.get_selected()][0]
        if mode == "skip":
            files = [f for f in files if not self.captions.get(f)]
        if not files:
            self._toast("Every image already has a caption.")
            return
        old = {f: list(self.captions.get(f, [])) for f in files}
        written = {}

        prose = "tag list" not in s.joy_caption_type
        folder = self.folder

        def one(f, text):                      # worker thread: stage each result as soon as it is ready
            got = [text.strip()] if prose else split_tags(text)
            if not got:
                return
            if mode == "replace":
                new = got
            else:
                new = old[f] + got if prose else tagedit.add(old[f], got)
            written[f] = new
            _once(self._caption_done, f, new)

        def done(_r):
            if self.folder != folder:
                return
            self._undo = {f: old[f] for f in written}
            self.undo_btn.set_sensitive(bool(written))
            self._fill_tags()
            self.panel.log(f"Captioned {len(written):,} images — press Save All Tags to write them.")
        self.panel.run(lambda ctx: joycaption.caption_files(s, files, ctx, one), done, name="JoyCaption")

    def _caption_done(self, f: str, new: list[str]) -> None:
        if f not in self.captions:             # folder was switched meanwhile
            return
        self._stage({f: new}, refresh=False)
        if f in self._cells:
            self._update_cell(f, self._cells[f])
        self.panel.log(f"{os.path.basename(f)}: {', '.join(new)[:300]}")
        if f == self.current and not self.cap_view.has_focus():
            self._show_caption()

    def _free(self) -> None:
        if self.panel.busy:
            return
        taggers.unload()
        joycaption.unload()
        self._toast("Models unloaded.")
