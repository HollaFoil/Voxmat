"""A small, self-contained HSV colour picker dialog.

Replaces the native ``QColorDialog`` (which renders the legacy Windows picker)
with a clean, predictable widget: a saturation/value plane, a hue bar, RGB/hex
fields and an optional alpha slider. Use :meth:`ColorPickerDialog.get_color`.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QSpinBox,
                               QVBoxLayout, QWidget)


class _SVPlane(QWidget):
    """Pick saturation (x) and value (y) for the current hue."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(220, 170)
        self._h = 0
        self._s = 255
        self._v = 255

    def set_hsv(self, h, s, v):
        self._h, self._s, self._v = h, s, v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor.fromHsv(self._h, 255, 255))
        sat = QLinearGradient(0, 0, r.width(), 0)
        sat.setColorAt(0.0, QColor(255, 255, 255, 255))
        sat.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(r, sat)
        val = QLinearGradient(0, 0, 0, r.height())
        val.setColorAt(0.0, QColor(0, 0, 0, 0))
        val.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(r, val)
        x = self._s / 255 * r.width()
        y = (1 - self._v / 255) * r.height()
        p.setPen(QPen(Qt.black, 2))
        p.drawEllipse(QPointF(x, y), 5, 5)
        p.setPen(QPen(Qt.white, 1))
        p.drawEllipse(QPointF(x, y), 5, 5)

    def _apply(self, pos):
        w, h = self.width(), self.height()
        self._s = int(min(max(pos.x(), 0), w) / w * 255)
        self._v = int((1 - min(max(pos.y(), 0), h) / h) * 255)
        self.update()
        self.changed.emit()

    def mousePressEvent(self, e):
        self._apply(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._apply(e.position())


class _HueBar(QWidget):
    """Vertical rainbow to pick hue (0-359)."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(22, 170)
        self._h = 0

    def set_hue(self, h):
        self._h = h
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        grad = QLinearGradient(0, 0, 0, r.height())
        for i in range(7):
            grad.setColorAt(i / 6, QColor.fromHsv(int(359 * i / 6), 255, 255))
        p.fillRect(r, grad)
        y = self._h / 359 * r.height()
        p.setPen(QPen(Qt.white, 2))
        p.drawLine(0, int(y), r.width(), int(y))
        p.setPen(QPen(Qt.black, 1))
        p.drawRect(0, int(y) - 1, r.width() - 1, 2)

    def _apply(self, pos):
        h = self.height()
        self._h = int(min(max(pos.y(), 0), h) / h * 359)
        self.update()
        self.changed.emit()

    def mousePressEvent(self, e):
        self._apply(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._apply(e.position())


class ColorPickerDialog(QDialog):
    def __init__(self, initial: QColor, has_alpha: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pick colour")
        self._has_alpha = has_alpha
        self._color = QColor(initial)
        self._updating = False

        # Fixed dark background, scoped to the dialog so it never tints with the
        # chosen colour (and isn't affected by a coloured parent widget).
        self.setObjectName("colorPickerDialog")
        self.setStyleSheet("#colorPickerDialog { background-color: #1e1e1e; }")

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.plane = _SVPlane()
        self.hue = _HueBar()
        top.addWidget(self.plane)
        top.addWidget(self.hue)

        fields = QFormLayout()
        self.r = self._byte()
        self.g = self._byte()
        self.b = self._byte()
        fields.addRow("R", self.r)
        fields.addRow("G", self.g)
        fields.addRow("B", self.b)
        self.a = self._byte()
        self.a.setValue(255)
        if has_alpha:
            fields.addRow("A", self.a)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.editingFinished.connect(self._from_hex)
        fields.addRow("Hex", self.hex)
        self.swatch = QLabel()
        self.swatch.setObjectName("colorPickerSwatch")
        self.swatch.setFixedHeight(28)
        fields.addRow("Preview", self.swatch)
        top.addLayout(fields)
        root.addLayout(top)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.plane.changed.connect(self._from_plane)
        self.hue.changed.connect(self._from_plane)
        for sb in (self.r, self.g, self.b, self.a):
            sb.valueChanged.connect(self._from_rgb)

        self._set_color(self._color)

    @staticmethod
    def _byte():
        s = QSpinBox()
        s.setRange(0, 255)
        return s

    # -- state sync -------------------------------------------------------
    def _set_color(self, c: QColor):
        self._updating = True
        self._color = QColor(c)
        h, s, v, _ = c.getHsv()
        h = max(h, 0)
        self.plane.set_hsv(h, s, v)
        self.hue.set_hue(h)
        self.r.setValue(c.red())
        self.g.setValue(c.green())
        self.b.setValue(c.blue())
        self.a.setValue(c.alpha())
        self.hex.setText(c.name())  # #rrggbb
        self.swatch.setStyleSheet(
            f"#colorPickerSwatch {{ background-color: {c.name()}; border: 1px solid #555; }}")
        self._updating = False

    def _from_plane(self):
        if self._updating:
            return
        c = QColor.fromHsv(self.hue._h, self.plane._s, self.plane._v,
                           self.a.value() if self._has_alpha else 255)
        self._set_color(c)

    def _from_rgb(self):
        if self._updating:
            return
        c = QColor(self.r.value(), self.g.value(), self.b.value(),
                   self.a.value() if self._has_alpha else 255)
        self._set_color(c)

    def _from_hex(self):
        if self._updating:
            return
        text = self.hex.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        c = QColor(text)
        if c.isValid():
            if self._has_alpha:
                c.setAlpha(self.a.value())
            self._set_color(c)

    def color(self) -> QColor:
        return QColor(self.r.value(), self.g.value(), self.b.value(),
                      self.a.value() if self._has_alpha else 255)

    @staticmethod
    def get_color(initial: QColor, has_alpha: bool = False, parent=None):
        dlg = ColorPickerDialog(initial, has_alpha, parent)
        if dlg.exec() == QDialog.Accepted:
            return dlg.color()
        return None
