"""Document — the editable project: frames, materials, and current selection.

Kept free of Qt so it stays unit-testable and reusable. UI code subscribes to
the lightweight :class:`Signal` callbacks to react to changes.
"""

from __future__ import annotations

from typing import Callable

from .frame import Frame
from .material import MaterialLibrary
from .selection import Selection


class Signal:
    """Minimal observer: ``connect`` callbacks, ``emit`` to call them."""

    def __init__(self):
        self._slots: list[Callable] = []

    def connect(self, slot: Callable) -> None:
        self._slots.append(slot)

    def disconnect(self, slot: Callable) -> None:
        if slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args, **kwargs) -> None:
        for slot in list(self._slots):
            slot(*args, **kwargs)


class Document:
    def __init__(self, frames: list[Frame] | None = None):
        self.frames: list[Frame] = frames or []
        self.materials = MaterialLibrary()
        self.current_frame_index: int = 0
        self.selection: Selection = Selection(self.dims) if self.frames else Selection((1, 1, 1))

        # signals
        self.frames_changed = Signal()      # frame list/order/content changed
        self.current_changed = Signal()     # current frame index changed
        self.selection_changed = Signal()   # selection mask changed
        self.materials_changed = Signal()   # library changed or assignment happened

    # -- dims / current frame --------------------------------------------
    @property
    def dims(self) -> tuple[int, int, int]:
        if not self.frames:
            return (0, 0, 0)
        return self.frames[0].dims

    @property
    def current_frame(self) -> Frame | None:
        if not self.frames:
            return None
        return self.frames[self.current_frame_index]

    def set_current(self, index: int) -> None:
        if not self.frames:
            return
        self.current_frame_index = max(0, min(index, len(self.frames) - 1))
        self.current_changed.emit()

    # -- frame management -------------------------------------------------
    def set_frames(self, frames: list[Frame]) -> None:
        if not frames:
            raise ValueError("Document needs at least one frame")
        dims = frames[0].dims
        if any(f.dims != dims for f in frames):
            raise ValueError("All frames must share the same dimensions")
        self.frames = frames
        self.current_frame_index = 0
        self.selection = Selection(dims)
        self.frames_changed.emit()
        self.current_changed.emit()
        self.selection_changed.emit()

    def reorder_frames(self, new_order: list[int]) -> None:
        """Reorder by a permutation of indices."""
        if sorted(new_order) != list(range(len(self.frames))):
            raise ValueError("new_order must be a permutation of frame indices")
        current = self.frames[self.current_frame_index]
        self.frames = [self.frames[i] for i in new_order]
        self.current_frame_index = self.frames.index(current)
        self.frames_changed.emit()

    # -- orientation (applied in place to every frame) -------------------
    def flip_axis(self, axis: int) -> None:
        for frame in self.frames:
            frame.grid.flip_axis(axis)
        self.selection = Selection(self.dims)
        self.frames_changed.emit()
        self.selection_changed.emit()

    def swap_xy(self) -> None:
        for frame in self.frames:
            frame.grid.transpose_xy()
        self.selection = Selection(self.dims)
        self.frames_changed.emit()
        self.selection_changed.emit()

    def rotate90(self, k: int = 1) -> None:
        for frame in self.frames:
            frame.grid.rotate90(k)
        self.selection = Selection(self.dims)
        self.frames_changed.emit()
        self.selection_changed.emit()

    # -- selection / materials -------------------------------------------
    def assign_material_to_selection(self, material_id: int) -> None:
        frame = self.current_frame
        if frame is None or self.selection.is_empty:
            return
        if material_id not in self.materials:
            raise KeyError(material_id)
        frame.grid.assign_material(self.selection.mask, material_id)
        self.materials_changed.emit()

    def emit_selection_changed(self) -> None:
        self.selection_changed.emit()
