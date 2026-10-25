from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QStyle, QVBoxLayout, QWidget)

from ..core.tags import process_tags
from .state import AppState
from .widgets import heading, hint, themed_icon


class TagsPage(QWidget):
    """Paste a comma-separated tag list, press Process, and the same box shows the cleaned result."""

    def __init__(self, state: AppState):
        super().__init__()
        self._previous: str | None = None
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Paste tags here, e.g.  white dog, a huge bike, 1girl")
        self.info = QLabel()
        self.info.setEnabled(False)

        self.process_btn = QPushButton(themed_icon("tag", "bookmark-new", "run-build",
                                                   fallback=QStyle.StandardPixmap.SP_DialogApplyButton), "Process Tags")
        self.process_btn.setDefault(True)
        self.undo_btn = QPushButton(themed_icon("edit-undo", fallback=QStyle.StandardPixmap.SP_ArrowBack), "Undo")
        self.undo_btn.setEnabled(False)
        self.copy_btn = QPushButton(themed_icon("edit-copy", fallback=QStyle.StandardPixmap.SP_FileIcon), "Copy")
        self.clear_btn = QPushButton(themed_icon("edit-clear", fallback=QStyle.StandardPixmap.SP_DialogResetButton), "Clear")

        row = QHBoxLayout()
        row.addWidget(self.info, 1)
        for b in (self.clear_btn, self.copy_btn, self.undo_btn, self.process_btn):
            row.addWidget(b)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(heading("Tag Tools"))
        layout.addWidget(hint("Replaces spaces inside a tag with underscores, removes all commas and tidies spacing. "
                              "Example: “white dog, a huge bike, 1girl” becomes “white_dog a_huge_bike 1girl”. "
                              "Each line is processed separately."))
        layout.addWidget(self.text, 1)
        layout.addLayout(row)

        self.process_btn.clicked.connect(self.process)
        self.undo_btn.clicked.connect(self.undo)
        self.copy_btn.clicked.connect(self.copy)
        self.clear_btn.clicked.connect(self.clear)
        QShortcut(QKeySequence("Ctrl+Return"), self.text, activated=self.process)

    def process(self) -> None:
        original = self.text.toPlainText()
        if not original.strip():
            return
        result = process_tags(original)
        if result != original:
            self._previous = original
            self.text.setPlainText(result)
            self.undo_btn.setEnabled(True)
        n = len(result.split())
        self.info.setText(f"{n} tags")

    def undo(self) -> None:
        if self._previous is not None:
            self.text.setPlainText(self._previous)
            self._previous = None
            self.undo_btn.setEnabled(False)
            self.info.setText("")

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())
        self.info.setText("Copied to clipboard")

    def clear(self) -> None:
        self._previous = self.text.toPlainText() or None
        self.text.clear()
        self.undo_btn.setEnabled(self._previous is not None)
        self.info.setText("")
