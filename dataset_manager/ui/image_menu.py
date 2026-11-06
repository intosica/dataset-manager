"""Small image helpers shared by every page: a right-click menu ("View in Aesthetic Scorer" / "Open in Quick Caption")
and a click-or-drop image picker."""
from __future__ import annotations

import os

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..core.imaging import make_thumbnail
from .widgets import run_background

IMAGE_FILTER_PATTERNS = ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp", "*.gif", "*.avif", "*.tif", "*.tiff")


def popup(widget: Gtk.Widget, x: float, y: float, items: list[tuple[str, object]]) -> None:
    """Flat-button popover at the pointer; `items` is [(label, callback)]."""
    pop = Gtk.Popover(has_arrow=False, halign=Gtk.Align.START)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
    for label, cb in items:
        b = Gtk.Button(label=label, css_classes=["flat"])
        b.get_child().set_halign(Gtk.Align.START)
        b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
        box.append(b)
    pop.set_child(box)
    pop.set_parent(widget)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    pop.set_pointing_to(rect)
    pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
    pop.popup()


def tool_items(state, path: str) -> list[tuple[str, object]]:
    return [("View in Aesthetic Scorer", lambda: state.open_in_tool.emit("scorer", path)),
            ("Open in Quick Caption", lambda: state.open_in_tool.emit("quick", path))]


def attach_image_menu(widget: Gtk.Widget, get_path, state, extras=None, before=None) -> None:
    """Right-click `widget` → menu for the image `get_path()` returns (nothing when it returns None).
    `extras()` may add more (label, callback) entries first; `before()` runs on the press (e.g. to select the item)."""
    g = Gtk.GestureClick(button=3)

    def pressed(gesture, _n, x, y):
        path = get_path()
        if not path or not os.path.isfile(path):
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if before:
            before()
        popup(widget, x, y, (extras() if extras else []) + tool_items(state, path))
    g.connect("pressed", pressed)
    widget.add_controller(g)


class ImageDrop(Gtk.Stack):
    """Click to choose an image, or drop one from anywhere on the system. Calls `on_image(path)`."""

    def __init__(self, on_image, title="Drop an image here", description="or click to choose one"):
        super().__init__(hexpand=True, vexpand=True, css_classes=["card"])
        self.on_image, self._tok = on_image, 0
        self.add_named(Adw.StatusPage(icon_name="insert-image-symbolic", title=title, description=description),
                       "empty")
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, margin_top=6, margin_bottom=6,
                               margin_start=6, margin_end=6)
        self.add_named(self.pic, "image")
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda *_a: self.choose())
        self.add_controller(click)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._dropped)
        drop.connect("enter", lambda *_a: (self.add_css_class("drop-active"), Gdk.DragAction.COPY)[1])
        drop.connect("leave", lambda *_a: self.remove_css_class("drop-active"))
        self.add_controller(drop)

    def choose(self) -> None:
        dlg = Gtk.FileDialog(title="Choose an Image")
        flt = Gtk.FileFilter(name="Images")
        for p in IMAGE_FILTER_PATTERNS:
            flt.add_pattern(p)
        dlg.set_default_filter(flt)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.on_image(f.get_path())
        dlg.open(self.get_root(), None, done)

    def _dropped(self, _t, value, _x, _y) -> bool:
        self.remove_css_class("drop-active")
        files = [f.get_path() for f in value.get_files() if f.get_path()]
        if not files:
            return False
        self.on_image(files[0])
        return True

    def set_path(self, path: str | None) -> None:
        self._tok += 1
        tok = self._tok
        if not path:
            self.set_visible_child_name("empty")
            return

        def job():
            t = make_thumbnail(path, 1600)
            try:
                return tok, Gdk.Texture.new_from_filename(t) if t else None
            except GLib.Error:
                return tok, None

        def done(r):
            if r[0] == self._tok and r[1] is not None:
                self.pic.set_paintable(r[1])
                self.set_visible_child_name("image")
        run_background(job, done)
