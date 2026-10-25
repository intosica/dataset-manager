from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.settings import Settings
from ..core.upscaler import get_upscaler


class AppState(QObject):
    """Shared settings and change notifications for all pages."""

    settingsChanged = Signal()   # preferences or options changed
    libraryChanged = Signal()    # a different library folder was chosen
    contentChanged = Signal()    # files in the library were added / moved / removed

    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.apply_runtime()

    def apply_runtime(self) -> None:
        get_upscaler().configure(self.settings.upscale_model, self.settings.all_model_dirs())

    def save(self) -> None:
        try:
            self.settings.save()
        except OSError:
            pass
        self.apply_runtime()
        self.settingsChanged.emit()

    @property
    def library(self) -> str:
        return self.settings.library_path()

    def set_library(self, path: str) -> None:
        self.settings.library_dir = path
        self.save()
        self.libraryChanged.emit()
