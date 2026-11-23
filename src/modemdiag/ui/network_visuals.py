"""Qt-native, interactive radio diagrams and time plots; no extra dependencies."""
import math

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QLinearGradient
from PyQt6.QtWidgets import QWidget

from modemdiag.core.network_history import cell_key
from modemdiag.ui.theme import theme_color


def _color(value, alpha=None):
    color = QColor(theme_color(value))
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def _font(painter, size, bold=False):
    font = QFont(painter.font())
    font.setPointSizeF(size)
    font.setBold(bold)
    painter.setFont(font)


def _text(painter, rect, text, color="#e6edf3", size=9, bold=False):
    painter.setPen(_color(color))
    _font(painter, size, bold)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class SignalBars(QWidget):
    """A labelled scale accompanies this relative RSRP/RSSI indicator."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.value = None
        self.metric = "RSRP"
        self.setAccessibleName("Received signal power")

    def set_reading(self, metric, value):
        self.metric, self.value = metric, value
        self.setToolTip(f"{metric} scale: −140 to −44 dBm" if metric == "RSRP" else "RSSI scale: −113 to −51 dBm")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        lower, upper = (-140, -44) if self.metric == "RSRP" else (-113, -51)
        count = 0 if self.value is None else max(1, min(14, round(14 * (self.value - lower) / (upper - lower))))
        width = min(self.width(), 190) / 14
        for index in range(14):
            height = 5 + index * 0.9
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("#58a6ff" if index < count else "#30363d"))
            painter.drawRoundedRect(QRectF(index * width, 19 - height, width - 3, height), 1.5, 1.5)


class CellLandscape(QWidget):
    cell_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cells = []
        self.selected = -1
        self._nodes = []
        self.setMinimumHeight(245)
        self.setMinimumWidth(280)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Cell relationship diagram. Arrow keys select a cell.")

    def set_cells(self, cells):
        previous = cell_key(self.cells[self.selected]) if 0 <= self.selected < len(self.cells) else None
        self.cells = list(cells)
        self.selected = next((index for index, cell in enumerate(cells) if cell_key(cell) == previous), 0 if cells else -1)
        self.update()

    def set_selected(self, index):
        self.selected = index
        self.update()

    def _visible_indices(self):
        indices = sorted(range(len(self.cells)), key=lambda index: self.cells[index].role != "Serving")[:6]
        if self.selected >= 0 and self.selected not in indices:
            indices[-1:] = [self.selected]
        return indices

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2 - 5)
        rx, ry = max(65, self.width() / 2 - 65), max(55, self.height() / 2 - 50)
        painter.setPen(QPen(_color("#30363d", 140), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, rx, ry)
        painter.drawEllipse(center, rx * 0.58, ry * 0.58)
        self._nodes = []
        indices = self._visible_indices()
        angles = {1: [-90], 2: [-145, -35], 3: [-130, -20, 90],
                  4: [-145, -35, 35, 145], 5: [-150, -90, -30, 45, 135],
                  6: [-150, -90, -30, 30, 90, 150]}.get(len(indices), [])
        for index, angle in zip(indices, angles):
            cell = self.cells[index]
            radians = math.radians(angle)
            point = QPointF(center.x() + rx * math.cos(radians), center.y() + ry * math.sin(radians))
            rect = QRectF(point.x() - 56, point.y() - 32, 112, 64)
            self._nodes.append((index, rect))
            accent = "#3fb950" if cell.role == "Serving" else "#58a6ff"
            painter.setPen(QPen(_color(accent, 150 if cell.role == "Serving" else 75),
                                2 if cell.role == "Serving" else 1,
                                Qt.PenStyle.SolidLine if cell.role == "Serving" else Qt.PenStyle.DotLine))
            painter.drawLine(center, point)
            painter.setPen(QPen(_color(accent if index == self.selected else "#30363d"), 2 if index == self.selected else 1))
            painter.setBrush(_color("#0d1f33" if index == self.selected else "#1c2128"))
            painter.drawRoundedRect(rect, 9, 9)
            _text(painter, QRectF(rect.x(), rect.y() + 4, rect.width(), 16),
                  f"{cell.role.upper()} · {cell.technology}", accent, 7.5, True)
            code = "PSC" if cell.technology == "UMTS" else "PCI"
            identifier = f"{code} {cell.pci}" if cell.pci != "—" else cell.cell_id
            identifier = identifier if len(identifier) <= 15 else identifier[:12] + "…"
            _text(painter, QRectF(rect.x(), rect.y() + 22, rect.width(), 18), identifier, size=10, bold=True)
            power = cell.rsrp.text if cell.rsrp.value is not None else "Power not reported"
            _text(painter, QRectF(rect.x(), rect.y() + 42, rect.width(), 16), power, "#8b949e", 8)
        # Modem symbol: one central endpoint, with solid links to serving radios.
        hub = QRectF(center.x() - 37, center.y() - 22, 74, 44)
        painter.setPen(QPen(_color("#58a6ff"), 1.5))
        painter.setBrush(_color("#0d1117"))
        painter.drawRoundedRect(hub, 10, 10)
        _text(painter, hub, "MODEM", "#58a6ff", 9, True)
        if not indices:
            _text(painter, QRectF(0, self.height() - 36, self.width(), 22),
                  "Refresh to discover reported cells", "#8b949e", 9)
        elif len(self.cells) > len(indices):
            _text(painter, QRectF(0, self.height() - 18, self.width(), 18),
                  f"Showing {len(indices)} of {len(self.cells)} cells · all cells in the table below", "#8b949e", 8)

    def mousePressEvent(self, event):
        for index, rect in self._nodes:
            if rect.contains(event.position()):
                self.setFocus()
                self.cell_selected.emit(index)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        for index, rect in self._nodes:
            if rect.contains(event.position()):
                cell = self.cells[index]
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setToolTip(f"{cell.role} · {cell.technology}\nCell: {cell.cell_id} · PCI: {cell.pci}\n"
                                f"Channel: {cell.channel} · Band: {cell.band}\nRSRP: {cell.rsrp.text}")
                return
        self.unsetCursor()
        self.setToolTip("Schematic cell relationships. Positions do not represent tower locations or distance.")

    def keyPressEvent(self, event):
        if self.cells and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 1 if event.key() == Qt.Key.Key_Right else -1
            self.cell_selected.emit((self.selected + step) % len(self.cells))
            return
        super().keyPressEvent(event)


class SignalTimeline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = []
        self.metric = "RSRP"
        self.setMinimumHeight(185)
        self.setMinimumWidth(240)
        self.setAccessibleName("Signal measurements over time")

    def set_data(self, samples, metric):
        self.samples = list(samples)
        self.metric = metric
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(42, 10, max(1, self.width() - 54), max(1, self.height() - 36))
        low, high = {"RSRP": (-140, -40), "RSSI": (-120, -40),
                     "RSRQ": (-30, 0), "SINR": (-20, 40)}[self.metric]
        values = [sample.values.get(self.metric) for sample in self.samples]
        observed = [value for value in values if value is not None]
        if observed:
            low, high = min(low, math.floor(min(observed) / 10) * 10), max(high, math.ceil(max(observed) / 10) * 10)
        for index in range(5):
            y = area.top() + index * area.height() / 4
            painter.setPen(QPen(_color("#30363d", 150), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            _text(painter, QRectF(0, y - 8, 35, 16), f"{high - index * (high - low) / 4:g}", "#8b949e", 8)
        if not observed:
            _text(painter, area, "Refresh to start a signal trace", "#8b949e", 9)
            return
        first, last = self.samples[0].at.timestamp(), self.samples[-1].at.timestamp()
        span = max(1, last - first)
        points = []
        segments = []
        for sample, value in zip(self.samples, values):
            if value is None:
                if points:
                    segments.append(points)
                    points = []
                continue
            x = area.right() if len(self.samples) == 1 else area.left() + (sample.at.timestamp() - first) / span * area.width()
            y = area.bottom() - (value - low) / (high - low) * area.height()
            points.append(QPointF(x, y))
        if points:
            segments.append(points)
        for points in segments:
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            fill = QPainterPath(path)
            fill.lineTo(points[-1].x(), area.bottom())
            fill.lineTo(points[0].x(), area.bottom())
            fill.closeSubpath()
            gradient = QLinearGradient(area.topLeft(), area.bottomLeft())
            gradient.setColorAt(0, _color("#58a6ff", 65))
            gradient.setColorAt(1, _color("#58a6ff", 0))
            painter.fillPath(fill, gradient)
            painter.setPen(QPen(_color("#58a6ff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.setBrush(_color("#58a6ff"))
            painter.drawEllipse(points[-1], 3, 3)
        _text(painter, QRectF(area.left(), area.bottom() + 7, 75, 18), self.samples[0].at.strftime("%H:%M:%S"), "#8b949e", 8)
        _text(painter, QRectF(area.right() - 75, area.bottom() + 7, 75, 18), self.samples[-1].at.strftime("%H:%M:%S"), "#8b949e", 8)
