"""Left sidebar: saved scraping sessions in folders. Always one session is active; edits on the Collect page
are saved into it, clicking another session loads that one."""
from __future__ import annotations

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from ..core.sessions import Node, SessionStore
from .widgets import alert, icon_button


class SessionsPanel(Gtk.Box):
    def __init__(self, store: SessionStore, get_params, load_params, new_params):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.store = store
        self.get_params, self.load_params, self.new_params = get_params, load_params, new_params
        self._save_id = 0
        self.rows: dict[str, Gtk.ListBoxRow] = {}

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, activate_on_single_click=True)
        self.list.add_css_class("navigation-sidebar")
        self.list.add_css_class("sessions")
        self.list.connect("row-activated", self._row_activated)
        # dropping on the empty area below the rows moves the item to the end of the top level
        end = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        end.connect("drop", lambda _t, value, _x, _y: self._drop(value, None, False))
        self.empty = Adw.StatusPage(icon_name="folder-saved-search-symbolic", title="No sessions",
                                    description="Create one with the + button.", vexpand=True)
        self.empty.add_controller(end)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(Gtk.ScrolledWindow(child=self.list, vexpand=True), "list")
        self.stack.add_named(self.empty, "empty")
        listdrop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        listdrop.connect("drop", lambda _t, value, _x, _y: self._drop(value, None, False))
        self.list.add_controller(listdrop)
        self.append(self.stack)
        self.rebuild()

    # ---- header buttons (the window puts these in the sidebar's header bar) ----
    def header_buttons(self) -> tuple[list, list]:
        add = icon_button("list-add-symbolic", "New session (Ctrl+N)", self.new_session)
        folder = icon_button("folder-new-symbolic", "New folder", self.new_folder)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="More", valign=Gtk.Align.CENTER)
        menu.add_css_class("flat")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for label, fn in (("Sort A–Z", self.sort),):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, f=fn: (pop.popdown(), f()))
            box.append(b)
        pop.set_child(box)
        menu.set_popover(pop)
        return [add, folder], [menu]

    # ---- building ----
    def rebuild(self) -> None:
        while (r := self.list.get_row_at_index(0)) is not None:
            self.list.remove(r)
        self.rows.clear()
        self._add_level(self.store.roots, 0)
        self.stack.set_visible_child_name("list" if self.store.roots else "empty")

    def _add_level(self, nodes: list, depth: int) -> None:
        for n in nodes:
            row = self._make_row(n, depth)
            self.rows[n.id] = row
            self.list.append(row)
            if n.kind == "folder" and n.expanded:
                self._add_level(n.children, depth + 1)

    def _make_row(self, n: Node, depth: int) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=True)
        row.node_id = n.id
        box = Gtk.Box(spacing=8, margin_start=6 + 16 * depth, margin_end=4, margin_top=6, margin_bottom=6)
        if n.kind == "folder":
            box.append(Gtk.Image(icon_name="go-down-symbolic" if n.expanded else "go-next-symbolic"))
            box.append(Gtk.Image(icon_name="folder-symbolic"))
            row.add_css_class("folder-row")
        else:
            box.append(Gtk.Image(icon_name="system-search-symbolic"))
        label = Gtk.Label(label=n.name, xalign=0, hexpand=True, ellipsize=3)
        box.append(label)
        if n.kind == "session" and n.id == self.store.active_id:
            row.add_css_class("accent")
            label.add_css_class("heading")
            box.append(Gtk.Image(icon_name="object-select-symbolic"))
        more = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, popover=self._menu(n))
        more.add_css_class("flat")
        more.add_css_class("circular")
        box.append(more)
        row.set_child(box)
        # drag source + drop target
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda _s, _x, _y, nid=n.id: Gdk.ContentProvider.new_for_value(nid))
        row.add_controller(src)
        tgt = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        tgt.connect("drop", lambda _t, value, _x, y, node=n, r=row: self._drop(value, node, y > r.get_height() / 2))
        row.add_controller(tgt)
        return row

    def _menu(self, n: Node) -> Gtk.Popover:
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        def item(label, fn, destructive=False):
            b = Gtk.Button(label=label, halign=Gtk.Align.FILL)
            b.add_css_class("flat")
            if destructive:
                b.add_css_class("destructive-action")
            b.connect("clicked", lambda _b: (pop.popdown(), GLib.idle_add(lambda: (fn(), False)[1])))
            box.append(b)
        item("Rename…", lambda: self.rename(n))
        if n.kind == "session":
            item("Duplicate", lambda: self.duplicate(n))
        else:
            item("New session in folder", lambda: self.new_session(near=n))
        item("Delete…", lambda: self.delete(n), True)
        pop.set_child(box)
        return pop

    # ---- actions ----
    def _changed(self, rebuild: bool = True) -> None:
        if rebuild:
            self.rebuild()
        self.save_soon()

    def save_soon(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self._save_now)

    def _save_now(self) -> bool:
        self._save_id = 0
        self.store.save()
        return False

    def flush(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
        self.store.save()

    def ensure_session(self) -> None:
        """There is always an active session, so edits are never lost."""
        if self.store.active is None:
            self.store.add_session("New session", self.get_params(), auto_name=True)
            self._changed()
        else:
            self.load_params(self.store.active.params)

    def edited(self) -> None:
        """The Collect page changed: write it into the active session."""
        if self.store.active is None:
            self.store.add_session("New session", self.get_params(), auto_name=True)
            self.store.update_active(self.get_params())
            self._changed()
            return
        before = self.store.active.name
        self.store.update_active(self.get_params())
        if self.store.active.name != before:
            self._relabel()
        self.save_soon()

    def _relabel(self) -> None:
        n = self.store.active
        row = self.rows.get(n.id) if n else None
        if row:
            box = row.get_child()
            child = box.get_first_child()
            while child:
                if isinstance(child, Gtk.Label):
                    child.set_label(n.name)
                    break
                child = child.get_next_sibling()

    def _selected_near(self) -> Node | None:
        return self.store.active

    def new_session(self, near: Node | None = None) -> None:
        self.flush()
        params = self.new_params()
        node = self.store.add_session("New session", params, near=near or self._selected_near(), auto_name=True)
        self.load_params(node.params)
        self._changed()

    def new_folder(self) -> None:
        folder = self.store.add_folder("New folder", near=None)
        self._changed()
        self.rename(folder)

    def duplicate(self, n: Node) -> None:
        copy = self.store.duplicate(n)
        copy.auto_name = False
        self.load_params(copy.params)
        self._changed()

    def delete(self, n: Node) -> None:
        inner = sum(1 for _ in self.store.walk(n.children))
        body = f"This also deletes the {inner} item(s) inside." if inner else "This cannot be undone."

        def answered(rid: str) -> None:
            if rid != "delete":
                return
            was_active = n.id in {x.id for x in self.store.walk([n])} and self.store.active is not None \
                and self.store.active.id in {x.id for x in self.store.walk([n])}
            self.store.delete(n)
            if was_active and self.store.active is not None:
                self.load_params(self.store.active.params)
            elif self.store.active is None:
                self.store.add_session("New session", self.new_params(), auto_name=True)
                self.load_params(self.store.active.params)
            self._changed()
        alert(self.get_root(), f"Delete “{n.name}”?", body, [("cancel", "Cancel"), ("delete", "Delete")],
              destructive="delete", callback=answered)

    def rename(self, n: Node) -> None:
        entry = Gtk.Entry(text=n.name, activates_default=True)
        dlg = Adw.AlertDialog.new("Rename", None)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Rename")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")

        def done(d, res):
            if d.choose_finish(res) == "ok" and entry.get_text().strip():
                n.name = entry.get_text().strip()
                n.auto_name = False
                self._changed()
        dlg.choose(self.get_root(), None, done)
        entry.grab_focus()
        entry.select_region(0, -1)

    def sort(self) -> None:
        self.store.sort()
        self._changed()

    def _row_activated(self, _lb, row) -> None:
        n = self.store.find(row.node_id)
        if n is None:
            return
        if n.kind == "folder":
            n.expanded = not n.expanded
            self._changed()
            return
        if n.id == self.store.active_id:
            return
        self.flush()
        self.store.active_id = n.id
        self.load_params(n.params)
        self._changed()

    def _drop(self, value, target: Node | None, lower_half: bool) -> bool:
        """Folder rows take the item inside; session rows place it before (upper half) or after (lower half)."""
        node = self.store.find(str(value))
        if node is None:
            return False
        if target is None or target.kind == "folder":
            ok = self.store.move(node, target, target is not None)
        elif lower_half:
            sibs = self.store.siblings(target)
            nxt = sibs[sibs.index(target) + 1] if sibs.index(target) + 1 < len(sibs) else None
            if nxt is node:
                return False
            if nxt is not None:
                ok = self.store.move(node, nxt, False)
            else:                                   # last in its list: park at the end of that list
                self.store.siblings(node).remove(node)
                sibs.append(node)
                ok = True
        else:
            ok = self.store.move(node, target, False)
        if ok:
            self._changed()
        return bool(ok)
