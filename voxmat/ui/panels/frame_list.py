"""Frame list panel — select the current frame and drag to reorder frames."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from ...core.document import Document


class FrameListPanel(QListWidget):
    def __init__(self, document: Document, parent=None):
        super().__init__(parent)
        self.document = document
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self._syncing = False

        document.frames_changed.connect(self._reload)
        document.current_changed.connect(self._sync_current)
        self.currentRowChanged.connect(self._on_row_changed)
        self.model().rowsMoved.connect(self._on_rows_moved)
        self._reload()

    def _reload(self):
        if self._syncing:
            return
        self._syncing = True
        self.clear()
        for i, frame in enumerate(self.document.frames):
            item = QListWidgetItem(frame.name)
            item.setData(Qt.UserRole, i)   # original index, survives reorder
            self.addItem(item)
        self.setCurrentRow(self.document.current_frame_index)
        self._syncing = False

    def _sync_current(self):
        if self._syncing:
            return
        self._syncing = True
        self.setCurrentRow(self.document.current_frame_index)
        self._syncing = False

    def _on_row_changed(self, row: int):
        if self._syncing or row < 0:
            return
        self.document.set_current(row)

    def _on_rows_moved(self, *args):
        if self._syncing:
            return
        # Rebuild order from the original indices stored on each item.
        new_order = [self.item(i).data(Qt.UserRole) for i in range(self.count())]
        if sorted(new_order) != list(range(len(self.document.frames))):
            return
        self._syncing = True
        self.document.reorder_frames(new_order)
        self._syncing = False
        self._reload()
