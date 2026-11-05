"""Filter chips for the selected search engine. Unlike the sites, several options per filter can be ticked;
the scraper rotates through every combination."""
from __future__ import annotations

from gi.repository import Adw, Gtk

from ..core.sources.filters import FILTERS, MAX_COMBOS
from .widgets import hint


class FilterPanel(Adw.PreferencesGroup):
    def __init__(self):
        super().__init__(title="Search filters")
        self.engine = ""
        self.changed = []
        self._selection: dict = {}        # engine -> {group: [values]}, kept across engine switches
        self._rows: list = []
        self._loading = False
        self.summary = hint("")
        self.clear_btn = Gtk.Button(label="Clear filters", valign=Gtk.Align.CENTER)
        self.clear_btn.add_css_class("flat")
        self.clear_btn.connect("clicked", lambda _b: self.clear())
        self.set_header_suffix(self.clear_btn)

    # ---- state ----
    def selection(self) -> dict:
        return {g: v for g, v in self._selection.get(self.engine, {}).items() if v}

    def all_selections(self) -> dict:
        return {e: {g: v for g, v in sel.items() if v} for e, sel in self._selection.items()}

    def set_all_selections(self, data: dict) -> None:
        self._selection = {e: {g: list(v) for g, v in sel.items()} for e, sel in (data or {}).items()}
        self.set_engine(self.engine, force=True)

    def clear(self) -> None:
        self._selection[self.engine] = {}
        self.set_engine(self.engine, force=True)
        self._emit()

    def _emit(self) -> None:
        for fn in self.changed:
            fn()

    # ---- building ----
    def set_engine(self, engine: str, force: bool = False) -> None:
        if engine == self.engine and not force:
            return
        self.engine = engine
        for r in self._rows:
            self.remove(r)
        self._rows.clear()
        groups = FILTERS.get(engine, [])
        sel = self._selection.setdefault(engine, {})
        self._loading = True
        for key, label, options in groups:
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=40,
                               column_spacing=6, row_spacing=6, homogeneous=False, margin_top=4, margin_bottom=4)
            for opt_label, value, swatch in options:
                b = Gtk.ToggleButton(tooltip_text=opt_label, active=value in sel.get(key, []))
                if swatch:
                    b.add_css_class("swatch")
                    b.add_css_class("sw" + swatch.lstrip("#"))      # colour defined in widgets.CSS
                else:
                    b.set_label(opt_label)
                    b.add_css_class("chip")
                b.connect("toggled", lambda btn, k=key, v=value: self._toggle(k, v, btn.get_active()))
                flow.append(b)
            title = Gtk.Label(label=label, xalign=0, margin_top=6)
            title.add_css_class("heading")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12, margin_top=4, margin_bottom=6)
            box.append(title)
            box.append(flow)
            row = Adw.PreferencesRow(child=box, activatable=False)
            self.add(row)
            self._rows.append(row)
        self._loading = False
        if not groups:
            row = Adw.ActionRow(title="This source has no search filters.")
            row.add_css_class("dim-label")
            self.add(row)
            self._rows.append(row)
        self.clear_btn.set_visible(bool(groups))
        self._update_summary()

    def _toggle(self, key: str, value: str, on: bool) -> None:
        if self._loading:
            return
        vals = self._selection.setdefault(self.engine, {}).setdefault(key, [])
        if on and value not in vals:
            vals.append(value)
        elif not on and value in vals:
            vals.remove(value)
        self._update_summary()
        self._emit()

    def _update_summary(self) -> None:
        n = 1
        for vals in self.selection().values():
            n *= len(vals)
        if not FILTERS.get(self.engine):
            self.set_description("")
        elif not self.selection():
            self.set_description("Nothing selected: plain search. Tick several options per filter to rotate through them.")
        elif n == 1:
            self.set_description("1 filter combination.")
        else:
            self.set_description(f"{n} combinations, scraped in random order"
                                 + (f" (a random {MAX_COMBOS} of them per run)." if n > MAX_COMBOS else "."))
