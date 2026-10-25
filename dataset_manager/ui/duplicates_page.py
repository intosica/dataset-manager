from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QSlider, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.duplicates import HASH_BITS, apply_removals, scan_duplicates
from ..core.imaging import make_thumbnails
from ..core.utils import fmt_bytes
from .state import AppState
from .widgets import TaskPanel, heading, hint, themed_icon

_ROLE = Qt.ItemDataRole.UserRole


def _threshold_text(v: int) -> str:
    if v == 0:
        return "Exact copies only"
    return f"{v} of {HASH_BITS} bits" + (" (recommended)" if v == 6 else "")


class DuplicatesPage(QWidget):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 20)
        self.slider.setValue(state.settings.duplicate_threshold)
        self.slider_label = QLabel(_threshold_text(self.slider.value()))
        self.slider_label.setMinimumWidth(170)
        self.scope = QComboBox()
        self.scope.addItem("Whole library", "library")
        self.scope.addItem("Within each collection", "collection")
        self.scan_btn = QPushButton(themed_icon("edit-find", "system-search", fallback=QStyle.StandardPixmap.SP_FileDialogContentsView),
                                    "Scan for Duplicates")
        self.cancel_btn = QPushButton(themed_icon("process-stop", fallback=QStyle.StandardPixmap.SP_MediaStop), "Cancel")
        self.cancel_btn.setEnabled(False)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Similarity:"))
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.slider_label)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Compare:"))
        controls.addWidget(self.scope)
        controls.addWidget(self.cancel_btn)
        controls.addWidget(self.scan_btn)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Image", "Resolution", "Size", "Location"])
        self.tree.setIconSize(QSize(64, 64))
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)

        self.selection_label = QLabel("Nothing to review.")
        self.trash_btn = QPushButton(themed_icon("user-trash", "edit-delete", fallback=QStyle.StandardPixmap.SP_TrashIcon),
                                     "Move Selected to Trash")
        self.delete_btn = QPushButton(themed_icon("edit-delete", fallback=QStyle.StandardPixmap.SP_DialogDiscardButton),
                                      "Delete Permanently…")
        self.trash_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        actions = QHBoxLayout()
        actions.addWidget(self.selection_label, 1)
        actions.addWidget(self.delete_btn)
        actions.addWidget(self.trash_btn)

        self.panel = TaskPanel(min_log_height=70)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        layout.addWidget(heading("Duplicates"))
        layout.addWidget(hint("Finds exact copies and near-duplicates (re-encoded, resized or upscaled versions). "
                              "In each group the best copy is kept; checked images will be removed."))
        layout.addLayout(controls)
        layout.addWidget(self.tree, 1)
        layout.addLayout(actions)
        layout.addWidget(self.panel)

        self.slider.valueChanged.connect(lambda v: self.slider_label.setText(_threshold_text(v)))
        self.scan_btn.clicked.connect(self.scan)
        self.cancel_btn.clicked.connect(self.panel.cancel)
        self.trash_btn.clicked.connect(lambda: self.remove("trash"))
        self.delete_btn.clicked.connect(lambda: self.remove("delete"))
        self.tree.itemChanged.connect(lambda *_: self._update_selection())
        self.panel.busyChanged.connect(self._busy)

    def _busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.trash_btn.setEnabled(not busy and self._checked_count() > 0)
        self.delete_btn.setEnabled(not busy and self._checked_count() > 0)

    # ---- scanning ----
    def scan(self) -> None:
        base, thr, scope = self.state.library, self.slider.value(), self.scope.currentData()
        self.state.settings.duplicate_threshold = thr
        self.state.save()
        self.tree.clear()
        self._update_selection()

        def job(ctx):
            groups = scan_duplicates(base, thr, scope, ctx)
            shown = groups[:300]
            files = [r["path"] for g in shown for r in [g.keeper] + g.duplicates]
            if files:
                ctx.log("Preparing previews…")
            thumbs = dict(zip(files, make_thumbnails(files, 128))) if files else {}
            return groups, shown, thumbs

        if os.path.isdir(base):
            self.panel.run(job, self._show_groups)
        else:
            self.panel.log.setPlainText(f"Library folder not found: {base}")

    def _show_groups(self, result) -> None:
        groups, shown, thumbs = result
        base = self.state.library
        bold = QFont()
        bold.setBold(True)

        def add_row(parent, rec, keep: bool):
            it = QTreeWidgetItem(parent, [os.path.basename(rec["path"]) + ("   — keep" if keep else ""),
                                          f"{rec['w']} × {rec['h']}", fmt_bytes(rec["size"]),
                                          os.path.relpath(os.path.dirname(rec["path"]), base)])
            if thumbs.get(rec["path"]):
                it.setIcon(0, QIcon(QPixmap(thumbs[rec["path"]])))
            it.setData(0, _ROLE, rec["path"])
            it.setToolTip(0, rec["path"])
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if keep:
                it.setFont(0, bold)
            else:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                it.setCheckState(0, Qt.CheckState.Checked)
            it.setFlags(flags)
            return it

        self.tree.blockSignals(True)
        for n, g in enumerate(shown, 1):
            top = QTreeWidgetItem(self.tree, [f"Group {n} — {len(g.duplicates) + 1} images", "", "",
                                              f"{fmt_bytes(g.reclaimable)} reclaimable"])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            top.setData(0, _ROLE, g.keeper["path"])
            add_row(top, g.keeper, True)
            for d in g.duplicates:
                add_row(top, d, False)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        if len(groups) > len(shown):
            self.panel.log.appendPlainText(f"Showing the {len(shown)} largest of {len(groups)} groups. "
                                           "Remove these and scan again to continue.")
        self._update_selection()

    # ---- selection ----
    def _checked(self) -> list[tuple[str, list[str]]]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            dups = [top.child(j).data(0, _ROLE) for j in range(top.childCount())
                    if top.child(j).flags() & Qt.ItemFlag.ItemIsUserCheckable
                    and top.child(j).checkState(0) == Qt.CheckState.Checked]
            if dups:
                out.append((top.data(0, _ROLE), dups))
        return out

    def _checked_count(self) -> int:
        return sum(len(d) for _, d in self._checked())

    def _update_selection(self) -> None:
        sel = self._checked()
        n = sum(len(d) for _, d in sel)
        size = 0
        for _, dups in sel:
            for p in dups:
                try:
                    size += os.path.getsize(p)
                except OSError:
                    pass
        self.selection_label.setText(f"{n} images selected · {fmt_bytes(size)}" if n else
                                     ("No images selected." if self.tree.topLevelItemCount() else "Nothing to review."))
        enabled = n > 0 and not self.panel.busy
        self.trash_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)

    # ---- removal ----
    def remove(self, mode: str) -> None:
        sel = self._checked()
        n = sum(len(d) for _, d in sel)
        if not n:
            return
        if mode == "delete":
            answer = QMessageBox.warning(
                self, "Delete Permanently",
                f"Permanently delete {n} images and their caption files?\n\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return

        def done(res) -> None:
            self.panel.log.appendPlainText(
                f"{'Deleted' if mode == 'delete' else 'Moved to Trash'}: {res.removed} images "
                f"({fmt_bytes(res.freed)})" + (f", {res.failed} failed" if res.failed else ""))
            self.tree.clear()
            self._update_selection()
            self.state.contentChanged.emit()

        self.panel.run(lambda ctx: apply_removals(sel, mode, ctx), done, with_trash=(mode == "trash"))
