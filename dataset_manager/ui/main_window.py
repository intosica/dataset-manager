from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QSizePolicy, QSplitter,
                               QStackedWidget, QStyle, QTabBar, QToolBar, QWidget)

from .. import APP_NAME, __version__
from ..core.upscaler import get_upscaler
from .collect_page import CollectPage
from .duplicates_page import DuplicatesPage
from .library_page import LibraryPage
from .maintenance_page import MaintenancePage
from .preferences import PreferencesDialog
from .sessions_panel import SessionsPanel
from .state import AppState
from .tags_page import TagsPage
from .widgets import TaskPanel, themed_icon


class MainWindow(QMainWindow):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)

        # ---- pages + sidebar ----
        pages = [
            ("Collect", ("folder-download", "emblem-downloads", "download"), QStyle.StandardPixmap.SP_ArrowDown, CollectPage),
            ("Library", ("folder-pictures", "image-x-generic", "view-media-library"), QStyle.StandardPixmap.SP_DirIcon, LibraryPage),
            ("Duplicates", ("edit-copy", "edit-find-replace"), QStyle.StandardPixmap.SP_FileDialogDetailedView, DuplicatesPage),
            ("Maintenance", ("tools-wizard", "applications-utilities", "preferences-other"), QStyle.StandardPixmap.SP_FileDialogListView, MaintenancePage),
            ("Tag Tools", ("tag", "bookmark-new", "format-text-bold"), QStyle.StandardPixmap.SP_FileDialogContentsView, TagsPage),
        ]
        # pages are tabs along the top; the left side holds the saved scraping sessions
        self.tabs = QTabBar()
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setIconSize(QSize(20, 20))
        self.tabs.setStyleSheet("QTabBar::tab { padding: 7px 14px; }")
        self.stack = QStackedWidget()
        for i, (title, names, fallback, cls) in enumerate(pages, 1):
            self.tabs.addTab(themed_icon(*names, fallback=fallback), title)
            self.tabs.setTabToolTip(i - 1, f"{title}  (Ctrl+{i})")
            self.stack.addWidget(cls(state))
            act = QAction(title, self)
            act.setShortcut(QKeySequence(f"Ctrl+{i}"))
            act.triggered.connect(lambda _=False, n=i - 1: self.tabs.setCurrentIndex(n))
            self.addAction(act)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)
        self.collect: CollectPage = self.stack.widget(0)

        self.sessions = SessionsPanel(self.collect.params)
        self.sessions.setMinimumWidth(180)
        self.sessions.sessionActivated.connect(self._open_session)
        self.collect.paramsChanged.connect(lambda: self.sessions.update_current(self.collect.params()))
        self.collect.started.connect(self._ensure_session)
        new_session = QAction("New Session", self)
        new_session.setShortcut(QKeySequence.StandardKey.New)
        new_session.triggered.connect(lambda: self.sessions.new_session())

        split = QSplitter()
        split.addWidget(self.sessions)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 900])
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        # ---- actions ----
        choose = QAction(themed_icon("folder-open", "document-open", fallback=QStyle.StandardPixmap.SP_DirOpenIcon),
                         "Choose Library Folder…", self)
        choose.setShortcut(QKeySequence.StandardKey.Open)
        choose.triggered.connect(self.choose_library)
        reveal = QAction(themed_icon("system-file-manager", "document-open-folder", fallback=QStyle.StandardPixmap.SP_DirLinkIcon),
                         "Show Library in File Manager", self)
        reveal.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(state.library)))
        prefs = QAction(themed_icon("configure", "preferences-system", fallback=QStyle.StandardPixmap.SP_FileDialogInfoView),
                        "Preferences…", self)
        prefs.setShortcut(QKeySequence.StandardKey.Preferences)
        prefs.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs.triggered.connect(self.show_preferences)
        quit_act = QAction(themed_icon("application-exit", fallback=QStyle.StandardPixmap.SP_DialogCloseButton), "Quit", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        about = QAction(f"About {APP_NAME}", self)
        about.triggered.connect(self.show_about)

        menu = self.menuBar()
        m_file = menu.addMenu("&File")
        m_file.addAction(new_session)
        m_file.addSeparator()
        m_file.addActions([choose, reveal])
        m_file.addSeparator()
        m_file.addAction(quit_act)
        menu.addMenu("&Settings").addAction(prefs)
        menu.addMenu("&Help").addAction(about)

        # ---- top bar: page tabs + library ----
        bar = QToolBar("Navigation")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        bar.addWidget(self.tabs)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        bar.addWidget(QLabel("Library: "))
        self.lib_edit = QLineEdit()
        self.lib_edit.setReadOnly(True)
        self.lib_edit.setMinimumWidth(220)
        self.lib_edit.setMaximumWidth(420)
        bar.addWidget(self.lib_edit)
        bar.addAction(choose)
        bar.addAction(reveal)
        self.addToolBar(bar)

        # ---- status bar ----
        self.model_label = QLabel()
        self.statusBar().addPermanentWidget(self.model_label)
        state.libraryChanged.connect(self._update_chrome)
        state.settingsChanged.connect(self._update_chrome)
        self._update_chrome()

    def _update_chrome(self) -> None:
        self.lib_edit.setText(self.state.library)
        up = get_upscaler()
        self.model_label.setText("Upscale model: " + (up.model_path.rsplit("/", 1)[-1] if up.model_path else "none") + "   ")

    def _open_session(self, params: dict) -> None:
        self.collect.set_params(params)
        self.tabs.setCurrentIndex(0)

    def _ensure_session(self) -> None:
        """Every collection run is remembered as a session."""
        if self.sessions.current_session() is None:
            self.sessions.new_session()

    def choose_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Library Folder", self.state.library)
        if path:
            self.state.set_library(path)

    def show_preferences(self) -> None:
        PreferencesDialog(self.state, self).exec()

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          "<p>Collect, organize, caption and de-duplicate image datasets.</p>")

    def closeEvent(self, event) -> None:
        if TaskPanel.active is not None:
            answer = QMessageBox.question(self, "Task in progress",
                                          "A task is still running. Cancel it and quit?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            TaskPanel.cancel_active()
            TaskPanel.wait_active()
        self.sessions.save()
        event.accept()
