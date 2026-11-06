"""Tag Tools extras: the Aesthetic Scorer and Quick Caption (a throw-away one-image Tag Editor)."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading

from gi.repository import Gtk

from ..core import capabilities
from ..core.aesthetic_score import NOTE, AestheticFilter, AestheticSpec
from ..core.utils import IMAGE_EXTS
from .image_menu import ImageDrop
from .tag_editor import TagEditor
from .widgets import NoteRow, hint, icon_button, run_background

_lock = threading.Lock()
_filter: AestheticFilter | None = None


def score_image(path: str) -> float:
    """Aesthetic score (1–10) of one image; loads the model on first use."""
    global _filter
    from PIL import Image
    if not AestheticFilter.available():
        raise RuntimeError("Needs aesthetic-predictor-v2-5 and torch: Preferences → System.")
    with _lock:
        _filter = _filter or AestheticFilter(AestheticSpec())
        if not _filter._load():
            raise RuntimeError("The aesthetic model could not be loaded. Install it in Preferences → Models.")
        with Image.open(path) as im:
            return _filter.score(im.convert("RGB"))


def verdict(score: float) -> str:
    return ("Low quality" if score < 4 else "Average" if score < 5.5 else "Good" if score < 6.5 else "Striking")


class AestheticScorer(Gtk.Box):
    def __init__(self, state):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=18, margin_bottom=14,
                         margin_start=18, margin_end=18)
        self._tok = 0
        self.drop = ImageDrop(self.open_path, "Aesthetic Scorer",
                              "Click to choose an image, or drop one here from anywhere, to rate how good it looks.")
        self.score = Gtk.Label(label="", css_classes=["title-1", "numeric"])
        self.verdict = Gtk.Label(label="", css_classes=["dim-label"])
        self.note = NoteRow("" if AestheticFilter.available() else
                            "Needs aesthetic-predictor-v2-5 and torch: Preferences → System.")
        self.note.set_visible(not AestheticFilter.available())
        self.append(hint(f"Scores one image with the aesthetic model: {NOTE}. Right-click any image in the app and "
                         "choose “View in Aesthetic Scorer” to send it here."))
        self.append(self.drop)
        for w in (self.score, self.verdict, self.note):
            self.append(w)

    def open_path(self, path: str) -> None:
        self.drop.set_path(path)
        self._tok += 1
        tok = self._tok
        self.score.set_label("…")
        self.verdict.set_label("Scoring (the first run loads the model and can take a while)…")

        def done(v):
            if tok == self._tok:
                self.score.set_label(f"{v:.2f}")
                self.verdict.set_label(f"{verdict(v)} · {os.path.basename(path)}")

        def failed(exc):
            if tok == self._tok:
                self.score.set_label("—")
                self.verdict.set_label(str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
        run_background(lambda: score_image(path), done, failed)


class QuickCaption(TagEditor):
    """The Tag Editor on a single temporary copy of an image: nothing is saved, tags are only for reading/copying."""

    def __init__(self, state):
        self._tmp = ""
        super().__init__(state)
        toolbar = self.get_first_child()
        toolbar.set_visible(False)
        toolbar.get_next_sibling().set_visible(False)             # its separator
        for w in (self.save_btn, self.revert_btn, self.trash_btn, self.preview, self.batch, self.nav):
            w.set_visible(False)
        self.cap_row.append(icon_button("edit-copy-symbolic", "Copy the tags", self._copy, flat=False))
        self.drop = ImageDrop(self.set_image, "Quick Caption",
                              "Click to choose an image, or drop one here from anywhere. Tag or caption it "
                              "with the tools on the right; nothing is saved.")
        self.left.add_named(self.drop, "drop")
        self.left.set_visible_child_name("drop")

    # the folder / library plumbing of the Tag Editor does not apply here
    def _first_map(self, _w) -> None: pass
    def _library_selected(self, _path) -> None: pass
    def _trash_selected(self) -> None: pass

    def _flush(self) -> None:
        super()._flush()
        self._cleanup()

    def _cleanup(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = ""

    def set_image(self, path: str) -> None:
        if self.panel.busy:
            self._toast("Wait for the running job to finish.")
            return
        self._cleanup()
        self._tmp = tempfile.mkdtemp(prefix="dataset-manager-quick-")
        name = os.path.basename(path)
        dst = os.path.join(self._tmp, name)
        try:
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                shutil.copyfile(path, dst)
            else:                                                  # gif / avif / tiff …: the taggers want a plain format
                from PIL import Image
                dst = os.path.splitext(dst)[0] + ".png"
                with Image.open(path) as im:
                    im.convert("RGB").save(dst)
        except Exception as exc:
            self._toast(f"Could not open that image: {exc}")
            return
        self.drop.set_path(dst)
        self.folder = self._tmp
        self._para.clear()
        self.reload()

    def _loaded(self, result) -> None:
        super()._loaded(result)
        self.left.set_visible_child_name("drop")
        if self.store.get_n_items():
            self.sel.select_item(0, True)

    def _copy(self) -> None:
        self.save_current()
        text = self._join(self.current)
        if text:
            self.get_clipboard().set(text)
            self._toast("Copied to the clipboard.")
