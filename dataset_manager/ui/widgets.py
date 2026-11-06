"""Shared GTK building blocks: background tasks, log panel, path / model / caption rows."""
from __future__ import annotations

import os
import threading
import traceback

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..core import capabilities, paths
from ..core import trash as core_trash
from ..core.captions import PLACEHOLDERS, CaptionSpec, render_caption
from ..core.sources.filters import FILTERS
from ..core.tasks import TaskContext
from ..core.upscaler import MODEL_EXTS, discover_models, get_upscaler

CSS = """
.chip { border-radius: 999px; padding: 2px 12px; min-height: 26px; }
.chip:checked { background: @accent_bg_color; color: @accent_fg_color; }
.swatch { border-radius: 999px; min-width: 22px; min-height: 22px; padding: 0; border: 2px solid alpha(currentColor, .25); }
.swatch:checked { border-color: @accent_bg_color; box-shadow: 0 0 0 2px @accent_bg_color; }
.log { font-family: monospace; font-size: 0.9em; padding: 6px; }
.drop-active { background: alpha(@accent_bg_color, .25); }
.thumb { border-radius: 8px; }
.score-badge { border-radius: 0 0 8px 0; padding: 2px 8px; margin: 0; }
.sessions row.folder-row { font-weight: bold; }
"""


def _swatch_css() -> str:
    colours = {sw for groups in FILTERS.values() for _k, _l, opts in groups for _o, _v, sw in opts if sw}
    return "".join(f"button.swatch.sw{c.lstrip('#')} {{ background: {c}; }}\n" for c in sorted(colours))


def install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS + _swatch_css())
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def hint(text: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True)
    label.add_css_class("dim-label")
    label.add_css_class("caption")
    return label


class NoteRow(Adw.PreferencesRow):
    """A wrapping paragraph row — a long text in a row subtitle is ellipsized into unreadability."""

    def __init__(self, text: str = ""):
        super().__init__()
        self.label = hint(text)
        self.label.set_selectable(True)
        for side in ("top", "bottom"):
            getattr(self.label, f"set_margin_{side}")(6)
        self.label.set_margin_start(12)
        self.label.set_margin_end(12)
        self.set_child(self.label)

    def set_text(self, text: str) -> None:
        self.label.set_label(text)


def icon_button(icon: str, tooltip: str, callback=None, flat: bool = True) -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    if flat:
        b.add_css_class("flat")
    if callback:
        b.connect("clicked", lambda _b: callback())
    return b


def desktop_trash(path: str) -> bool:
    """GIO trash (uses the portal inside Flatpak), then command-line helpers."""
    try:
        if Gio.File.new_for_path(path).trash(None):
            return True
    except GLib.Error:
        pass
    return core_trash.move_to_trash(path)


def _once(fn, *args):
    GLib.idle_add(lambda: (fn(*args), False)[1])


def run_background(fn, on_result, on_error=None) -> None:
    """Short read-only job on a thread; on_result (or on_error(exc)) runs on the main thread."""
    def work():
        try:
            result = fn()
        except Exception as exc:
            traceback.print_exc()
            if on_error:
                _once(on_error, exc)
            return
        _once(on_result, result)
    threading.Thread(target=work, daemon=True).start()


