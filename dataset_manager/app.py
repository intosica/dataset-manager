from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_ID, APP_NAME, __version__

ICON = Path(__file__).parent / "resources" / "dataset-manager.svg"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} — image dataset manager")
    parser.add_argument("--library", help="open this library folder (remembered for next time)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    try:
        from PySide6.QtGui import QGuiApplication, QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is not installed in this environment.\n"
              "Run:  venv/bin/pip install -r requirements.txt", file=sys.stderr)
        return 1

    from .ui.main_window import MainWindow
    from .ui.state import AppState

    import os
    flatpak_id = os.environ.get("FLATPAK_ID")
    # matches the window to the .desktop entry (WM_CLASS on X11, app_id on Wayland)
    QGuiApplication.setDesktopFileName(flatpak_id or APP_ID)
    app = QApplication([sys.argv[0], *qt_args])
    if flatpak_id:
        app.setStyle("Fusion")        # the sandbox has no desktop Qt style plugins; Fusion follows the palette
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(ICON)))

    state = AppState()
    if args.library:
        state.set_library(args.library)
    window = MainWindow(state)
    window.show()
    return app.exec()
