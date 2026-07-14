#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="${1:-}"
MAIN_PY="${2:-}"

if [[ -z "$SCRIPT_DIR" || -z "$MAIN_PY" ]]; then
    echo "Usage: ladock-wsl.sh <script_dir> <main_py>"
    exit 1
fi

if [[ ! -f "$MAIN_PY" ]]; then
    echo "main.py was not found:"
    echo "  $MAIN_PY"
    exit 1
fi

PYTHON_CMD=""
if [[ -x "$HOME/miniconda3/bin/python" ]]; then
    PYTHON_CMD="$HOME/miniconda3/bin/python"
elif [[ -x "$HOME/anaconda3/bin/python" ]]; then
    PYTHON_CMD="$HOME/anaconda3/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="$(command -v python)"
fi

if [[ -z "$PYTHON_CMD" ]]; then
    echo "Python 3 was not found in WSL Ubuntu."
    echo "Tried: \$HOME/miniconda3/bin/python, \$HOME/anaconda3/bin/python, python3, python"
    exit 1
fi

if ! "$PYTHON_CMD" -c "import PySide6" >/dev/null 2>&1; then
    printf 'PySide6 is not installed in WSL Python: %s\n' "$PYTHON_CMD"
    echo "Install dependencies with:"
    printf '  %s -m pip install -e %s\n' "$PYTHON_CMD" "$SCRIPT_DIR"
    exit 1
fi

cd "$SCRIPT_DIR"
exec "$PYTHON_CMD" "$MAIN_PY"
