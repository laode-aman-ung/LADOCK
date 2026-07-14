#!/usr/bin/env bash
# LADOCK Desktop — Linux/macOS launcher
# Usage: bash ladock.sh [args...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Python discovery ──────────────────────────────────────────────────────────
find_python() {
    local candidates=(
        "$HOME/miniconda3/bin/python"
        "$HOME/anaconda3/bin/python"
        "$HOME/miniforge3/bin/python"
        "$HOME/mambaforge/bin/python"
        "$HOME/.pyenv/shims/python3"
        "$HOME/.local/bin/python3"
        "$(command -v python3 2>/dev/null || true)"
        "$(command -v python 2>/dev/null || true)"
    )

    # Also search active conda environment
    if [ -n "${CONDA_PREFIX:-}" ] && [ -f "$CONDA_PREFIX/bin/python" ]; then
        echo "$CONDA_PREFIX/bin/python"
        return 0
    fi

    # Also search active virtual environment
    if [ -n "${VIRTUAL_ENV:-}" ] && [ -f "$VIRTUAL_ENV/bin/python" ]; then
        echo "$VIRTUAL_ENV/bin/python"
        return 0
    fi

    for p in "${candidates[@]}"; do
        if [ -n "$p" ] && [ -x "$p" ]; then
            # Skip Python < 3.10
            local ver
            ver=$("$p" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo "False")
            if [ "$ver" = "True" ]; then
                echo "$p"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON="$(find_python)" || {
    echo "ERROR: Python 3.10+ not found."
    echo ""
    echo "Install Miniconda (recommended):"
    echo "  wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
    echo "  bash Miniconda3-latest-Linux-x86_64.sh"
    echo ""
    echo "Or install system Python 3.10+:"
    echo "  sudo apt install python3 python3-pip   # Debian/Ubuntu"
    echo "  sudo dnf install python3               # Fedora/RHEL"
    exit 1
}

# ── Dependency check ──────────────────────────────────────────────────────────
if ! "$PYTHON" -c "import PySide6" &>/dev/null; then
    echo "ERROR: PySide6 is not installed for: $PYTHON"
    echo ""
    echo "Install all dependencies with:"
    echo "  $PYTHON -m pip install -e \"$SCRIPT_DIR\""
    echo ""
    echo "Or install PySide6 only:"
    echo "  $PYTHON -m pip install PySide6"
    exit 1
fi

# ── Launch ────────────────────────────────────────────────────────────────────
exec "$PYTHON" "$SCRIPT_DIR/main.py" "$@"
