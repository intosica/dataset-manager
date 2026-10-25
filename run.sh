#!/usr/bin/env bash
# Launches Dataset Manager using the virtual environment that lives next to this script.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

PYTHON=""
for candidate in "$APP_DIR/venv/bin/python" "$APP_DIR/.venv/bin/python"; do
    if [[ -x "$candidate" ]]; then PYTHON="$candidate"; break; fi
done

if [[ -z "$PYTHON" ]]; then
    msg="No virtual environment found in:
$APP_DIR

Create one with:
  python3 -m venv venv
  venv/bin/pip install -r requirements.txt"
    echo "$msg" >&2
    if [[ ! -t 2 ]]; then   # started from the app menu: show a dialog
        if command -v kdialog >/dev/null; then kdialog --title "Dataset Manager" --error "$msg"
        elif command -v zenity >/dev/null; then zenity --error --title="Dataset Manager" --text="$msg"
        elif command -v notify-send >/dev/null; then notify-send "Dataset Manager" "No virtual environment found."
        fi
    fi
    exit 1
fi

cd "$APP_DIR"
exec "$PYTHON" -m dataset_manager "$@"
