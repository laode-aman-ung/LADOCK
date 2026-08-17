"""LADOCK Desktop — the PySide6 workstation behind ``ladock-desktop``.

Nothing is imported here on purpose: pulling PySide6 in at package-import time
would make ``import ladock.desktop.core.tool_paths`` (used by tooling and tests)
depend on a working Qt installation. Import :mod:`ladock.desktop.main` instead.
"""
