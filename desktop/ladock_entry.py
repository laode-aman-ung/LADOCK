"""
Package entry point — used by pip-installed `ladock` / `ladock-gui` commands.
Delegates to main.py logic while ensuring the package root is on sys.path.
"""

import sys
import os

# Make the package root importable when running as an installed script
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from main import main  # noqa: E402


if __name__ == "__main__":
    main()
