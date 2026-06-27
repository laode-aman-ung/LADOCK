#!/usr/bin/env bash
# LADOCK Desktop launcher script
# Place this in ~/bin/ or /usr/local/bin/ for easy access

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$HOME/miniconda3/bin/python"

# Fallback to system python if miniconda not found
if [ ! -f "$PYTHON" ]; then
    PYTHON="$(which python3)"
fi

exec "$PYTHON" "$SCRIPT_DIR/main.py" "$@"
