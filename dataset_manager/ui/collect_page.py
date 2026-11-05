from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSplitter, QSpinBox, QStyle, QVBoxLayout,
                               QWidget)

from ..core import capabilities
from ..core.collector import CollectOptions, run_collection
from ..core.similarity import BACKENDS, SimilaritySpec
from ..core.sink import SinkOptions
from ..core.sources import SOURCES
from ..core.sources.filters import NO_SAFE_SEARCH
from .filter_panel import FilterPanel
from .state import AppState
from .widgets import ModelPicker, PathEdit, TaskPanel, heading, hint, themed_icon


class CollectPage(QWidget):
    paramsChanged = Signal()
    started = Signal()

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

        # ---- filters ----
        self.filters = FilterPanel()
        self.no_safe = QCheckBox("Turn off safe search")
        self.box_filters = QGroupBox("Search filters")
        fl = QVBoxLayout(self.box_filters)
        fl.addWidget(self.no_safe)
        fl.addWidget(self.filters)

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
        self.sort_orient = QCheckBox("Sort into Portrait / Landscape / Square folders")
        self.sort_orient.setChecked(True)
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
        f2.addRow("", self.sort_orient)
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

        # ---- similarity ----
        self.sim_box = QGroupBox("Keep only images that look like my examples")
        self.sim_box.setCheckable(True)
        self.sim_box.setChecked(False)
        self.backend = QComboBox()
        for key, (label, _mods, _cmd) in BACKENDS.items():
            self.backend.addItem(label, key)
        self.backend_status = hint("")
        self.pos_dir = PathEdit("dir", placeholder="Folder of images to go for")
        self.neg_dir = PathEdit("dir", placeholder="Folder of images to avoid (optional)")
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(-1.0, 1.0)
        self.threshold.setDecimals(3)
        self.threshold.setSingleStep(0.01)
        f3 = QFormLayout(self.sim_box)
        f3.addRow("Model:", self.backend)
        f3.addRow("", self.backend_status)
        f3.addRow("Look like:", self.pos_dir)
        f3.addRow("Don't look like:", self.neg_dir)
        f3.addRow("Threshold:", self.threshold)
        f3.addRow("", hint("Score = similarity to the examples minus similarity to the ones to avoid. "
                           "Images scoring at or below the threshold are skipped. Start at 0 and raise it "
                           "if too much gets through; without avoid-examples try about 0.2."))

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

        # settings scroll on top, log below; the user can drag the divider
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(18, 14, 18, 8)
        col.setSpacing(10)
        col.addWidget(heading("Collect Images"))
        col.addWidget(box_source)
        col.addWidget(self.box_filters)
        col.addWidget(box_opts)
        col.addWidget(self.sim_box)
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(18, 4, 18, 14)
        bl.addLayout(buttons)
        self.panel = TaskPanel()
        bl.addWidget(self.panel, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(scroll)
        split.addWidget(bottom)
        split.setStretchFactor(0, 1)
        self._split = split
        split.setChildrenCollapsible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

        self.source.currentIndexChanged.connect(self._source_changed)
        self.backend.currentIndexChanged.connect(self._backend_changed)
        self.query.returnPressed.connect(self.start)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self.panel.cancel)
        self.panel.busyChanged.connect(self._busy)
        state.settingsChanged.connect(self._reload)
        for sig in (self.source.currentIndexChanged, self.query.textChanged, self.name.textChanged,
                    self.no_safe.toggled, self.filters.changed, self.limit.valueChanged, self.min_side.valueChanged,
                    self.parallel.valueChanged, self.randomize.toggled, self.skip_dups.toggled,
                    self.sort_orient.toggled, self.resize.toggled, self.target.valueChanged, self.captions.toggled,
                    self.extra.textChanged, self.sim_box.toggled, self.backend.currentIndexChanged,
                    self.pos_dir.textChanged, self.neg_dir.textChanged, self.threshold.valueChanged):
            sig.connect(self.paramsChanged)
        self._source_changed()
        self._backend_changed()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._split is not None:          # first show: give the settings most of the height
            split, self._split = self._split, None
            QTimer.singleShot(0, lambda: split.setSizes([int(split.height() * 0.72), int(split.height() * 0.28)]))

    # ---- session parameters ----
    def params(self) -> dict:
        return {
            "source": self.source.currentData()[0], "query": self.query.text(), "name": self.name.text(),
            "safe_search_off": self.no_safe.isChecked(), "filters": self.filters.all_selections(),
            "limit": self.limit.value(), "min_side": self.min_side.value(), "parallel": self.parallel.value(),
            "randomize": self.randomize.isChecked(), "skip_duplicates": self.skip_dups.isChecked(),
            "sort_by_orientation": self.sort_orient.isChecked(), "resize": self.resize.isChecked(),
            "target_dim": self.target.value(), "captions": self.captions.isChecked(), "extra_tags": self.extra.text(),
            "similarity": {"enabled": self.sim_box.isChecked(), "backend": self.backend.currentData(),
                           "positive_dir": self.pos_dir.text(), "negative_dir": self.neg_dir.text(),
                           "threshold": self.threshold.value()},
        }

    def set_params(self, p: dict) -> None:
        """Missing keys keep their current value, so old sessions still load."""
        self.blockSignals(True)            # one paramsChanged at the end, not twenty
        try:
            idx = next((i for i in range(self.source.count()) if self.source.itemData(i)[0] == p.get("source")), -1)
            if idx >= 0:
                self.source.setCurrentIndex(idx)
            for key, w in (("query", self.query), ("name", self.name), ("extra_tags", self.extra)):
                if key in p:
                    w.setText(p[key])
            for key, w in (("safe_search_off", self.no_safe), ("randomize", self.randomize),
                           ("skip_duplicates", self.skip_dups), ("sort_by_orientation", self.sort_orient),
                           ("resize", self.resize), ("captions", self.captions)):
                if key in p:
                    w.setChecked(bool(p[key]))
            for key, w in (("limit", self.limit), ("min_side", self.min_side), ("parallel", self.parallel),
                           ("target_dim", self.target)):
                if key in p:
                    w.setValue(int(p[key]))
            if "filters" in p:
                self.filters.set_all_selections(p["filters"])
            sim = p.get("similarity") or {}
            if sim:
                self.sim_box.setChecked(bool(sim.get("enabled")))
                i = self.backend.findData(sim.get("backend"))
                if i >= 0:
                    self.backend.setCurrentIndex(i)
                self.pos_dir.setText(sim.get("positive_dir", ""))
                self.neg_dir.setText(sim.get("negative_dir", ""))
                self.threshold.setValue(float(sim.get("threshold", 0.0)))
        finally:
            self.blockSignals(False)
        self.paramsChanged.emit()

    # ---- reactions ----
    def _reload(self) -> None:
        if not self.panel.busy:
            self.extra.setText(self.state.settings.extra_tags)

    def _source_changed(self) -> None:
        key, artist = self.source.currentData()
        self.box_filters.setVisible(not artist)
        self.no_safe.setVisible(key not in NO_SAFE_SEARCH)
        self.filters.set_engine(key)
        if artist:
            self.query_label.setText("Artist name:")
            self.query.setPlaceholderText("e.g. Rebecca Guay")
        else:
            self.query_label.setText("Search terms or URL:")
            self.query.setPlaceholderText("Search terms, or a Pinterest board / Internet Archive item URL")

    def _backend_changed(self) -> None:
        _label, mods, cmd = BACKENDS[self.backend.currentData()]
        missing = [m for m in mods if not capabilities.has(m)]
        if missing and os.environ.get("FLATPAK_ID"):
            cmd = f"./build-flatpak.sh --extras \"imageprism onnxruntime transformers {cmd.removeprefix('pip install ')}\""
        self.backend_status.setText(f"Not installed — run: {cmd}" if missing else
                                    "Installed. The model is downloaded on first use and cached.")

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
        key, artist = self.source.currentData()
        sim = (SimilaritySpec(self.backend.currentData(), self.pos_dir.text().strip(), self.neg_dir.text().strip(),
                              self.threshold.value()) if self.sim_box.isChecked() else SimilaritySpec())
        opts = CollectOptions(
            source=key, query=self.query.text(), collection_name=self.name.text(), limit=self.limit.value(),
            randomize=self.randomize.isChecked(), parallel=self.parallel.value(),
            safe_search_off=self.no_safe.isChecked() and key not in NO_SAFE_SEARCH,
            filters={} if artist else self.filters.selection(), similarity=sim,
            sink=SinkOptions(min_side=self.min_side.value(), resize=self.resize.isChecked(), target_dim=s.target_dim,
                             direction=s.scale_direction, snap_to_64=s.snap_to_64,
                             skip_duplicates=self.skip_dups.isChecked(), duplicate_threshold=s.duplicate_threshold,
                             write_captions=self.captions.isChecked(), caption=s.caption_spec(),
                             sort_by_orientation=self.sort_orient.isChecked()))
        self.started.emit()
        self.panel.run(lambda ctx: run_collection(opts, s, ctx), lambda _r: self.state.contentChanged.emit())
