from __future__ import annotations

from gi.repository import Gdk, Gtk

from ..core.tags import process_tags
from .widgets import hint


class TagsPage(Gtk.Box):
    """Paste a comma-separated tag list, press Process, and the same box shows the cleaned result."""

    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=18, margin_bottom=14,
                         margin_start=18, margin_end=18)
        self._previous: str | None = None
        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=8,
                                 right_margin=8)
        self.buf = self.view.get_buffer()
        self.info = Gtk.Label(xalign=0, hexpand=True)
        self.info.add_css_class("dim-label")
        self.process_btn = Gtk.Button(label="Process Tags")
        self.process_btn.add_css_class("suggested-action")
        self.undo_btn = Gtk.Button(label="Undo", sensitive=False)
        copy_btn, clear_btn = Gtk.Button(label="Copy"), Gtk.Button(label="Clear")
        row = Gtk.Box(spacing=8)
        row.append(self.info)
        for b in (clear_btn, copy_btn, self.undo_btn, self.process_btn):
            row.append(b)

        frame = Gtk.ScrolledWindow(vexpand=True, child=self.view)
        frame.add_css_class("card")
        self.append(hint("Replaces spaces inside a tag with underscores, removes all commas and tidies spacing. "
                         "Example: “white dog, a huge bike, 1girl” becomes “white_dog a_huge_bike 1girl”. "
                         "Each line is processed separately. Ctrl+Enter processes."))
        self.append(frame)
        self.append(row)

        self.process_btn.connect("clicked", lambda _b: self.process())
        self.undo_btn.connect("clicked", lambda _b: self.undo())
        copy_btn.connect("clicked", lambda _b: self.copy())
        clear_btn.connect("clicked", lambda _b: self.clear())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.view.add_controller(keys)

    def _key(self, _c, keyval, _code, mods) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and mods & Gdk.ModifierType.CONTROL_MASK:
            self.process()
            return True
        return False

    def _text(self) -> str:
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)

    def process(self) -> None:
        original = self._text()
        if not original.strip():
            return
        result = process_tags(original)
        if result != original:
            self._previous = original
            self.buf.set_text(result)
            self.undo_btn.set_sensitive(True)
        self.info.set_text(f"{len(result.split())} tags")

    def undo(self) -> None:
        if self._previous is not None:
            self.buf.set_text(self._previous)
            self._previous = None
            self.undo_btn.set_sensitive(False)
            self.info.set_text("")

    def copy(self) -> None:
        self.get_clipboard().set(self._text())
        self.info.set_text("Copied to clipboard")

    def clear(self) -> None:
        self._previous = self._text() or None
        self.buf.set_text("")
        self.undo_btn.set_sensitive(self._previous is not None)
        self.info.set_text("")
