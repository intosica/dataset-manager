from __future__ import annotations

import os

from gi.repository import Adw, Gdk, GLib, Gtk

from ..core.duplicates import HASH_BITS, apply_removals, scan_duplicates
from ..core.imaging import make_thumbnails
from ..core.utils import fmt_bytes
from .widgets import TaskPanel, alert, open_path


def _threshold_text(v: int) -> str:
    if v == 0:
        return "Exact copies only"
    return f"{v} of {HASH_BITS} bits" + (" (recommended)" if v == 6 else "")


class DuplicatesPage(Gtk.Box):
    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.state = state
        self.checks: list[tuple[Gtk.CheckButton, str, str]] = []     # (check, keeper path, duplicate path)

        self.thr = Adw.SpinRow.new_with_range(0, 20, 1)
        self.thr.set_title("Similarity")
        self.thr.set_value(state.settings.duplicate_threshold)
        self.thr.set_subtitle(_threshold_text(int(self.thr.get_value())))
        self.thr.connect("notify::value", lambda *_: self.thr.set_subtitle(_threshold_text(int(self.thr.get_value()))))
        self.scope = Adw.ComboRow(title="Compare", model=Gtk.StringList.new(["Whole library", "Within each collection"]))
        opts = Adw.PreferencesGroup(title="Find duplicates")
        opts.add(self.thr)
        opts.add(self.scope)

        self.scan_btn = Gtk.Button(label="Scan")
        self.scan_btn.add_css_class("suggested-action")
        self.cancel_btn = Gtk.Button(label="Cancel", sensitive=False)
        self.trash_btn = Gtk.Button(label="Move to Trash", sensitive=False)
        self.delete_btn = Gtk.Button(label="Delete Permanently", sensitive=False)
        self.delete_btn.add_css_class("destructive-action")
        self.selection_label = Gtk.Label(label="Nothing to review.", xalign=0, hexpand=True)
        self.selection_label.add_css_class("dim-label")
        actions = Gtk.Box(spacing=8, margin_top=6)
        for w in (self.scan_btn, self.cancel_btn, self.selection_label, self.trash_btn, self.delete_btn):
            actions.append(w)

        self.groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.panel = TaskPanel(min_height=90)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top.append(opts)
        top.append(actions)
        top.append(self.groups)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    child=Adw.Clamp(maximum_size=960, child=top, margin_top=18, margin_bottom=18,
                                                    margin_start=12, margin_end=12))
        bottom = Gtk.Box(margin_start=18, margin_end=18, margin_bottom=12)
        self.panel.set_hexpand(True)
        bottom.append(self.panel)
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, resize_end_child=False,
                          shrink_start_child=False, shrink_end_child=False, start_child=scroll, end_child=bottom,
                          position=560)
        self.append(paned)

        self.scan_btn.connect("clicked", lambda _b: self.scan())
        self.cancel_btn.connect("clicked", lambda _b: self.panel.cancel())
        self.trash_btn.connect("clicked", lambda _b: self.remove("trash"))
        self.delete_btn.connect("clicked", lambda _b: self.remove("delete"))
        self.panel.on_busy(self._busy)

    def _busy(self, busy: bool) -> None:
        self.scan_btn.set_sensitive(not busy)
        self.cancel_btn.set_sensitive(busy)
        self._update_selection()

    # ---- scanning ----
    def _clear(self) -> None:
        while (c := self.groups.get_first_child()) is not None:
            self.groups.remove(c)
        self.checks.clear()

    def scan(self) -> None:
        base = self.state.library
        thr, scope = int(self.thr.get_value()), ("library", "collection")[self.scope.get_selected()]
        self.state.settings.duplicate_threshold = thr
        self.state.save()
        self._clear()
        self._update_selection()

        def job(ctx):
            groups = scan_duplicates(base, thr, scope, ctx)
            shown, rows = [], 0
            for g in groups[:300]:                # keep the results view light: at most ~1500 rows
                rows += len(g.duplicates) + 1
                if shown and rows > 1500:
                    break
                shown.append(g)
            files = [r["path"] for g in shown for r in [g.keeper] + g.duplicates]
            if files:
                ctx.log("Preparing previews…")
            thumbs = {}
            for f, t in zip(files, make_thumbnails(files, 128)):
                if t:
                    try:
                        thumbs[f] = Gdk.Texture.new_from_filename(t)   # decode here, not on the main thread
                    except GLib.Error:
                        pass
            return groups, shown, thumbs

        if os.path.isdir(base):
            self.panel.run(job, self._show_groups, name="Duplicate scan")
        else:
            self.panel.clear()
            self.panel.log(f"Library folder not found: {base}")

    def _row(self, rec, keep: bool, thumbs, base, keeper_path) -> Adw.ActionRow:
        rel = os.path.relpath(os.path.dirname(rec["path"]), base)
        row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(rec["path"])),
                            subtitle=GLib.markup_escape_text(f"{rec['w']} × {rec['h']} · {fmt_bytes(rec['size'])} · {rel}"),
                            activatable=True, tooltip_text=rec["path"])
        t = thumbs.get(rec["path"])
        if t:
            pic = Gtk.Picture.new_for_paintable(t)
            pic.set_size_request(64, 64)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.add_css_class("thumb")
            row.add_prefix(pic)
        row.connect("activated", lambda _r, p=rec["path"]: open_path(p))
        if keep:
            tag = Gtk.Label(label="Keep")
            tag.add_css_class("success")
            row.add_suffix(tag)
        else:
            chk = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER)
            chk.connect("toggled", lambda _c: self._update_selection())
            row.add_suffix(chk)
            self.checks.append((chk, keeper_path, rec["path"], rec.get("size", 0)))
        return row

    def _show_groups(self, result) -> None:
        groups, shown, thumbs = result
        base = self.state.library
        for n, g in enumerate(shown, 1):
            grp = Adw.PreferencesGroup(title=f"Group {n}: {len(g.duplicates) + 1} images",
                                       description=f"{fmt_bytes(g.reclaimable)} reclaimable")
            grp.add(self._row(g.keeper, True, thumbs, base, g.keeper["path"]))
            for d in g.duplicates:
                grp.add(self._row(d, False, thumbs, base, g.keeper["path"]))
            self.groups.append(grp)
        if not shown:
            self.panel.log("No duplicates found.")
        if len(groups) > len(shown):
            self.panel.log(f"Showing the {len(shown)} largest of {len(groups)} groups. "
                           "Remove these and scan again to continue.")
        self._update_selection()

    # ---- selection ----
    def _checked(self) -> list[tuple[str, list[str]]]:
        out: dict[str, list[str]] = {}
        for chk, keeper, dup, _size in self.checks:
            if chk.get_active():
                out.setdefault(keeper, []).append(dup)
        return list(out.items())

    def _update_selection(self) -> None:
        sel = self._checked()
        n = sum(len(d) for _, d in sel)
        size = sum(c[3] for c in self.checks if c[0].get_active())     # sizes from the scan: no stat() per click
        self.selection_label.set_text(f"{n} images selected · {fmt_bytes(size)}" if n else
                                      ("No images selected." if self.checks else "Nothing to review."))
        enabled = n > 0 and not self.panel.busy
        self.trash_btn.set_sensitive(enabled)
        self.delete_btn.set_sensitive(enabled)

    # ---- removal ----
    def remove(self, mode: str) -> None:
        sel = self._checked()
        n = sum(len(d) for _, d in sel)
        if not n:
            return

        def go() -> None:
            def done(res) -> None:
                self.panel.log(f"{'Deleted' if mode == 'delete' else 'Moved to Trash'}: {res.removed} images "
                               f"({fmt_bytes(res.freed)})" + (f", {res.failed} failed" if res.failed else ""))
                self._clear()
                self._update_selection()
                self.state.content_changed.emit()
            self.panel.run(lambda ctx: apply_removals(sel, mode, ctx), done, with_trash=(mode == "trash"),
                           name="Removing duplicates")

        if mode == "delete":
            alert(self.get_root(), "Delete permanently?",
                  f"{n} images and their caption files will be deleted. This cannot be undone.",
                  [("cancel", "Cancel"), ("delete", "Delete")], destructive="delete", default="cancel",
                  callback=lambda rid: go() if rid == "delete" else None)
        else:
            go()
