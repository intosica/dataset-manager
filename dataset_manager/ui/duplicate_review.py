from __future__ import annotations

import os

from gi.repository import Adw, Gdk, GLib, Gtk

from ..core.duplicates import apply_removals, scan_duplicates
from ..core.imaging import make_thumbnails
from ..core.utils import fmt_bytes
from .image_menu import attach_image_menu
from .widgets import alert, open_path


class DuplicateReview(Gtk.Box):
    """Scan a folder for look-alike images, then pick which copies go to the Trash (used by Maintenance)."""

    def __init__(self, state, panel):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        self.state, self.panel = state, panel
        self.checks: list[tuple[Gtk.CheckButton, str, str, int]] = []     # (check, keeper, duplicate, size)
        self.trash_btn = Gtk.Button(label="Move to Trash", sensitive=False)
        self.trash_btn.add_css_class("suggested-action")
        self.delete_btn = Gtk.Button(label="Delete Permanently", sensitive=False)
        self.delete_btn.add_css_class("destructive-action")
        self.close_btn = Gtk.Button(label="Close")
        self.selection_label = Gtk.Label(label="Nothing to review.", xalign=0, hexpand=True, css_classes=["dim-label"])
        actions = Gtk.Box(spacing=8)
        for w in (Gtk.Label(label="Duplicates", css_classes=["title-4"]), self.selection_label, self.close_btn,
                  self.trash_btn, self.delete_btn):
            actions.append(w)
        self.groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.append(actions)
        self.append(self.groups)
        self.trash_btn.connect("clicked", lambda _b: self.remove("trash"))
        self.delete_btn.connect("clicked", lambda _b: self.remove("delete"))
        self.close_btn.connect("clicked", lambda _b: (self._clear(), self.set_visible(False)))
        panel.on_busy(lambda _b: self._update_selection())

    def _clear(self) -> None:
        while (c := self.groups.get_first_child()) is not None:
            self.groups.remove(c)
        self.checks.clear()
        self._update_selection()

    def scan(self, base: str, thr: int) -> None:
        self._clear()
        self.set_visible(True)

        def job(ctx):
            groups = scan_duplicates(base, thr, "library", ctx)
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
            return groups, shown, thumbs, base

        if os.path.isdir(base):
            self.panel.run(job, self._show_groups, name="Duplicate scan")
        else:
            self.panel.log(f"Folder not found: {base}")

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
        attach_image_menu(row, lambda p=rec["path"]: p, self.state)
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
        groups, shown, thumbs, base = result
        for n, g in enumerate(shown, 1):
            grp = Adw.PreferencesGroup(title=f"Group {n}: {len(g.duplicates) + 1} images",
                                       description=f"{fmt_bytes(g.reclaimable)} reclaimable")
            grp.add(self._row(g.keeper, True, thumbs, base, g.keeper["path"]))
            for d in g.duplicates:
                grp.add(self._row(d, False, thumbs, base, g.keeper["path"]))
            self.groups.append(grp)
        if not shown:
            self.panel.log("No duplicates found.")
            self.set_visible(False)
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
                self.set_visible(False)
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
