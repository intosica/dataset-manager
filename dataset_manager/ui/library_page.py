from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QListView,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QSplitter, QStyle, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.imaging import make_thumbnails
from ..core.library import library_stats, preview_files
from ..core.utils import fmt_bytes
from .state import AppState
from .widgets import heading, themed_icon
from .workers import run_background


def _open(path: str) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class LibraryPage(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self._dirty, self._token = True, 0

        self.summary = QLabel()
        self.summary.setEnabled(False)
        self.refresh_btn = QPushButton(themed_icon("view-refresh", fallback=QStyle.StandardPixmap.SP_BrowserReload), "Refresh")
        self.open_btn = QPushButton(themed_icon("document-open-folder", "folder-open",
                                                fallback=QStyle.StandardPixmap.SP_DirOpenIcon), "Open Folder")
        top = QHBoxLayout()
        top.addWidget(heading("Library"))
        top.addWidget(self.summary, 1)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.open_btn)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Collection", "Images", "Landscape", "Portrait", "Square", "Low quality", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 7):
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        self.view = QComboBox()
        self.view.addItems(["All", "Landscape", "Portrait", "Square", "Low quality"])
        self.count = QSpinBox()
        self.count.setRange(6, 300)
        self.count.setValue(60)
        self.count.setSuffix(" previews")
        self.shuffle_btn = QPushButton(themed_icon("media-playlist-shuffle", "view-refresh",
                                                   fallback=QStyle.StandardPixmap.SP_BrowserReload), "Shuffle")
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Preview:"))
        controls.addWidget(self.view)
        controls.addWidget(self.count)
        controls.addWidget(self.shuffle_btn)
        controls.addStretch(1)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setIconSize(QSize(160, 160))
        self.grid.setGridSize(QSize(180, 200))
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)

        lower = QWidget()
        lv = QVBoxLayout(lower)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addLayout(controls)
        lv.addWidget(self.grid, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(lower)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(split, 1)

        self.refresh_btn.clicked.connect(self.refresh)
        self.open_btn.clicked.connect(lambda: _open(self.state.library))
        self.tree.itemSelectionChanged.connect(self.load_previews)
        self.view.currentIndexChanged.connect(self.load_previews)
        self.count.editingFinished.connect(self.load_previews)
        self.shuffle_btn.clicked.connect(self.load_previews)
        self.grid.itemActivated.connect(lambda it: _open(it.data(Qt.ItemDataRole.UserRole)))
        state.libraryChanged.connect(self._mark_dirty)
        state.contentChanged.connect(self._mark_dirty)

    # ---- lifecycle ----
    def _mark_dirty(self) -> None:
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    # ---- statistics ----
    def refresh(self) -> None:
        self._dirty = False
        base = self.state.library
        if not os.path.isdir(base):
            self.tree.clear()
            self.grid.clear()
            self.summary.setText(f"{base} does not exist yet — collect some images to create it.")
            return
        self.summary.setText("Reading library…")
        run_background(lambda: library_stats(base), self._show_stats, self)

    def _show_stats(self, rows) -> None:
        self.tree.clear()
        for s in rows:
            it = QTreeWidgetItem([s.name, str(s.total), str(s.landscape), str(s.portrait), str(s.square),
                                  str(s.low_quality), fmt_bytes(s.size)])
            it.setData(0, Qt.ItemDataRole.UserRole, s.path)
            for col in range(1, 7):
                it.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(it)
        total, size = sum(r.total for r in rows), sum(r.size for r in rows)
        self.summary.setText(f"{total:,} images in {len(rows)} collections · {fmt_bytes(size)}")
        if rows:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    # ---- previews ----
    def load_previews(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            self.grid.clear()
            return
        path, view, n = items[0].data(0, Qt.ItemDataRole.UserRole), self.view.currentText(), self.count.value()
        self._token += 1
        token = self._token

        def job():
            files = preview_files(path, view, n, True)
            return token, files, make_thumbnails(files, 256)

        run_background(job, self._show_previews, self)

    def _show_previews(self, result) -> None:
        token, files, thumbs = result
        if token != self._token:
            return
        self.grid.clear()
        for f, t in zip(files, thumbs):
            if not t:
                continue
            it = QListWidgetItem(QIcon(QPixmap(t)), os.path.basename(f))
            it.setData(Qt.ItemDataRole.UserRole, f)
            it.setToolTip(f)
            self.grid.addItem(it)
