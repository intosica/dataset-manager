from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from . import APP_ID, APP_NAME, __version__

ICON = Path(__file__).parent / "resources" / "dataset-manager.svg"
ICON_DIR = Path(__file__).parent / "resources" / "icons"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME}: image dataset manager")
    parser.add_argument("--library", help="open this library folder (remembered for next time)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args, gtk_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, GLib, Gtk
    except (ImportError, ValueError) as exc:
        print(f"GTK 4 / libadwaita (PyGObject) is not available: {exc}\n"
              "Install python3-gi and gir1.2-adw-1 (Debian/Ubuntu/Pop!_OS) or use the Flatpak (./build-flatpak.sh).",
              file=sys.stderr)
        return 1

    from .core import models, pkgs
    from .ui.main_window import MainWindow
    from .ui.state import AppState
    from .ui.widgets import install_css

    app_id = os.environ.get("FLATPAK_ID") or "io.github.intosica.DatasetManager"
    GLib.set_application_name(APP_NAME)
    GLib.set_prgname(app_id)
    app = Adw.Application(application_id=app_id)
    state = AppState()
    note = pkgs.activate(state.settings)      # optional packages from a venv or the user's site-packages
    models.apply_cache_env(state.settings)    # before transformers / torch import
    if note:
        print(note, file=sys.stderr)
    if args.library:
        state.set_library(args.library)

    failed = False

    def on_activate(application) -> None:
        nonlocal failed
        win = application.props.active_window
        if win is None:
            try:
                Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(ICON_DIR))
                Gtk.Window.set_default_icon_name(app_id)
                install_css()
                win = MainWindow(application, state)
            except Exception:
                # Never stay alive without a window: this process owns the app id, so every later launch
                # (icon, `flatpak run`) would just activate it and silently do nothing.
                traceback.print_exc()
                failed = True
                application.quit()
                return
            application.set_accels_for_action("win.preferences", ["<Control>comma"])
            application.set_accels_for_action("win.new-session", ["<Control>n"])
            application.set_accels_for_action("win.choose-library", ["<Control>o"])
            application.set_accels_for_action("win.quit", ["<Control>q"])
            for i in range(1, 5):
                application.set_accels_for_action(f"win.page-{i}", [f"<Control>{i}"])
        win.present()

    app.connect("activate", on_activate)
    code = app.run([sys.argv[0], *gtk_args])
    return 1 if failed else code
