"""Undo/redo command stack.

Commands capture just enough state to reverse themselves. The stack is UI-side
so the core document stays a plain data model; new editing actions become
undoable simply by wrapping them in a :class:`Command` and pushing.
"""

from __future__ import annotations

import numpy as np

from ..core.document import Document
from ..core.grid import VoxelGrid


class Command:
    label = "command"

    def do(self) -> None: ...
    def undo(self) -> None: ...


class AssignMaterialCommand(Command):
    """Assign ``material_id`` to a set of voxels, remembering their old ids."""

    label = "assign material"

    def __init__(self, grid: VoxelGrid, mask: np.ndarray, material_id: int):
        self.grid = grid
        self.coords = np.argwhere(mask)
        self.material_id = material_id
        xs, ys, zs = self.coords[:, 0], self.coords[:, 1], self.coords[:, 2]
        self._old = grid.material_id[xs, ys, zs].copy()

    def do(self) -> None:
        xs, ys, zs = self.coords[:, 0], self.coords[:, 1], self.coords[:, 2]
        self.grid.material_id[xs, ys, zs] = self.material_id

    def undo(self) -> None:
        xs, ys, zs = self.coords[:, 0], self.coords[:, 1], self.coords[:, 2]
        self.grid.material_id[xs, ys, zs] = self._old


class UndoStack:
    def __init__(self, document: Document):
        self.document = document
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def push(self, command: Command) -> None:
        command.do()
        self._undo.append(command)
        self._redo.clear()
        self.document.materials_changed.emit()

    def undo(self) -> None:
        if not self._undo:
            return
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)
        self.document.materials_changed.emit()

    def redo(self) -> None:
        if not self._redo:
            return
        cmd = self._redo.pop()
        cmd.do()
        self._undo.append(cmd)
        self.document.materials_changed.emit()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
