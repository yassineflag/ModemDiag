#!/usr/bin/env bash
# build.sh — Build ModemDiag for Linux
# Usage:  chmod +x build.sh && ./build.sh
# Output: local-ignored/dist/ModemDiag
set -euo pipefail
cd "$(dirname "$0")"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$PWD/local-ignored/cache/pip}"
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$PWD/local-ignored/cache/pyinstaller}"
export PYTHONPYCACHEPREFIX="$PWD/local-ignored/cache/pycache"
case "${1:-}" in
    "") export MODEMDIAG_RELEASE=0 ;;
    --release) export MODEMDIAG_RELEASE=1 ;;
    -h|--help) echo "Usage: ./build.sh [--release]"; exit 0 ;;
    *) echo "Usage: ./build.sh [--release]"; exit 2 ;;
esac

echo "=== ModemDiag Linux Build ==="
python3 --version || { echo "ERROR: python3 not found"; exit 1; }

# A directory can remain after an interrupted venv setup. Validate the actual
# interpreter and pip, and repair in place without deleting the environment.
BUILD_PYTHON="$PWD/.venv/bin/python"
if [ ! -x "$BUILD_PYTHON" ] || ! "$BUILD_PYTHON" -m pip --version >/dev/null 2>&1; then
    echo ">>> Creating or repairing virtual environment..."
    if ! python3 -m venv .venv; then
        echo "ERROR: Could not create a complete Python virtual environment."
        echo "On Debian/Ubuntu, install python3-venv (or the package matching your Python version),"
        echo "then run ./build.sh again. Existing environment files were not deleted."
        exit 1
    fi
fi

if ! "$BUILD_PYTHON" -m pip --version >/dev/null 2>&1; then
    echo "ERROR: .venv/bin/python cannot run pip. Repair the Python venv installation and retry."
    exit 1
fi

# Explicit interpreter paths work without activation and ensure dependencies and
# PyInstaller use the same environment, even when another venv is active.
RELEASE_REQUIREMENTS="requirements-release.txt"
if [ ! -f "$RELEASE_REQUIREMENTS" ]; then
    RELEASE_REQUIREMENTS="release/linux-$(uname -m)/requirements-release.txt"
fi
if [ -f "$RELEASE_REQUIREMENTS" ]; then
    "$BUILD_PYTHON" -m pip install --disable-pip-version-check -r "$RELEASE_REQUIREMENTS"
else
    "$BUILD_PYTHON" -m pip install --disable-pip-version-check -r requirements.txt
fi

echo ">>> Cleaning old build..."
rm -rf local-ignored/build/ModemDiag

echo ">>> Running PyInstaller..."
"$BUILD_PYTHON" -m PyInstaller tools/build/ModemDiag.spec --distpath local-ignored/dist --workpath local-ignored/build --noconfirm

if [ "$MODEMDIAG_RELEASE" = 1 ]; then
    echo ">>> Packaging separate binary and source ZIPs..."
    "$BUILD_PYTHON" tools/release_bundle.py package
else
    "$BUILD_PYTHON" tools/release_bundle.py clean
fi

echo ""
echo "✓ Build complete!"
echo "  Executable : $(pwd)/local-ignored/dist/ModemDiag"
if [ "$MODEMDIAG_RELEASE" = 1 ]; then
    echo "  Release    : binary ZIP plus separate source ZIP in local-ignored/dist/"
    echo "  Multi-platform release: combine both builds' metadata as described in README.md."
fi
echo "  User data  : ~/.local/share/ModemDiag/"
