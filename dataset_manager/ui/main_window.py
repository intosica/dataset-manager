from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QStackedWidget, QStyle, QToolBar, QWidget)

from .. import APP_NAME, __version__
from ..core.upscaler import get_upscaler
from .collect_page import CollectPage
from .duplicates_page import DuplicatesPage
from .library_page import LibraryPage
from .maintenance_page import MaintenancePage
from .preferences import PreferencesDialog
from .state import AppState
from .tags_page import TagsPage
from .widgets import TaskPanel, themed_icon


class MainWindow(QMainWindow):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 760)

        # ---- pages + sidebar ----
        pages = [
            ("Collect", ("folder-download", "emblem-downloads", "download"), QStyle.StandardPixmap.SP_ArrowDown, CollectPage),
            ("Library", ("folder-pictures", "image-x-generic", "view-media-library"), QStyle.StandardPixmap.SP_DirIcon, LibraryPage),
            ("Duplicates", ("edit-copy", "edit-find-replace"), QStyle.StandardPixmap.SP_FileDialogDetailedView, DuplicatesPage),
            ("Maintenance", ("tools-wizard", "applications-utilities", "preferences-other"), QStyle.StandardPixmap.SP_FileDialogListView, MaintenancePage),
            ("Tag Tools", ("tag", "bookmark-new", "format-text-bold"), QStyle.StandardPixmap.SP_FileDialogContentsView, TagsPage),
        ]
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(190)
        self.sidebar.setIconSize(QSize(22, 22))
        self.sidebar.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar.setSpacing(2)
        self.stack = QStackedWidget()
        for i, (title, names, fallback, cls) in enumerate(pages, 1):
            item = QListWidgetItem(themed_icon(*names, fallback=fallback), title)
            item.setSizeHint(QSize(170, 38))
            self.sidebar.addItem(item)
            self.stack.addWidget(cls(state))
            act = QAction(title, self)
            act.setShortcut(QKeySequence(f"Ctrl+{i}"))
            act.triggered.connect(lambda _=False, n=i - 1: self.sidebar.setCurrentRow(n))
            self.addAction(act)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.sidebar)
        row.addWidget(line)
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)

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
        m_file.addActions([choose, reveal])
        m_file.addSeparator()
        m_file.addAction(quit_act)
        menu.addMenu("&Settings").addAction(prefs)
        menu.addMenu("&Help").addAction(about)

        # ---- library toolbar ----
        bar = QToolBar("Library")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.addWidget(QLabel("  Library:  "))
        self.lib_edit = QLineEdit()
        self.lib_edit.setReadOnly(True)
        self.lib_edit.setMinimumWidth(380)
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
        event.accept()
