from __future__ import annotations

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
        self.apply_runtime()

    def apply_runtime(self) -> None:
        get_upscaler().configure(self.settings.upscale_model, self.settings.all_model_dirs())

    def save(self) -> None:
        try:
            self.settings.save()
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
