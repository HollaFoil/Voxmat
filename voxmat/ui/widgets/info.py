"""Clickable info chip and a form-field helper.

The chip is painted (an antialiased disc with an "i") rather than relying on a
Unicode glyph, which rendered pixelated and unclear. It reveals its help text on
**click** (no hover — hover was clunky and unreliable over a continuously-
repainting render window) and keeps it up until the cursor leaves the chip, using
a small popup label we manage ourselves (``QToolTip`` got dismissed by the
mouse-release event). Pair it with :func:`with_info` to right-align the chips.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

_ACCENT = QColor("#5aa9e6")


class InfoChip(QWidget):
    """A small painted 'i' badge that shows ``tip`` on click and hides on leave."""

    _SIZE = 16

    def __init__(self, tip: str, parent=None):
        super().__init__(parent)
        self._tip = tip
        self._popup: QLabel | None = None
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_ACCENT)
        painter.drawEllipse(rect)
        painter.setPen(QColor("white"))
        font = self.font()
        font.setBold(True)
        font.setPointSizeF(self._SIZE * 0.62)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, "i")
        painter.end()

    def _ensure_popup(self) -> QLabel:
        if self._popup is None:
            popup = QLabel(self._tip, self, Qt.ToolTip)   # frameless, on-top, no focus
            popup.setStyleSheet(
                "QLabel { background: #2b2b2b; color: #eee;"
                " border: 1px solid #666; padding: 6px; }")
            self._popup = popup
        return self._popup

    def mousePressEvent(self, event) -> None:
        popup = self._ensure_popup()
        popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        popup.show()

    def leaveEvent(self, event) -> None:
        if self._popup is not None:
            self._popup.hide()
        super().leaveEvent(event)


def with_info(widget: QWidget, tip: str) -> QWidget:
    """Pack ``widget`` with a right-aligned :class:`InfoChip`, for use as the
    field of a form row so the chips align on the right edge."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(widget, 1)
    layout.addWidget(InfoChip(tip))
    return row
