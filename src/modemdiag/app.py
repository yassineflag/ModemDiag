#!/usr/bin/env python3
"""
ModemDiag — Open-Source AT Command Tester & Cellular Modem Diagnostic Tool
Copyright (c) 2026 Yassine El Harami — MIT License
"""
import sys

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap, QPainter, QColor
from modemdiag import __version__


def main():
    # Qt 6 enables HiDPI scaling by default — nothing to configure here
    app = QApplication(sys.argv)
    app.setApplicationName("ModemDiag")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("ModemDiag")

    pixmap = QPixmap(420, 150)
    pixmap.fill(QColor("#161b22"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#58a6ff"))
    painter.setFont(QFont("Segoe UI", 23, QFont.Weight.Bold))
    painter.drawText(pixmap.rect().adjusted(0, 0, 0, -35), Qt.AlignmentFlag.AlignCenter, "ModemDiag")
    painter.end()
    splash = QSplashScreen(pixmap)
    splash.showMessage("Loading interface…", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, QColor("#e6edf3"))
    splash.show()
    app.processEvents()

    from modemdiag.ui.main_window import MainWindow

    # Apply dark theme before any window is created
    from modemdiag.ui.styles import DARK_THEME
    app.setStyleSheet(DARK_THEME)

    # Base font
    font = QFont("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)

    try:
        window = MainWindow()
        window.show()
    finally:
        splash.close()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
