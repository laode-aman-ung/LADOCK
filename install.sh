#!/usr/bin/env bash
# LADOCK Desktop — Linux/macOS installer
# Usage: bash install.sh [--rdkit]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_RDKIT=0

for arg in "$@"; do
    case "$arg" in
        --rdkit) INSTALL_RDKIT=1 ;;
        -h|--help)
            echo "Usage: bash install.sh [--rdkit]"
            echo "  --rdkit   Also install RDKit (for SMILES rendering)"
            exit 0
            ;;
    esac
done

# ── Python discovery ──────────────────────────────────────────────────────────
find_python() {
    local candidates=(
        "$HOME/miniconda3/bin/python"
        "$HOME/anaconda3/bin/python"
        "$HOME/miniforge3/bin/python"
        "$HOME/mambaforge/bin/python"
        "$(command -v python3 2>/dev/null || true)"
        "$(command -v python 2>/dev/null || true)"
    )
    [ -n "${CONDA_PREFIX:-}" ] && candidates=("$CONDA_PREFIX/bin/python" "${candidates[@]}")
    [ -n "${VIRTUAL_ENV:-}" ]  && candidates=("$VIRTUAL_ENV/bin/python" "${candidates[@]}")
    for p in "${candidates[@]}"; do
        if [ -n "$p" ] && [ -x "$p" ]; then
            local ver
            ver=$("$p" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo "False")
            [ "$ver" = "True" ] && echo "$p" && return 0
        fi
    done
    return 1
}

echo "=== LADOCK Desktop Installer ==="
echo ""

PYTHON="$(find_python)" || {
    echo "ERROR: Python 3.10+ not found."
    echo ""
    echo "Install Miniconda first:"
    echo "  wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
    echo "  bash Miniconda3-latest-Linux-x86_64.sh"
    exit 1
}

echo "Using Python: $PYTHON"
"$PYTHON" --version
echo ""

# ── Install dependencies ──────────────────────────────────────────────────────
echo "Installing LADOCK and its dependencies..."
if [ "$INSTALL_RDKIT" -eq 1 ]; then
    "$PYTHON" -m pip install -e "$SCRIPT_DIR[rdkit]"
else
    "$PYTHON" -m pip install -e "$SCRIPT_DIR"
fi

echo ""
echo "Making launcher executable..."
chmod +x "$SCRIPT_DIR/ladock.sh"

# ── Optional: desktop shortcut ────────────────────────────────────────────────
if [ -d "$HOME/.local/share/applications" ] && command -v xdg-user-dirs-update &>/dev/null; then
    DESKTOP_FILE="$HOME/.local/share/applications/ladock.desktop"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=LADOCK Desktop
Comment=Molecular Docking Workstation
Exec=bash $SCRIPT_DIR/ladock.sh
Icon=$SCRIPT_DIR/ladock_viewer.png
Terminal=false
Categories=Science;Chemistry;Education;
EOF
    echo "Desktop shortcut created: $DESKTOP_FILE"
fi

echo ""
echo "=== Installation complete! ==="
echo ""
echo "Launch LADOCK with:"
echo "  bash $SCRIPT_DIR/ladock.sh"
echo ""
echo "Or via the installed command (if $HOME/.local/bin is on PATH):"
echo "  ladock"
