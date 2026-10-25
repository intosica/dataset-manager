from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QStyle, QVBoxLayout, QWidget)

from ..core.collector import CollectOptions, run_collection
from ..core.sink import SinkOptions
from ..core.sources import SOURCES
from .state import AppState
from .widgets import ModelPicker, TaskPanel, heading, hint, themed_icon


class CollectPage(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        s = state.settings

        # ---- source ----
        self.source = QComboBox()
        for key, label, artist in SOURCES:
            self.source.addItem(label, (key, artist))
        self.query_label = QLabel()
        self.query = QLineEdit()
        self.query.setClearButtonEnabled(True)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Defaults to the search terms or artist name")
        box_source = QGroupBox("Source")
        form = QFormLayout(box_source)
        form.addRow("Source:", self.source)
        form.addRow(self.query_label, self.query)
        form.addRow("Collection name:", self.name)

        # ---- options ----
        self.limit = QSpinBox()
        self.limit.setRange(0, 5000)
        self.limit.setValue(100)
        self.limit.setSpecialValueText("Unlimited")
        self.min_side = QSpinBox()
        self.min_side.setRange(0, 4096)
        self.min_side.setSingleStep(32)
        self.min_side.setSuffix(" px")
        self.min_side.setValue(s.min_image_side)
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 8)
        self.parallel.setValue(s.parallel_downloads)
        self.randomize = QCheckBox("Randomize order")
        self.skip_dups = QCheckBox("Skip duplicates and previously downloaded images")
        self.skip_dups.setChecked(True)
        self.resize = QCheckBox("Resize while downloading")
        self.target = QSpinBox()
        self.target.setRange(64, 8192)
        self.target.setSingleStep(64)
        self.target.setSuffix(" px")
        self.target.setValue(s.target_dim)
        self.target.setEnabled(False)
        self.resize.toggled.connect(self.target.setEnabled)
        self.captions = QCheckBox("Write caption files (.txt)")
        self.captions.setChecked(True)
        self.extra = QLineEdit(s.extra_tags)
        self.extra.setPlaceholderText("Extra tags added to every caption, e.g. anime screencap")
        self.captions.toggled.connect(self.extra.setEnabled)

        box_opts = QGroupBox("Options")
        f2 = QFormLayout(box_opts)
        f2.addRow("Maximum images:", self.limit)
        f2.addRow("Minimum image size:", self.min_side)
        f2.addRow("Parallel downloads:", self.parallel)
        f2.addRow("", self.randomize)
        f2.addRow("", self.skip_dups)
        row = QHBoxLayout()
        row.addWidget(self.resize)
        row.addWidget(QLabel("Longer side:"))
        row.addWidget(self.target)
        row.addStretch(1)
        f2.addRow("", row)
        self.model_picker = ModelPicker(state)
        self.model_picker.setEnabled(False)
        self.resize.toggled.connect(self.model_picker.setEnabled)
        f2.addRow("Upscale model:", self.model_picker)
        f2.addRow("", self.captions)
        f2.addRow("Extra tags:", self.extra)
        f2.addRow("", hint("Resizing direction, snapping and the caption template are set in Preferences."))

        # ---- actions ----
        self.start_btn = QPushButton(themed_icon("media-playback-start", fallback=QStyle.StandardPixmap.SP_MediaPlay),
                                     "Start Collection")
        self.start_btn.setDefault(True)
        self.cancel_btn = QPushButton(themed_icon("process-stop", "dialog-cancel",
                                                  fallback=QStyle.StandardPixmap.SP_MediaStop), "Cancel")
        self.cancel_btn.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.start_btn)

        self.panel = TaskPanel()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(heading("Collect Images"))
        layout.addWidget(box_source)
        layout.addWidget(box_opts)
        layout.addLayout(buttons)
        layout.addWidget(self.panel, 1)

        self.source.currentIndexChanged.connect(self._source_changed)
        self.query.returnPressed.connect(self.start)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self.panel.cancel)
        self.panel.busyChanged.connect(self._busy)
        state.settingsChanged.connect(self._reload)
        self._source_changed()

    def _reload(self) -> None:
        if not self.panel.busy:
            self.extra.setText(self.state.settings.extra_tags)

    def _source_changed(self) -> None:
        _key, artist = self.source.currentData()
        if artist:
            self.query_label.setText("Artist name:")
            self.query.setPlaceholderText("e.g. Rebecca Guay")
        else:
            self.query_label.setText("Search terms or URL:")
            self.query.setPlaceholderText("Search terms, or a Pinterest board / Internet Archive item URL")

    def _busy(self, busy: bool) -> None:
        self.start_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)

    def start(self) -> None:
        if self.panel.busy:
            return
        s = self.state.settings
        s.min_image_side, s.parallel_downloads = self.min_side.value(), self.parallel.value()
        s.target_dim, s.extra_tags = self.target.value(), self.extra.text().strip()
        self.state.save()
        key, _artist = self.source.currentData()
        opts = CollectOptions(
            source=key, query=self.query.text(), collection_name=self.name.text(), limit=self.limit.value(),
            randomize=self.randomize.isChecked(), parallel=self.parallel.value(),
            sink=SinkOptions(min_side=self.min_side.value(), resize=self.resize.isChecked(), target_dim=s.target_dim,
                             direction=s.scale_direction, snap_to_64=s.snap_to_64,
                             skip_duplicates=self.skip_dups.isChecked(), duplicate_threshold=s.duplicate_threshold,
                             write_captions=self.captions.isChecked(), caption=s.caption_spec()))
        self.panel.run(lambda ctx: run_collection(opts, s, ctx), lambda _r: self.state.contentChanged.emit())
