#!/usr/bin/env python3
"""
ModemDiag — Open-Source AT Command Tester & Cellular Modem Diagnostic Tool
Copyright (c) 2026 Yassine El Harami — MIT License
"""
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from modemdiag import __version__


def main():
    # Qt 6 enables HiDPI scaling by default — nothing to configure here
    app = QApplication(sys.argv)

    from modemdiag.ui.main_window import MainWindow
    app.setApplicationName("ModemDiag")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("ModemDiag")

    # Apply dark theme before any window is created
    from modemdiag.ui.styles import DARK_THEME
    app.setStyleSheet(DARK_THEME)

    # Base font
    font = QFont("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
