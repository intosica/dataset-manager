from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStyle, QVBoxLayout, QWidget)

from ..core import capabilities
from ..core.duplicates import HASH_BITS
from ..core.organizer import OrganizeOptions, organize_library
from .state import AppState
from .widgets import CaptionEditor, ModelPicker, TaskPanel, heading, hint, themed_icon

DIRECTIONS = [("Enlarge and shrink", "both"), ("Enlarge only", "up"), ("Shrink only", "down")]
CAPTION_MODES = [("Add where missing", "add"), ("Append missing tags to existing captions", "append"),
                 ("Overwrite all captions", "overwrite")]


def _step(title: str, checked: bool) -> QGroupBox:
    box = QGroupBox(title)
    box.setCheckable(True)
    box.setChecked(checked)
    return box


class MaintenancePage(QWidget):
    """Every step is optional; unchecked steps are skipped entirely."""

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        s = state.settings

        # 1 duplicates
        self.g_dup = _step("Remove duplicate images", False)
        self.dup_slider = QSlider(Qt.Orientation.Horizontal)
        self.dup_slider.setRange(0, 20)
        self.dup_slider.setValue(s.duplicate_threshold)
        self.dup_label = QLabel()
        self.dup_slider.valueChanged.connect(lambda v: self.dup_label.setText(
            "Exact copies only" if v == 0 else f"{v} of {HASH_BITS} bits"))
        self.dup_slider.valueChanged.emit(self.dup_slider.value())
        row = QHBoxLayout(self.g_dup)
        row.addWidget(QLabel("Similarity:"))
        row.addWidget(self.dup_slider, 1)
        row.addWidget(self.dup_label)
        row.addWidget(hint("Extra copies are moved to the Trash."))

        # 2 files and folders
        self.g_files = QGroupBox("Files and folders")
        self.sort = QCheckBox("Sort into Landscape / Portrait / Square folders")
        self.sort.setChecked(True)
        self.rename = QCheckBox("Clean file names (lowercase_with_underscores)")
        self.rename.setChecked(True)
        self.to_jpg = QCheckBox("Convert images to JPEG")
        self.to_jpg.setChecked(True)
        v = QVBoxLayout(self.g_files)
        for w in (self.sort, self.rename, self.to_jpg):
            v.addWidget(w)
        v.addWidget(hint("With sorting off, files stay where they are. Caption files always follow their image, "
                         "and JPEG files are moved without being re-encoded."))

        # 3 resize
        self.g_resize = _step("Resize images", False)
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
        f = QFormLayout(self.g_resize)
        f.addRow("Longer side:", self.target)
        f.addRow("Direction:", self.direction)
        f.addRow("", self.snap)
        f.addRow("Upscale model:", ModelPicker(state))

        # 4 low quality
        self.g_low = _step("Flag low-quality images", False)
        self.strict = QSlider(Qt.Orientation.Horizontal)
        self.strict.setRange(20, 80)
        self.strict.setValue(45)
        self.strict_label = QLabel("0.45")
        self.strict.valueChanged.connect(lambda v: self.strict_label.setText(f"{v / 100:.2f}"))
        r = QHBoxLayout()
        r.addWidget(QLabel("Strictness:"))
        r.addWidget(self.strict, 1)
        r.addWidget(self.strict_label)
        lv = QVBoxLayout(self.g_low)
        lv.addLayout(r)
        note = "Images under 400 px are always flagged; flagged images go to a low_quality folder when sorting is on."
        if not capabilities.has("transformers"):
            note += "  The aesthetic model needs: pip install transformers"
        lv.addWidget(hint(note))

        # 5 captions
        self.g_cap = _step("Caption files (.txt)", True)
        self.cap_mode = QComboBox()
        for label, key in CAPTION_MODES:
            self.cap_mode.addItem(label, key)
        self.cap_editor = CaptionEditor()
        self.cap_editor.load(s.caption_spec())
        cf = QFormLayout()
        cf.addRow("Existing captions:", self.cap_mode)
        cv = QVBoxLayout(self.g_cap)
        cv.addLayout(cf)
        cv.addWidget(self.cap_editor)

        # layout
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 8, 0)
        for g in (self.g_dup, self.g_files, self.g_resize, self.g_low, self.g_cap):
            iv.addWidget(g)
        iv.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(inner)

        self.run_btn = QPushButton(themed_icon("system-run", "media-playback-start",
                                               fallback=QStyle.StandardPixmap.SP_MediaPlay), "Run Maintenance")
        self.run_btn.setDefault(True)
        self.cancel_btn = QPushButton(themed_icon("process-stop", fallback=QStyle.StandardPixmap.SP_MediaStop), "Cancel")
        self.cancel_btn.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.run_btn)
        self.panel = TaskPanel(min_log_height=110)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(heading("Maintenance"))
        layout.addWidget(hint("Choose which steps to run on the whole library. Unchecked steps are skipped."))
        layout.addWidget(scroll, 3)
        layout.addLayout(buttons)
        layout.addWidget(self.panel, 2)

        self.run_btn.clicked.connect(self.run)
        self.cancel_btn.clicked.connect(self.panel.cancel)
        self.panel.busyChanged.connect(lambda b: (self.run_btn.setEnabled(not b), self.cancel_btn.setEnabled(b)))
        state.settingsChanged.connect(self._reload)

    def _reload(self) -> None:
        if not self.panel.busy:
            self.cap_editor.load(self.state.settings.caption_spec())

    def run(self) -> None:
        if self.panel.busy:
            return
        s = self.state.settings
        spec = self.cap_editor.spec()
        s.caption_template, s.good_quality_tags = spec.template, spec.good_tags
        s.low_quality_tags, s.extra_tags = spec.low_tags, spec.extra_tags
        s.target_dim, s.scale_direction = self.target.value(), self.direction.currentData()
        s.snap_to_64, s.duplicate_threshold = self.snap.isChecked(), self.dup_slider.value()
        self.state.save()
        opts = OrganizeOptions(
            remove_duplicates=self.g_dup.isChecked(), duplicate_threshold=self.dup_slider.value(),
            detect_low_quality=self.g_low.isChecked(), low_quality_strictness=self.strict.value() / 100,
            sort_by_orientation=self.sort.isChecked(), clean_filenames=self.rename.isChecked(),
            convert_to_jpg=self.to_jpg.isChecked(), resize=self.g_resize.isChecked(),
            target_dim=self.target.value(), direction=self.direction.currentData(), snap_to_64=self.snap.isChecked(),
            caption_mode=self.cap_mode.currentData() if self.g_cap.isChecked() else "off", caption=spec)
        base = self.state.library
        self.panel.run(lambda ctx: organize_library(base, opts, ctx), lambda _r: self.state.contentChanged.emit(),
                       with_trash=True)
