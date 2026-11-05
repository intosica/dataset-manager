#!/usr/bin/env bash
# Launches Dataset Manager (GTK 4 / libadwaita) with the system Python.
# Needs PyGObject + libadwaita (Debian/Ubuntu/Pop!_OS: sudo apt install python3-gi gir1.2-adw-1 gir1.2-gtk-4.0),
# plus the packages in requirements.txt. A venv next to this script is used if it can see the system PyGObject
# (python3 -m venv --system-site-packages venv); otherwise the system Python is used. Or use the Flatpak.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$APP_DIR"

PYTHON=python3
for candidate in "$APP_DIR/venv/bin/python" "$APP_DIR/.venv/bin/python"; do
    if [[ -x "$candidate" ]] && "$candidate" -c "import gi" 2>/dev/null; then PYTHON="$candidate"; break; fi
done

if ! "$PYTHON" -c "import gi; gi.require_version('Adw', '1')" 2>/dev/null; then
    msg="PyGObject / libadwaita not found for $PYTHON.

Install them (Debian/Ubuntu/Pop!_OS):
  sudo apt install python3-gi gir1.2-adw-1 gir1.2-gtk-4.0
then python packages:
  python3 -m venv --system-site-packages venv
  venv/bin/pip install -r requirements.txt

Or build the Flatpak:  ./build-flatpak.sh"
    echo "$msg" >&2
    if [[ ! -t 2 ]]; then
        if command -v zenity >/dev/null; then zenity --error --title="Dataset Manager" --text="$msg"
        elif command -v notify-send >/dev/null; then notify-send "Dataset Manager" "PyGObject / libadwaita not found."
        fi
    fi
    exit 1
fi
exec "$PYTHON" -m dataset_manager "$@"
