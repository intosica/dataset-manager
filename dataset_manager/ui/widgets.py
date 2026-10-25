from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QStyle, QVBoxLayout, QWidget)

from ..core.captions import PLACEHOLDERS, CaptionSpec, render_caption
from ..core import capabilities, paths
from ..core.upscaler import MODEL_EXTS, discover_models, get_upscaler
from .workers import Worker, run_background


def themed_icon(*names: str, fallback=QStyle.StandardPixmap.SP_FileIcon) -> QIcon:
    """Icon from the active desktop theme (Breeze, Adwaita, …) with a style fallback."""
    for n in names:
        icon = QIcon.fromTheme(n)
        if not icon.isNull():
            return icon
    return QApplication.style().standardIcon(fallback)


def heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.25)
    label.setFont(font)
    return label


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)          # renders in the palette's muted "disabled text" colour
    return label


class PathEdit(QWidget):
    """Line edit + Browse… button using the platform's native file dialog."""

    textChanged = Signal(str)

    def __init__(self, mode: str = "dir", file_filter: str = "All files (*)", placeholder: str = "", parent=None):
        super().__init__(parent)
        self.mode, self.file_filter = mode, file_filter
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.button = QPushButton(themed_icon("document-open", "folder-open", fallback=QStyle.StandardPixmap.SP_DirOpenIcon),
                                  "Browse…")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)
        self.button.clicked.connect(self._browse)
        self.edit.textChanged.connect(self.textChanged)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def _browse(self) -> None:
        start = self.text() or ""
        if self.mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Choose Folder", start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose File", start, self.file_filter)
        if path:
            self.edit.setText(path)


class TaskPanel(QWidget):
    """Progress bar + log for one long-running task. Only one task may run at a time app-wide."""

    busyChanged = Signal(bool)
    active: "TaskPanel | None" = None

    def __init__(self, parent=None, min_log_height: int = 140):
        super().__init__(parent)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.status = QLabel("")
        self.status.setEnabled(False)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log.setMinimumHeight(min_log_height)
        top = QHBoxLayout()
        top.addWidget(self.bar, 1)
        top.addWidget(self.status)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addLayout(top)
        col.addWidget(self.log, 1)
        self._worker: Worker | None = None
        self._on_success = None

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def run(self, fn, on_success=None, with_trash: bool = False) -> bool:
        if TaskPanel.active is not None:
            QMessageBox.information(self.window(), "Task in progress",
                                    "Another task is still running. Wait for it to finish or cancel it first.")
            return False
        self.log.clear()
        self.status.setText("")
        worker = Worker(fn, self, with_trash)
        worker.log.connect(self.log.appendPlainText)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker, self._on_success = worker, on_success
        TaskPanel.active = self
        self.bar.setRange(0, 0)       # indeterminate until the task reports progress
        self.busyChanged.emit(True)
        worker.start()
        return True

    def cancel(self) -> None:
        if self._worker:
            self.log.appendPlainText("Cancelling…")
            self._worker.cancel()

    @classmethod
    def cancel_active(cls) -> None:
        if cls.active:
            cls.active.cancel()

    @classmethod
    def wait_active(cls, ms: int = 8000) -> None:
        if cls.active and cls.active._worker:
            cls.active._worker.wait(ms)

    def _on_progress(self, done: int, total: int, text: str) -> None:
        if total <= 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        self.status.setText(text)

    def _on_succeeded(self, result) -> None:
        if self._on_success:
            self._on_success(result)

    def _on_failed(self, tb: str) -> None:
        self.log.appendPlainText("An unexpected error occurred:\n" + tb)

    def _on_finished(self) -> None:
        worker, self._worker = self._worker, None
        TaskPanel.active = None
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.status.setText("")
        if worker:
            worker.deleteLater()
        self.busyChanged.emit(False)


class CaptionEditor(QWidget):
    """Template + quality tags + extra tags with a live preview."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.template = QLineEdit()
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("e.g. anime screencap")
        self.good = QLineEdit()
        self.low = QLineEdit()
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Template:", self.template)
        form.addRow("", hint(f"Placeholders: {PLACEHOLDERS}"))
        form.addRow("Extra tags:", self.extra)
        form.addRow("Normal quality tags:", self.good)
        form.addRow("Low quality tags:", self.low)
        form.addRow("Preview:", self.preview)
        for w in (self.template, self.extra, self.good, self.low):
            w.textChanged.connect(self._update)

    def load(self, spec: CaptionSpec) -> None:
        for w, v in ((self.template, spec.template), (self.extra, spec.extra_tags),
                     (self.good, spec.good_tags), (self.low, spec.low_tags)):
            w.setText(v)
        self._update()

    def spec(self) -> CaptionSpec:
        return CaptionSpec(self.template.text(), self.good.text(), self.low.text(), self.extra.text())

    def _update(self) -> None:
        s = self.spec()
        self.preview.setText(f"{render_caption(s, 'my_collection', 'Landscape') or '(empty)'}\n"
                             f"{render_caption(s, 'my_collection', 'Portrait', True) or '(empty)'}")
        self.changed.emit()


class ModelPicker(QWidget):
    """Pick the AI upscale model: discovered models, or any file via the native file dialog."""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(24)
        self.browse = QPushButton(themed_icon("document-open", "folder-open",
                                              fallback=QStyle.StandardPixmap.SP_DirOpenIcon), "Browse…")
        self.test = QPushButton("Test")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.combo, 1)
        row.addWidget(self.browse)
        row.addWidget(self.test)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addLayout(row)
        col.addWidget(self.status)
        self.combo.activated.connect(self._chosen)
        self.browse.clicked.connect(self._browse)
        self.test.clicked.connect(self._test)
        state.settingsChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        s = self.state.settings
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("Automatic (first model found)", "")
        self.combo.addItem("None (standard resampling only)", "none")
        models = discover_models(s.all_model_dirs())
        cur = s.upscale_model
        if cur and cur != "none":
            cur_path = paths.resolve(cur)
            if cur_path not in models:
                models.append(cur_path)
        for p in models:
            self.combo.addItem(f"{os.path.basename(p)}   ({os.path.dirname(p)})", p)
            self.combo.setItemData(self.combo.count() - 1, p, Qt.ItemDataRole.ToolTipRole)
        idx = self.combo.findData(cur) if cur in ("", "none") else self.combo.findData(paths.resolve(cur))
        self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo.blockSignals(False)
        self._set_status()

    def _set_status(self, text: str | None = None) -> None:
        up = get_upscaler()
        if text is None:
            if not (capabilities.has("torch") and capabilities.has("spandrel")):
                text = "AI upscaling needs: pip install torch spandrel (standard resampling is used until then)"
            elif up.model_path:
                text = up.model_path
            else:
                text = "No model selected — standard resampling is used."
        self.status.setText(text)

    def _chosen(self) -> None:
        self.state.settings.upscale_model = self.combo.currentData() or ""
        self.state.save()

    def _browse(self) -> None:
        cur = self.state.settings.upscale_model
        start = os.path.dirname(cur) if cur not in ("", "none") else ""
        exts = " ".join(f"*{e}" for e in MODEL_EXTS)
        path, _ = QFileDialog.getOpenFileName(self, "Choose Upscale Model", start,
                                              f"Upscale models ({exts});;All files (*)")
        if path:
            self.state.settings.upscale_model = path
            self.state.save()

    def _test(self) -> None:
        self.status.setText("Loading model…")
        self.test.setEnabled(False)

        def done(result) -> None:
            self.test.setEnabled(True)
            self._set_status(("OK: " if result[0] else "Problem: ") + result[1])

        run_background(get_upscaler().check, done, self)

