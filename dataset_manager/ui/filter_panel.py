"""Filter chips for the selected search engine. Unlike the sites, several options per filter can be ticked;
the scraper rotates through every combination."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QLayout, QPushButton, QToolButton, QWidget

from ..core.sources.filters import FILTERS, MAX_COMBOS
from .widgets import hint

CHIP_STYLE = """
QToolButton#chip { border: 1px solid palette(mid); border-radius: 12px; padding: 3px 11px; background: palette(base); }
QToolButton#chip:hover { border-color: palette(highlight); }
QToolButton#chip:checked { background: palette(highlight); color: palette(highlighted-text); border-color: palette(highlight); }
QToolButton#swatch { border: 2px solid palette(mid); border-radius: 11px; min-width: 18px; min-height: 18px;
                     max-width: 18px; max-height: 18px; }
QToolButton#swatch:hover { border-color: palette(text); }
QToolButton#swatch:checked { border: 3px solid palette(highlight); }
"""


class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping onto new lines (Qt has no built-in one)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items, self._sp = [], spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint_ = it.sizeHint()
            if x + hint_.width() > rect.right() and line:
                x, y, line = rect.x(), y + line + self._sp, 0
            if apply:
                it.setGeometry(QRect(QPoint(x, y), hint_))
            x += hint_.width() + self._sp
            line = max(line, hint_.height())
        return y + line - rect.y()


class FilterPanel(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(CHIP_STYLE)
        self.engine = ""
        self._selection: dict = {}           # engine -> {group: [values]}, kept across engine switches
        self._buttons: dict = {}             # (group, value) -> button
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnStretch(1, 1)
        self.summary = hint("")
        self.clear_btn = QPushButton("Clear filters")
        self.clear_btn.setFlat(True)
        self.clear_btn.clicked.connect(self.clear)

    # ---- state ----
    def selection(self) -> dict:
        """{group: [values]} for the current engine."""
        return {g: v for g, v in self._selection.get(self.engine, {}).items() if v}

    def all_selections(self) -> dict:
        return {e: {g: v for g, v in sel.items() if v} for e, sel in self._selection.items()}

    def set_all_selections(self, data: dict) -> None:
        self._selection = {e: {g: list(v) for g, v in sel.items()} for e, sel in (data or {}).items()}
        self.set_engine(self.engine, force=True)

    def clear(self) -> None:
        self._selection[self.engine] = {}
        self.set_engine(self.engine, force=True)
        self.changed.emit()

    # ---- building ----
    def set_engine(self, engine: str, force: bool = False) -> None:
        if engine == self.engine and not force:
            return
        self.engine = engine
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w and w not in (self.summary, self.clear_btn):
                w.hide()
                w.deleteLater()
        self._buttons.clear()
        groups = FILTERS.get(engine, [])
        sel = self._selection.setdefault(engine, {})
        for row, (key, label, options) in enumerate(groups):
            name = QLabel(label + ":")
            name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            box = QWidget()
            flow = FlowLayout(box)
            for opt_label, value, swatch in options:
                b = QToolButton()
                b.setCheckable(True)
                b.setChecked(value in sel.get(key, []))
                b.setToolTip(opt_label)
                if swatch:
                    b.setObjectName("swatch")
                    b.setStyleSheet(f"QToolButton#swatch {{ background: {swatch}; }}")
                else:
                    b.setObjectName("chip")
                    b.setText(opt_label.replace("&", "&&"))
                b.toggled.connect(lambda on, k=key, v=value: self._toggle(k, v, on))
                flow.addWidget(b)
                self._buttons[(key, value)] = b
            self.grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(box, row, 1)
        self.summary.setVisible(bool(groups))
        if not groups:
            self.clear_btn.hide()
            self.grid.addWidget(hint("This source has no search filters."), 0, 0, 1, 2)
            return
        else:
            self.grid.addWidget(self.summary, len(groups), 1)
            self.grid.addWidget(self.clear_btn, len(groups), 0)
        self._update_summary()

    def _toggle(self, key: str, value: str, on: bool) -> None:
        vals = self._selection.setdefault(self.engine, {}).setdefault(key, [])
        if on and value not in vals:
            vals.append(value)
        elif not on and value in vals:
            vals.remove(value)
        self._update_summary()
        self.changed.emit()

    def _update_summary(self) -> None:
        n = 1
        for vals in self.selection().values():
            n *= len(vals)
        has = bool(self.selection())
        self.clear_btn.setVisible(has)
        if not has:
            text = "Nothing selected: plain search. Tick several options per filter to rotate through them."
        elif n == 1:
            text = "1 filter combination."
        else:
            text = (f"{n} combinations, scraped in random order"
                    + (f" (a random {MAX_COMBOS} of them per run)." if n > MAX_COMBOS else "."))
        self.summary.setText(text)
