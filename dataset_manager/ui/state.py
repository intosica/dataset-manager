from __future__ import annotations

import dataclasses

from ..core.settings import Settings
from ..core.upscaler import get_upscaler


class Signal:
    """Minimal callback list; every handler runs on the GTK main thread."""

    def __init__(self):
        self._handlers = []

    def connect(self, fn) -> None:
        self._handlers.append(fn)

    def emit(self, *args) -> None:
        for fn in list(self._handlers):
            fn(*args)


class AppState:
    """Shared settings and change notifications for all pages."""

    def __init__(self):
        self.settings = Settings.load()
        self.settings_changed = Signal()   # preferences or options changed
        self.library_changed = Signal()    # a different library folder was chosen
        self.content_changed = Signal()    # files in the library were added / moved / removed
        self.library_selected = Signal()   # a collection row was picked on the Library page
        self.open_in_tool = Signal()       # ("scorer" | "quick", image path): right-click → open in a Tag Tools tool
        self.selected_collection = ""
        self._runtime = None
        self._saved = dataclasses.asdict(self.settings)
        self.apply_runtime()

    def apply_runtime(self) -> None:
        key = (self.settings.upscale_model, tuple(self.settings.all_model_dirs()))
        if key != self._runtime:           # rescanning model folders on every unrelated save is slow on USB disks
            self._runtime = key
            get_upscaler().configure(*key[:1], list(key[1]))

    def save(self) -> None:
        now = dataclasses.asdict(self.settings)
        if now == self._saved:             # nothing changed: don't make every page reload
            return
        try:
            self.settings.save()
            self._saved = now               # only after it really reached the disk, so a failed save retries
        except OSError:
            pass
        self.apply_runtime()
        self.settings_changed.emit()

    @property
    def library(self) -> str:
        return self.settings.library_path()

    def set_library(self, path: str) -> None:
        self.settings.library_dir = path
        self.save()
        self.library_changed.emit()
