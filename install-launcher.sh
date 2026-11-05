#!/usr/bin/env bash
# Adds Dataset Manager to the application menu (KDE, GNOME, …). Run with --uninstall to remove.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP="$DATA/applications/io.github.intosica.DatasetManager.desktop"
ICON="$DATA/icons/hicolor/scalable/apps/io.github.intosica.DatasetManager.svg"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP" "$ICON"
    echo "Removed launcher."
else
    mkdir -p "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
    cp "$APP_DIR/dataset_manager/resources/dataset-manager.svg" "$ICON"
    cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=Dataset Manager
GenericName=Image Dataset Manager
Comment=Collect, organize, caption and de-duplicate image datasets
Exec="$APP_DIR/run.sh" %U
Path=$APP_DIR
Icon=io.github.intosica.DatasetManager
Terminal=false
Categories=Graphics;Utility;
Keywords=images;dataset;captions;duplicates;upscale;
StartupNotify=true
StartupWMClass=io.github.intosica.DatasetManager
DESK
    echo "Installed: $DESKTOP"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
