from __future__ import annotations

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .. import APP_NAME, __version__
from ..core import pkgs
from ..core.sessions import SessionStore
from .collect_page import CollectPage
from .duplicates_page import DuplicatesPage
from .library_page import LibraryPage
from .maintenance_page import MaintenancePage
from .preferences import PreferencesDialog
from .sessions_panel import SessionsPanel
from .tags_page import TagsPage
from .widgets import TaskPanel, alert, icon_button, open_path

PAGES = [("collect", "Collect", "folder-download-symbolic", CollectPage),
         ("library", "Library", "folder-pictures-symbolic", LibraryPage),
         ("duplicates", "Duplicates", "edit-copy-symbolic", DuplicatesPage),
         ("maintenance", "Maintenance", "applications-engineering-symbolic", MaintenancePage),
         ("tags", "Tag Tools", "bookmark-new-symbolic", TagsPage)]


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, state):
        super().__init__(application=app, title=APP_NAME, default_width=1280, default_height=840)
        self.state = state

        # ---- pages ----
        self.stack = Adw.ViewStack()
        for name, title, icon, cls in PAGES:
            self.stack.add_titled_with_icon(cls(state), name, title, icon)
        self.collect: CollectPage = self.stack.get_child_by_name("collect")
        self.tags_page: TagsPage = self.stack.get_child_by_name("tags")

        # ---- sessions (left) ----
        self.store = SessionStore()
        self.sessions = SessionsPanel(self.store, self.collect.params, self._load_session, self.collect.defaults)
        self.collect.changed.append(self.sessions.edited)
        self.sessions.ensure_session()

        side_header = Adw.HeaderBar(show_title=True)
        side_header.set_title_widget(Gtk.Label(label="Sessions", css_classes=["heading"]))
        start, end = self.sessions.header_buttons()
        for b in start:
            side_header.pack_start(b)
        for b in end:
            side_header.pack_end(b)
        side = Adw.ToolbarView(content=self.sessions)
        side.add_top_bar(side_header)

        # ---- content ----
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True, tooltip_text="Sessions")
        header = Adw.HeaderBar(title_widget=switcher)
        header.pack_start(self.sidebar_btn)
        self.lib_btn = icon_button("folder-symbolic", "Show the library folder in Files")
        self.lib_btn.connect("clicked", lambda _b: open_path(state.library))
        menu = Gio.Menu()
        sec1 = Gio.Menu()
        sec1.append("Choose Library Folder…", "win.choose-library")
        sec1.append("Show Library in Files", "win.show-library")
        sec2 = Gio.Menu()
        sec2.append("New Session", "win.new-session")
        sec2.append("Preferences", "win.preferences")
        sec2.append(f"About {APP_NAME}", "win.about")
        sec2.append("Quit", "win.quit")
        menu.append_section(None, sec1)
        menu.append_section(None, sec2)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Main menu"))
        header.pack_end(self.lib_btn)
        bar = Adw.ViewSwitcherBar(stack=self.stack)
        main = Adw.ToolbarView(content=self.stack)
        main.add_top_bar(header)
        main.add_bottom_bar(bar)

        self.split = Adw.OverlaySplitView(sidebar=side, content=main, min_sidebar_width=210, max_sidebar_width=340,
                                          sidebar_width_fraction=0.2)
        self.sidebar_btn.bind_property("active", self.split, "show-sidebar", GObject.BindingFlags.BIDIRECTIONAL |
                                       GObject.BindingFlags.SYNC_CREATE)
        self.set_content(self.split)

        # narrow window: sidebar becomes an overlay, the switcher moves to the bottom
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        bp.add_setter(self.split, "collapsed", True)
        bp.add_setter(bar, "reveal", True)
        self.add_breakpoint(bp)

        # ---- actions ----
        for name, fn in (("choose-library", self.choose_library), ("show-library", lambda: open_path(state.library)),
                         ("new-session", self._new_session), ("preferences", self.show_preferences),
                         ("about", self.show_about), ("quit", self.close)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, f=fn: f())
            self.add_action(act)
        for i, (name, *_rest) in enumerate(PAGES, 1):
            act = Gio.SimpleAction.new(f"page-{i}", None)
            act.connect("activate", lambda _a, _p, n=name: self.stack.set_visible_child_name(n))
            self.add_action(act)

        state.library_changed.connect(self._update_chrome)
        self._update_chrome()
        self.connect("close-request", self._close_request)

    def _load_session(self, params: dict) -> None:
        self.collect.set_params(params)
        self.stack.set_visible_child_name("collect")

    def _new_session(self) -> None:
        self.sessions.new_session()
        self.stack.set_visible_child_name("collect")

    def _update_chrome(self) -> None:
        self.lib_btn.set_tooltip_text(f"Library: {self.state.library} (click to open)")

    def choose_library(self) -> None:
        dlg = Gtk.FileDialog(title="Choose Library Folder")
        if self.state.library:
            dlg.set_initial_folder(Gio.File.new_for_path(self.state.library))

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.state.set_library(f.get_path())
        dlg.select_folder(self, None, done)

    def show_preferences(self) -> None:
        PreferencesDialog(self.state).present(self)

    def show_about(self) -> None:
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon=self.get_application().get_application_id(),
                                version=__version__, developer_name="intosica",
                                comments="Collect, organize, caption and de-duplicate image datasets.",
                                website="https://github.com/intosica/dataset-manager")
        about.present(self)

    def _close_request(self, _w) -> bool:
        task = TaskPanel.running()
        if task is not None:
            def answered(rid: str) -> None:
                if rid == "quit":
                    TaskPanel.cancel_active()
                    TaskPanel.wait_active()
                    self._finish_quit()
            alert(self, "Task in progress", f"“{task.name}” is still running. Cancel it and quit?",
                  [("keep", "Keep Running"), ("quit", "Cancel and Quit")], destructive="quit", default="keep",
                  callback=answered)
            return True
        editor = self.tags_page.editor
        unsaved = editor.unsaved()
        if unsaved:
            def answered(rid: str) -> None:
                if rid == "cancel":
                    return
                if rid == "save":
                    editor.save_all()
                self._finish_quit()
            alert(self, f"Save {unsaved} unsaved caption{'s' if unsaved != 1 else ''}?",
                  "The Tag Editor has caption changes that are not on disk yet.",
                  [("cancel", "Cancel"), ("discard", "Discard and Quit"), ("save", "Save and Quit")],
                  destructive="discard", default="save", callback=answered)
            return True
        pkgs.kill_all()
        self.sessions.flush()
        return False

    def _finish_quit(self) -> None:
        pkgs.kill_all()
        self.sessions.flush()
        self.destroy()