def alert(parent, heading: str, body: str, responses: list, destructive: str = "", default: str = "", callback=None) -> None:
    """responses: [(id, label)]. callback(id) runs when answered."""
    dlg = Adw.AlertDialog.new(heading, body)
    for rid, label in responses:
        dlg.add_response(rid, label)
    if destructive:
        dlg.set_response_appearance(destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    if default:
        dlg.set_default_response(default)
        dlg.set_close_response(responses[0][0])

    def done(d, res):
        if callback:
            callback(d.choose_finish(res))
    dlg.choose(parent, None, done)


class PathRow(Adw.EntryRow):
    """Entry row with a Browse button that opens the native (portal) file chooser."""

    def __init__(self, title: str, mode: str = "dir", placeholder: str = ""):
        super().__init__(title=title)
        self.mode, self.placeholder = mode, placeholder
        btn = icon_button("folder-open-symbolic", "Browse…", self._browse)
        self.add_suffix(btn)

    def path(self) -> str:
        return paths.clean_user_path(self.get_text())

    def _browse(self) -> None:
        dlg = Gtk.FileDialog()
        cur = self.path()
        folder = cur if self.mode == "dir" else os.path.dirname(cur)
        if folder and os.path.isdir(folder):
            dlg.set_initial_folder(Gio.File.new_for_path(folder))

        def done(d, res):
            try:
                f = d.select_folder_finish(res) if self.mode == "dir" else d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.set_text(f.get_path())
        (dlg.select_folder if self.mode == "dir" else dlg.open)(self.get_root(), None, done)


class TaskPanel(Gtk.Box):
    """Progress bar + log for one long-running task. Only one task may run at a time app-wide."""

    active: "TaskPanel | None" = None

    def __init__(self, min_height: int = 140):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.bar = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        self.status = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("dim-label")
        top = Gtk.Box(spacing=12)
        top.append(self.bar)
        top.append(self.status)
        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(buffer=self.buffer, editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.view.add_css_class("log")
        self.scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=min_height, child=self.view)
        self.scroll.add_css_class("card")
        self.append(top)
        self.append(self.scroll)
        self.busy_callbacks = []
        self._thread = None
        self._ctx = None
        self._pulse_id = 0
        self._indeterminate = False
        self._on_success = None
        self.name = ""

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def on_busy(self, fn) -> None:
        self.busy_callbacks.append(fn)

    def log(self, msg: str) -> None:
        end = self.buffer.get_end_iter()
        self.buffer.insert(end, msg + "\n")
        if self.buffer.get_line_count() > 5000:
            self.buffer.delete(self.buffer.get_start_iter(), self.buffer.get_iter_at_line(1000))
        adj = self.scroll.get_vadjustment()
        GLib.idle_add(lambda: (adj.set_value(adj.get_upper() - adj.get_page_size()), False)[1])

    def clear(self) -> None:
        self.buffer.set_text("")

    def run(self, fn, on_success=None, with_trash: bool = False, name: str = "") -> bool:
        if TaskPanel.running() is not None:
            alert(self.get_root(), "Task in progress",
                  f"“{TaskPanel.active.name}” is still running. Wait for it to finish or cancel it first.",
                  [("ok", "OK")])
            return False
        self.clear()
        self.status.set_text("")
        self.set_visible(True)
        self.name = name or "A task"
        self._on_success = on_success
        ctx = TaskContext(lambda m: _once(self.log, m), lambda d, t, x: _once(self._progress, d, t, x),
                          trash=desktop_trash if with_trash else None)
        self._ctx = ctx
        TaskPanel.active = self
        self._set_indeterminate(True)
        for cb in self.busy_callbacks:
            cb(True)

        self._gen = gen = getattr(self, "_gen", 0) + 1

        def work():
            result, err, ok = None, None, False
            try:
                result = fn(ctx)
                ok = True
            except BaseException:          # CancelledError & co. aren't Exceptions; the panel must still reset
                err = None if ctx.cancelled else traceback.format_exc()
            finally:
                _once(self._finished, result, err, ok, gen)
        self._thread = threading.Thread(target=work, daemon=True, name=self.name)
        self._thread.start()
        return True

    @classmethod
    def running(cls) -> "TaskPanel | None":
        """The panel whose task is really still running (a dead thread never counts)."""
        p = cls.active
        if p is not None and (p._thread is None or not p._thread.is_alive()):
            return None
        return p

    def cancel(self) -> None:
        if self._ctx and not self._ctx.cancelled:
            self.log("Cancelling…")
            self._ctx.cancel()

    @classmethod
    def cancel_active(cls) -> None:
        if cls.active:
            cls.active.cancel()

    @classmethod
    def wait_active(cls, seconds: float = 8.0) -> None:
        if cls.active and cls.active._thread:
            cls.active._thread.join(seconds)

    def _set_indeterminate(self, on: bool) -> None:
        self._indeterminate = on
        if on and not self._pulse_id:
            self._pulse_id = GLib.timeout_add(120, self._pulse)

    def _pulse(self) -> bool:
        if not self._indeterminate:
            self._pulse_id = 0
            return False
        self.bar.pulse()
        return True

    def _progress(self, done: int, total: int, text: str) -> None:
        if total <= 0:
            self._set_indeterminate(True)
        else:
            self._set_indeterminate(False)
            self.bar.set_fraction(min(1.0, done / total))
        self.status.set_text(text)

    def _finished(self, result, err, ok=True, gen=None) -> None:
        if gen is not None and gen != getattr(self, "_gen", gen):
            return                          # stale: a newer run already owns this panel
        self._thread = self._ctx = None
        if TaskPanel.active is self:
            TaskPanel.active = None
        self._set_indeterminate(False)
        self.bar.set_fraction(0)
        self.status.set_text("")
        try:
            if err:
                self.log("An unexpected error occurred:\n" + err)
            elif not ok:
                self.log("Cancelled.")
            elif self._on_success:
                self._on_success(result)
        except Exception:
            self.log("An unexpected error occurred:\n" + traceback.format_exc())
        finally:
            for cb in self.busy_callbacks:
                cb(False)


class CaptionEditor(Adw.PreferencesGroup):
    """Template + quality tags + extra tags with a live preview."""

    def __init__(self):
        super().__init__(title="Captions", description=f"Placeholders: {PLACEHOLDERS}")
        self.changed = []
        self.template = Adw.EntryRow(title="Template")
        self.extra = Adw.EntryRow(title="Extra tags (e.g. anime screencap)")
        self.good = Adw.EntryRow(title="Normal quality tags")
        self.low = Adw.EntryRow(title="Low quality tags")
        self.preview = Adw.ActionRow(title="Preview", subtitle_selectable=True)
        self.preview.add_css_class("property")
        for w in (self.template, self.extra, self.good, self.low, self.preview):
            self.add(w)
        for w in (self.template, self.extra, self.good, self.low):
            w.connect("changed", lambda _w: self._update())

    def load(self, spec: CaptionSpec) -> None:
        for w, v in ((self.template, spec.template), (self.extra, spec.extra_tags),
                     (self.good, spec.good_tags), (self.low, spec.low_tags)):
            if w.get_text() != v:
                w.set_text(v)
        self._update()

    def spec(self) -> CaptionSpec:
        return CaptionSpec(self.template.get_text(), self.good.get_text(), self.low.get_text(), self.extra.get_text())

    def _update(self) -> None:
        s = self.spec()
        text = (f"{render_caption(s, 'my_collection', 'Landscape') or '(empty)'}\n"
                f"{render_caption(s, 'my_collection', 'Portrait', True) or '(empty)'}")
        self.preview.set_subtitle(GLib.markup_escape_text(text))
        for fn in self.changed:
            fn()


class ModelPicker(Adw.ComboRow):
    """Pick the AI upscale model: discovered models, or any file via the native file dialog."""

    def __init__(self, state):
        super().__init__(title="Upscale model")
        self.state = state
        self._values: list[str] = []
        self._busy = False
        self.add_suffix(icon_button("document-open-symbolic", "Choose a model file…", self._browse))
        self.test_btn = icon_button("emblem-ok-symbolic", "Test the model", self._test)
        self.add_suffix(self.test_btn)
        self.connect("notify::selected", self._chosen)
        state.settings_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        s = self.state.settings
        models = discover_models(s.all_model_dirs())
        cur = s.upscale_model
        if cur and cur != "none":
            cur_path = paths.resolve(cur)
            if cur_path not in models:
                models.append(cur_path)
        self._values = ["", "none"] + models
        labels = ["Automatic (first model found)", "None (standard resampling only)"] + \
                 [f"{os.path.basename(p)}  ({os.path.dirname(p)})" for p in models]
        self._busy = True
        self.set_model(Gtk.StringList.new(labels))
        want = cur if cur in ("", "none") else paths.resolve(cur)
        self.set_selected(self._values.index(want) if want in self._values else 0)
        self._busy = False
        self._set_status()

    def _set_status(self, text: str | None = None) -> None:
        if text is None:
            up = get_upscaler()
            if not (capabilities.has("torch") and capabilities.has("spandrel")):
                text = "AI upscaling needs torch and spandrel; standard resampling is used until then."
            elif up.model_path:
                text = up.model_path
            else:
                text = "No model selected: standard resampling is used."
        self.set_subtitle(GLib.markup_escape_text(text))

    def _chosen(self, *_a) -> None:
        if self._busy or not self._values:
            return
        self.state.settings.upscale_model = self._values[self.get_selected()]
        self.state.save()

    def _browse(self) -> None:
        cur = self.state.settings.upscale_model
        dlg = Gtk.FileDialog()
        flt = Gtk.FileFilter()
        flt.set_name("Upscale models")
        for e in MODEL_EXTS:
            flt.add_suffix(e.lstrip("."))
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)
        if cur not in ("", "none") and os.path.isdir(os.path.dirname(cur)):
            dlg.set_initial_folder(Gio.File.new_for_path(os.path.dirname(cur)))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            self.state.settings.upscale_model = f.get_path()
            self.state.save()
        dlg.open(self.get_root(), None, done)

    def _test(self) -> None:
        self._set_status("Loading model…")
        self.test_btn.set_sensitive(False)

        def done(result):
            self.test_btn.set_sensitive(True)
            self._set_status(("OK: " if result[0] else "Problem: ") + result[1])
        run_background(get_upscaler().check, done)


def open_path(path: str) -> None:
    """Open a file or folder with the desktop's default handler (portal-aware inside Flatpak)."""
    Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(None, None, None)


def page(child: Gtk.Widget, width: int = 860) -> Gtk.ScrolledWindow:
    """Scrollable, width-limited page body, the standard libadwaita form layout."""
    child.set_margin_top(18)
    child.set_margin_bottom(18)
    child.set_margin_start(12)
    child.set_margin_end(12)
    return Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                              child=Adw.Clamp(maximum_size=width, tightening_threshold=600, child=child))
