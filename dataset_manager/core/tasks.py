"""Progress / cancellation plumbing shared by every long-running operation."""
from __future__ import annotations

import threading
from typing import Callable, Optional


class TaskContext:
    def __init__(self, log: Optional[Callable[[str], None]] = None,
                 progress: Optional[Callable[[int, int, str], None]] = None,
                 trash: Optional[Callable[[str], bool]] = None):
        self._log = log or (lambda msg: None)
        self._progress = progress or (lambda done, total, text: None)
        self.trash = trash                      # moves a path to the desktop Trash; True on success
        self.cancel_event = threading.Event()

    def log(self, msg: str) -> None:
        self._log(str(msg).encode("utf-8", "replace").decode("utf-8"))

    def progress(self, done: int, total: int = 0, text: str = "") -> None:
        self._progress(int(done), int(total), text)

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self) -> None:
        self.cancel_event.set()
