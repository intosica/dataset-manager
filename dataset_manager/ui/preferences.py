from __future__ import annotations

import os

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QListWidget, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from ..core import capabilities, paths
from ..core.browser import find_browser
from ..core.upscaler import MODEL_EXTS, discover_models
from .state import AppState
from .widgets import CaptionEditor, ModelPicker, PathEdit, hint

DIRECTIONS = [("Enlarge and shrink", "both"), ("Enlarge only", "up"), ("Shrink only", "down")]


class PreferencesDialog(QDialog):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Preferences")
        self.resize(640, 520)
        s = state.settings
        tabs = QTabWidget()

        # ---- General ----
        general = QWidget()
        f = QFormLayout(general)
        self.library = PathEdit("dir", placeholder=str(paths.default_library_dir()))
        self.library.setText(s.library_dir)
        self.browser = PathEdit("file", placeholder="Automatic")
        self.browser.setText(s.browser_path)
        self.profile = PathEdit("dir", placeholder=str(paths.default_profile_dir()))
        self.profile.setText(s.browser_profile_dir)
        self.headless = QCheckBox("Run the browser without a window")
        self.headless.setChecked(s.headless)
        f.addRow("Library folder:", self.library)
        f.addRow("Browser:", self.browser)
        f.addRow("", hint("Chrome or Chromium. Leave empty to detect one automatically."))
        f.addRow("Browser profile:", self.profile)
        f.addRow("", hint("Keeps logins and cookies between collections."))
        f.addRow("", self.headless)
        tabs.addTab(general, "General")

        # ---- Upscaling ----
        up = QWidget()
        v = QVBoxLayout(up)
        form = QFormLayout()
        form.addRow("Upscale model:", ModelPicker(state))
        self.target = QSpinBox()
        self.target.setRange(64, 8192)
        self.target.setSingleStep(64)
        self.target.setSuffix(" px")
        self.target.setValue(s.target_dim)
        self.direction = QComboBox()
        for label, key in DIRECTIONS:
            self.direction.addItem(label, key)
        self.direction.setCurrentIndex(max(0, self.direction.findData(s.scale_direction)))
        self.snap = QCheckBox("Round sizes to multiples of 64")
        self.snap.setChecked(s.snap_to_64)
        form.addRow("Default longer side:", self.target)
        form.addRow("Default direction:", self.direction)
        form.addRow("", self.snap)
        v.addLayout(form)
        v.addWidget(hint("Additional folders to search for models (.pth, .pt, .safetensors):"))
        self.dirs = QListWidget()
        self.dirs.addItems([str(d) for d in s.model_dirs])
        v.addWidget(self.dirs, 1)
        btns = QHBoxLayout()
        self.add_dir, self.remove_dir = QPushButton("Add Folder…"), QPushButton("Remove")
        btns.addWidget(self.add_dir)
        btns.addWidget(self.remove_dir)
        btns.addStretch(1)
        v.addLayout(btns)
        tabs.addTab(up, "Upscaling")
        self.add_dir.clicked.connect(self._add_dir)
        self.remove_dir.clicked.connect(self._remove_dir)

        # ---- Captions ----
        self.captions = CaptionEditor()
        self.captions.load(s.caption_spec())
        cap = QWidget()
        QVBoxLayout(cap).addWidget(self.captions)
        cap.layout().addStretch(1)
        tabs.addTab(cap, "Captions")

        # ---- System ----
        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setPlainText(self._system_report())
        tabs.addTab(info, "System")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # ---- model folders (applied immediately so the model list updates) ----
    def _save_dirs(self) -> None:
        self.state.settings.model_dirs = [self.dirs.item(i).text() for i in range(self.dirs.count())]
        self.state.save()

    def _add_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add Model Folder")
        if d:
            self.dirs.addItem(d)
            self._save_dirs()

    def _remove_dir(self) -> None:
        for it in self.dirs.selectedItems():
            self.dirs.takeItem(self.dirs.row(it))
        self._save_dirs()

    # ---- system tab ----
    def _system_report(self) -> str:
        lines = ["Optional components", ""]
        missing = []
        for mod, ok, what, cmd in capabilities.report():
            lines.append(f"  [{'installed' if ok else 'missing  '}]  {mod:<20} {what}")
            if not ok:
                missing.append(cmd)
        if missing:
            lines += ["", "To install what is missing, run inside the app's virtual environment:"]
            lines += [f"  venv/bin/{c}" for c in dict.fromkeys(missing)]
        lines += ["", "Locations", "",
                  f"  Application folder  {paths.APP_DIR}", f"  Settings            {self.state.settings.file()}",
                  f"  Cache               {paths.cache_dir()}", f"  Browser             {find_browser(self.state.settings.browser_path) or 'Playwright bundled Chromium'}",
                  f"  Portable mode       {'yes' if paths.portable() else 'no'}"]
        return "\n".join(lines)

    # ---- apply ----
    def _accept(self) -> None:
        s = self.state.settings
        s.library_dir, s.browser_path = self.library.text(), self.browser.text()
        s.browser_profile_dir, s.headless = self.profile.text(), self.headless.isChecked()
        s.model_dirs = [self.dirs.item(i).text() for i in range(self.dirs.count())]
        s.target_dim, s.scale_direction, s.snap_to_64 = self.target.value(), self.direction.currentData(), self.snap.isChecked()
        spec = self.captions.spec()
        s.caption_template, s.good_quality_tags = spec.template, spec.good_tags
        s.low_quality_tags, s.extra_tags = spec.low_tags, spec.extra_tags
        self.state.save()
        self.state.libraryChanged.emit()
        self.accept()
