"""Saved scraping sessions: the left sidebar. Sessions hold every Collect parameter; they can be renamed,
dragged around, sorted and grouped into folders. Stored in sessions.json in the config folder."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QMenu, QMessageBox, QStyle, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import paths
from .widgets import themed_icon

KIND, PARAMS = Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1
FILE = "sessions.json"


class SessionsPanel(QWidget):
    sessionActivated = Signal(dict)

    def __init__(self, params_provider, parent=None):
        super().__init__(parent)
        self.params_provider = params_provider          # () -> dict of the Collect page's current parameters
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIndentation(14)
        self.tree.setAnimated(True)
        self.tree.setStyleSheet("QTreeWidget { border: none; background: transparent; }"
                                "QTreeWidget::item { padding: 5px 2px; border-radius: 6px; }")
        self._icon_session = themed_icon("edit-find", "system-search", fallback=QStyle.StandardPixmap.SP_FileIcon)
        self._icon_folder = themed_icon("folder", fallback=QStyle.StandardPixmap.SP_DirIcon)

        def tool(icon, tip, slot):
            b = QToolButton()
            b.setIcon(icon)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            return b

        title = QLabel("Sessions")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 4, 0)
        bar.addWidget(title, 1)
        bar.addWidget(tool(themed_icon("document-new", "list-add", fallback=QStyle.StandardPixmap.SP_FileIcon),
                           "Save the current Collect settings as a new session", lambda: self.new_session()))
        bar.addWidget(tool(themed_icon("folder-new", fallback=QStyle.StandardPixmap.SP_FileDialogNewFolder),
                           "New folder", self.new_folder))
        bar.addWidget(tool(themed_icon("view-sort-ascending", "view-sort", fallback=QStyle.StandardPixmap.SP_ArrowUp),
                           "Sort A–Z", self.sort))
        bar.addWidget(tool(themed_icon("edit-delete", "list-remove", fallback=QStyle.StandardPixmap.SP_TrashIcon),
                           "Delete selected", self.delete_selected))
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addLayout(bar)
        col.addWidget(self.tree, 1)

        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.save)
        self._loading = False
        self.load()
        m = self.tree.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.rowsMoved, m.dataChanged):
            sig.connect(self._changed)
        self.tree.itemExpanded.connect(self._changed)
        self.tree.itemCollapsed.connect(self._changed)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.customContextMenuRequested.connect(self._menu)

    # ---- items ----
    def _make(self, kind: str, name: str, params: dict | None = None) -> QTreeWidgetItem:
        it = QTreeWidgetItem([name])
        it.setData(0, KIND, kind)
        flags = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                 | Qt.ItemFlag.ItemIsDragEnabled)
        if kind == "folder":
            it.setFlags(flags | Qt.ItemFlag.ItemIsDropEnabled)
            it.setIcon(0, self._icon_folder)
        else:
            it.setFlags(flags)
            it.setIcon(0, self._icon_session)
            it.setData(0, PARAMS, json.dumps(params or {}))
        return it

    def _insert_point(self) -> tuple:
        """(parent, row) next to the current item; inside it when it is a folder."""
        cur = self.tree.currentItem()
        if cur is None:
            return self.tree.invisibleRootItem(), self.tree.topLevelItemCount()
        if cur.data(0, KIND) == "folder":
            return cur, cur.childCount()
        parent = cur.parent() or self.tree.invisibleRootItem()
        return parent, parent.indexOfChild(cur) + 1

    def current_session(self) -> QTreeWidgetItem | None:
        cur = self.tree.currentItem()
        return cur if cur is not None and cur.data(0, KIND) == "session" else None

    def new_session(self, name: str = "", params: dict | None = None) -> QTreeWidgetItem:
        params = params if params is not None else self.params_provider()
        name = name or params.get("query", "").strip()[:60] or "New session"
        parent, row = self._insert_point()
        it = self._make("session", name, params)
        parent.insertChild(row, it)
        parent.setExpanded(True)
        self.tree.blockSignals(True)            # the page already shows these parameters
        self.tree.setCurrentItem(it)
        self.tree.blockSignals(False)
        return it

    def new_folder(self) -> None:
        parent, row = self._insert_point()
        it = self._make("folder", "New folder")
        parent.insertChild(row, it)
        parent.setExpanded(True)
        self.tree.setCurrentItem(it)
        self.tree.editItem(it)

    def update_current(self, params: dict) -> None:
        it = self.current_session()
        if it is not None and not self._loading:
            it.setData(0, PARAMS, json.dumps(params))

    def delete_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        n_children = sum(it.childCount() for it in items)
        text = f"Delete {len(items)} item(s)" + (f" and the {n_children} session(s)/folder(s) inside?" if n_children else "?")
        if QMessageBox.question(self, "Delete sessions", text) != QMessageBox.StandardButton.Yes:
            return
        for it in items:
            if it.treeWidget() is None:        # already gone with a deleted parent folder
                continue
            (it.parent() or self.tree.invisibleRootItem()).removeChild(it)

    def sort(self) -> None:
        """Folders first, then sessions, A–Z, at every level."""
        def sort_children(parent):
            kids = [parent.takeChild(0) for _ in range(parent.childCount())]
            kids.sort(key=lambda i: (i.data(0, KIND) != "folder", i.text(0).casefold()))
            parent.addChildren(kids)
            for k in kids:
                sort_children(k)
        expanded = {id(i) for i in self._walk() if i.isExpanded()}
        sort_children(self.tree.invisibleRootItem())
        for i in self._walk():
            i.setExpanded(id(i) in expanded)

    def _duplicate(self, it) -> None:
        self.tree.setCurrentItem(it)
        copy = self.new_session(it.text(0) + " (copy)", json.loads(it.data(0, PARAMS) or "{}"))
        self.tree.setCurrentItem(copy)

    def _walk(self, parent=None):
        parent = parent or self.tree.invisibleRootItem()
        for i in range(parent.childCount()):
            yield parent.child(i)
            yield from self._walk(parent.child(i))

    # ---- signals ----
    def _current_changed(self, cur, _prev) -> None:
        if cur is not None and cur.data(0, KIND) == "session":
            self._loading = True
            try:
                self.sessionActivated.emit(json.loads(cur.data(0, PARAMS) or "{}"))
            finally:
                self._loading = False

    def _changed(self, *_a) -> None:
        if not self._loading:
            self._save_timer.start()

    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        m = QMenu(self)
        m.addAction("New Session from Current Settings", lambda: self.new_session())
        m.addAction("New Folder", self.new_folder)
        if it is not None:
            m.addSeparator()
            m.addAction("Rename", lambda: self.tree.editItem(it))
            if it.data(0, KIND) == "session":
                m.addAction("Duplicate", lambda: self._duplicate(it))
            m.addAction("Delete", self.delete_selected)
        m.addSeparator()
        m.addAction("Sort A–Z", self.sort)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- persistence ----
    @staticmethod
    def file() -> str:
        return str(paths.config_dir() / FILE)

    def _dump(self, parent) -> list:
        out = []
        for i in range(parent.childCount()):
            it = parent.child(i)
            if it.data(0, KIND) == "folder":
                out.append({"type": "folder", "name": it.text(0), "expanded": it.isExpanded(),
                            "children": self._dump(it)})
            else:
                out.append({"type": "session", "name": it.text(0), "params": json.loads(it.data(0, PARAMS) or "{}")})
        return out

    def _build(self, parent, nodes: list) -> None:
        for n in nodes:
            it = self._make(n.get("type", "session"), n.get("name", "Untitled"), n.get("params"))
            parent.addChild(it)
            if n.get("type") == "folder":
                self._build(it, n.get("children", []))
                it.setExpanded(n.get("expanded", True))

    def load(self) -> None:
        try:
            with open(self.file(), encoding="utf-8") as f:
                nodes = json.load(f).get("sessions", [])
        except (OSError, ValueError, AttributeError):
            nodes = []
        self._loading = True
        self._build(self.tree.invisibleRootItem(), nodes)
        self._loading = False

    def save(self) -> None:
        tmp = self.file() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "sessions": self._dump(self.tree.invisibleRootItem())}, f, indent=1)
            os.replace(tmp, self.file())
        except OSError:
            pass
