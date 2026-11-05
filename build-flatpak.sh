#!/usr/bin/env bash
# Builds the Dataset Manager flatpak (GNOME / libadwaita; Wayland or X11) and installs it for the current user.
#
#   ./build-flatpak.sh                          build + install (user)
#   ./build-flatpak.sh --bundle                 also write a single-file dataset-manager.flatpak bundle
#   ./build-flatpak.sh --extras "torch open_clip_torch"
#                                               extra pip packages baked in (default: the light ONNX
#                                               similarity backends "imageprism onnxruntime transformers"; "" for none)
#   ./build-flatpak.sh --clean                  wipe the build cache first
#
# Run it with:  flatpak run io.github.intosica.DatasetManager   (or from the app menu)
set -euo pipefail

APP_ID=io.github.intosica.DatasetManager
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# several GB of build cache: kept on the home drive, override with FLATPAK_BUILD_DIR
BUILD="${FLATPAK_BUILD_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/dataset-manager-flatpak}"
EXTRAS="imageprism onnxruntime transformers"
BUNDLE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle) BUNDLE=1 ;;
        --extras) EXTRAS="${2-}"; shift ;;
        --clean) rm -rf "$BUILD" ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

for tool in flatpak flatpak-builder; do
    command -v "$tool" >/dev/null || { echo "$tool is not installed (sudo apt install $tool)." >&2; exit 1; }
done

# runtime + SDK (no-op when already installed)
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --noninteractive --or-update flathub org.gnome.Platform//50 org.gnome.Sdk//50

tr ' ' '\n' <<<"$EXTRAS" | sed '/^$/d' > "$DIR/flatpak/extra-requirements.txt"

# A running instance keeps the app id on the D-Bus session: until it is gone the icon and `flatpak run`
# only activate it, so the freshly built app would never be the one you see.
flatpak kill "$APP_ID" 2>/dev/null || true

mkdir -p "$BUILD"
flatpak-builder --user --install --force-clean \
    --state-dir="$BUILD/state" --repo="$BUILD/repo" \
    "$BUILD/app" "$DIR/flatpak/$APP_ID.yml"

if [[ $BUNDLE == 1 ]]; then
    flatpak build-bundle "$BUILD/repo" "$BUILD/dataset-manager.flatpak" "$APP_ID"
    echo "Bundle: $BUILD/dataset-manager.flatpak  (install with: flatpak install --user dataset-manager.flatpak)"
fi
echo "Installed. Start with: flatpak run $APP_ID"
