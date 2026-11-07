"""Shared dark/light colors for global styles, inline styles and live results."""
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette, QTextCursor
from PyQt6.QtWidgets import QLabel, QTableWidget, QTextBrowser, QWidget

from modemdiag.ui.styles import COLOR, DARK_THEME

_DARK_TERMINAL = dict(COLOR)
_CURRENT = "dark"
_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
_STYLE_ATTRIBUTE = re.compile(r"(\bstyle\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
_BACKGROUND_ROLE = Qt.ItemDataRole.UserRole + 97
_LIGHT = {
    "#010409": "#ffffff", "#0d1117": "#ffffff", "#161b22": "#f6f8fa",
    "#1c2128": "#eaeef2", "#21262d": "#d8dee4", "#292e36": "#eaeef2",
    "#30363d": "#afb8c1", "#484f58": "#6e7781", "#8b949e": "#57606a",
    "#c9d1d9": "#24292f", "#e6edf3": "#1f2328", "#79c0ff": "#0969da",
    "#58a6ff": "#0969da", "#388bfd": "#0969da", "#1f6feb": "#0969da",
    "#1158c7": "#0550ae", "#6e9ed6": "#dbeafe", "#1f3a5f": "#ddf4ff",
    "#0d1f33": "#ddf4ff", "#1a3a5c": "#b6e3ff", "#3a5a80": "#325d88",
    "#221600": "#fff8c5", "#3d2a00": "#ffec99", "#6e5000": "#735c0f",
    "#d29922": "#9a6700", "#e3b341": "#825600", "#ffd977": "#633c01",
    "#332800": "#fff8c5", "#0a1f12": "#dafbe1", "#1a4731": "#aceebb",
    "#206040": "#9be9a8", "#2a6040": "#2d593d", "#3fb950": "#1a7f37",
    "#26a641": "#1a7f37", "#56d364": "#116329", "#1a3a1a": "#dafbe1",
    "#1a3d1a": "#dafbe1", "#3d1a1a": "#ffebe9", "#6e2020": "#ffebe9",
    "#8a2929": "#ffcecb", "#ffa198": "#82071e", "#f85149": "#cf222e",
    "#1a1800": "#fff8c5", "#2d2500": "#ffec99", "#6a5a00": "#735c0f",
    "#ffd700": "#825600", "#e8c800": "#825600",
}


def theme_color(color: str) -> str:
    return _LIGHT.get(color.lower(), color) if _CURRENT == "light" else color


def themed_style(style: str) -> str:
    if _CURRENT == "dark":
        return style
    def replace(match):
        value = match.group()
        if len(value) == 9:
            color = QColor(theme_color(value[:7]))
            return f"rgba({color.red()}, {color.green()}, {color.blue()}, {int(value[7:], 16)})"
        return theme_color(value)
    return _COLOR_PATTERN.sub(replace, style)


def set_style(widget, style: str):
    widget.setProperty("_theme_style", style)
    widget.setStyleSheet(themed_style(style))


def themed_html(text: str) -> str:
    # Translate style attributes, never command text or values resembling colors.
    return _STYLE_ATTRIBUTE.sub(
        lambda match: match[1] + match[2] + themed_style(match[3]) + match[2], text)


def set_html(widget, text: str):
    widget.setProperty("_theme_html", text)
    widget.setText(themed_html(text))


def set_tooltip(widget, text: str):
    widget.setProperty("_theme_tooltip", text)
    widget.setToolTip(themed_html(text))


def set_background(item, color: str):
    item.setData(_BACKGROUND_ROLE, color)
    item.setBackground(QColor(theme_color(color)))


def _recolor_terminal(browser, old_colors, new_colors):
    replacements = {old_colors[key]: new_colors[key] for key in old_colors}
    document = browser.document()
    changes = []
    block = document.begin()
    while block.isValid():
        fragment_iterator = block.begin()
        while not fragment_iterator.atEnd():
            fragment = fragment_iterator.fragment()
            if fragment.isValid():
                color = fragment.charFormat().foreground().color().name()
                if color in replacements:
                    changes.append((fragment.position(), fragment.length(), replacements[color]))
            fragment_iterator += 1
        block = block.next()
    # Change only formatting; modem response text and command history stay intact.
    cursor = QTextCursor(document)
    for start, length, color in changes:
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
        formatting = cursor.charFormat()
        formatting.setForeground(QColor(color))
        cursor.setCharFormat(formatting)


def apply_theme(app, name: str, root=None):
    global _CURRENT
    if name not in ("dark", "light"):
        raise ValueError("Unknown theme")
    old_colors = dict(COLOR)
    _CURRENT = name
    COLOR.update({key: theme_color(value) for key, value in _DARK_TERMINAL.items()})
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#0d1117"),
                        (QPalette.ColorRole.Base, "#010409"),
                        (QPalette.ColorRole.AlternateBase, "#161b22"),
                        (QPalette.ColorRole.Button, "#21262d"),
                        (QPalette.ColorRole.WindowText, "#e6edf3"),
                        (QPalette.ColorRole.Text, "#e6edf3"),
                        (QPalette.ColorRole.ButtonText, "#e6edf3"),
                        (QPalette.ColorRole.PlaceholderText, "#8b949e"),
                        (QPalette.ColorRole.ToolTipBase, "#1c2128"),
                        (QPalette.ColorRole.ToolTipText, "#e6edf3"),
                        (QPalette.ColorRole.Highlight, "#1f6feb")):
        palette.setColor(role, QColor(theme_color(color)))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(theme_color("#484f58")))
    stylesheet = themed_style(DARK_THEME)
    if app.styleSheet() != stylesheet:
        app.setPalette(palette)
        app.setStyleSheet(stylesheet)
    widgets = [root, *root.findChildren(QWidget)] if root is not None else app.allWidgets()
    for widget in widgets:
        style = widget.property("_theme_style")
        if style is not None:
            widget.setStyleSheet(themed_style(style))
        tooltip = widget.property("_theme_tooltip")
        if tooltip is not None:
            widget.setToolTip(themed_html(tooltip))
        if isinstance(widget, QLabel):
            html = widget.property("_theme_html")
            if html is not None:
                widget.setText(themed_html(html))
        if isinstance(widget, QTextBrowser):
            _recolor_terminal(widget, old_colors, COLOR)
        if isinstance(widget, QTableWidget):
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item is not None and item.data(_BACKGROUND_ROLE) is not None:
                        item.setBackground(QColor(theme_color(item.data(_BACKGROUND_ROLE))))
