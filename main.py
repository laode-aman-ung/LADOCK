#!/usr/bin/env python3
"""
LADOCK Desktop — Entry Point
Run with: ~/miniconda3/bin/python main.py
"""

import sys
import os
import platform


def _configure_qt_runtime():
    """Prefer software rendering on Linux/WSL so the UI can start without GPU EGL."""
    if not sys.platform.startswith("linux"):
        return

    env = os.environ
    release = platform.release().lower()
    is_wsl = "microsoft" in release or "wsl" in release or "WSL_DISTRO_NAME" in env

    env.setdefault("QT_OPENGL", "software")
    env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    env.setdefault("QT_QUICK_BACKEND", "software")
    env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "")

    if is_wsl:
        # WSLg often advertises Wayland even when EGL is unavailable for Qt WebEngine.
        # Forcing XCB avoids the failing Wayland/EGL path on many Ubuntu-on-WSL setups.
        env.setdefault("QT_QPA_PLATFORM", "xcb")

    chromium_flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    extra_flags = [
        "--enable-webgl",
        "--ignore-gpu-blocklist",
    ]
    if is_wsl:
        extra_flags.extend(
            [
                "--use-angle=swiftshader",
                "--disable-features=Vulkan",
                "--disable-gpu-compositing",
            ]
        )
    for flag in extra_flags:
        if flag not in chromium_flags:
            chromium_flags = f"{chromium_flags} {flag}".strip()
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = chromium_flags


_configure_qt_runtime()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSettings
from app.main_window import MainWindow
from app.project_manager import WelcomeDialog


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("LADOCK Desktop")
    app.setOrganizationName("LADOCK")

    window = MainWindow()
    window.show()

    # Show welcome dialog on first launch or if no recent projects
    settings = QSettings("LADOCK", "Desktop")
    show_welcome = settings.value("show_welcome", True)
    if show_welcome:
        dlg = WelcomeDialog(window)
        dlg.project_chosen.connect(window._project_mgr.set_project)
        dlg.exec()
        settings.setValue("show_welcome", False)
        settings.sync()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
