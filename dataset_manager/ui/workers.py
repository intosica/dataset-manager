from __future__ import annotations

import traceback

from PySide6.QtCore import QFile, QThread, Signal

from ..core import trash as core_trash
from ..core.tasks import TaskContext


def desktop_trash(path: str) -> bool:
    """Qt's native trash support first, then gio / trash-cli / kioclient."""
    try:
        result = QFile.moveToTrash(path)
        ok = result[0] if isinstance(result, tuple) else bool(result)
        if ok:
            return True
    except Exception:
        pass
    return core_trash.move_to_trash(path)


class Worker(QThread):
    """Runs fn(ctx) off the UI thread and relays log/progress as Qt signals."""

    log = Signal(str)
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None, with_trash: bool = False):
        super().__init__(parent)
        self._fn = fn
        self.ctx = TaskContext(self.log.emit, self.progress.emit, trash=desktop_trash if with_trash else None)

    def run(self):
        try:
            self.succeeded.emit(self._fn(self.ctx))
        except Exception:
            self.failed.emit(traceback.format_exc())

    def cancel(self):
        self.ctx.cancel()


_background: set = set()


def run_background(fn, on_result, parent=None) -> Worker:
    """Fire-and-forget helper for short read-only jobs (statistics, thumbnails)."""
    w = Worker(lambda ctx: fn(), parent)
    w.succeeded.connect(on_result)
    w.failed.connect(lambda tb: print(tb))
    w.finished.connect(lambda: (_background.discard(w), w.deleteLater()))
    _background.add(w)
    w.start()
    return w
