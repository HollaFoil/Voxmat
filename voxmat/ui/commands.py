"""Undo/redo command stack.

Every state-changing edit is wrapped in a :class:`Command` that knows how to
``do`` and ``undo`` itself and emits the document signal the UI listens on. New
editing actions become undoable simply by pushing a command — the panels never
mutate the document directly. Adjacent edits to the *same* thing (e.g. dragging a
spin box) coalesce via :meth:`Command.merge` so they collapse to one undo step.
"""

from __future__ import annotations

import time

import numpy as np

from ..core.document import Document
from ..core.grid import VoxelGrid
from ..core.material import Material


class Command:
    label = "command"

    def do(self) -> None: ...
    def undo(self) -> None: ...

    def merge(self, other: "Command") -> bool:
        """Try to absorb ``other`` (just pushed) into this command. Return True if
        merged so the stack keeps a single entry. Default: never merge."""
        return False


class AssignMaterialCommand(Command):
    """Assign ``material_id`` to a set of voxels, remembering their old ids."""

    label = "assign material"

    def __init__(self, document: Document, grid: VoxelGrid, mask: np.ndarray,
                 material_id: int):
        self.document = document
        self.grid = grid
        self.coords = np.argwhere(mask)
        self.material_id = material_id
        xs, ys, zs = self.coords[:, 0], self.coords[:, 1], self.coords[:, 2]
        self._old = grid.material_id[xs, ys, zs].copy()

    def do(self) -> None:
        xs, ys, zs = self.coords[:, 0], self.coords[:, 1], self.coords[:, 2]
        self.grid.material_id[xs, ys, zs] = self.material_id
        self.document.materials_changed.emit()

    def undo(self) -> None:
        xs, ys, zs = self.coords[:, 0], self.coords[:, 1], self.coords[:, 2]
        self.grid.material_id[xs, ys, zs] = self._old
        self.document.materials_changed.emit()


class MaterialEditCommand(Command):
    """Replace one material's properties with an edited version."""

    label = "edit material"
    _MERGE_WINDOW = 0.6   # seconds; rapid edits to the same material collapse

    def __init__(self, document: Document, before: Material, after: Material):
        self.document = document
        self.id = before.id
        self.before = before
        self.after = after
        self._t = time.monotonic()

    def do(self) -> None:
        self.document.materials.update(self.after)
        self.document.materials_changed.emit()

    def undo(self) -> None:
        self.document.materials.update(self.before)
        self.document.materials_changed.emit()

    def merge(self, other: Command) -> bool:
        if (isinstance(other, MaterialEditCommand) and other.id == self.id
                and other._t - self._t < self._MERGE_WINDOW):
            self.after = other.after          # keep original 'before', take latest
            self._t = other._t
            return True
        return False


class AddMaterialCommand(Command):
    """Add a material to the library (``do`` is idempotent: the panel may have
    created it already via ``MaterialLibrary.new``)."""

    label = "add material"

    def __init__(self, document: Document, material: Material):
        self.document = document
        self.material = material

    def do(self) -> None:
        if self.material.id not in self.document.materials:
            self.document.materials.add(self.material)
        self.document.materials_changed.emit()

    def undo(self) -> None:
        self.document.materials.remove(self.material.id)
        self.document.materials_changed.emit()


class RemoveMaterialCommand(Command):
    """Remove a material; undo restores it (voxels keep their id meanwhile)."""

    label = "remove material"

    def __init__(self, document: Document, material: Material):
        self.document = document
        self.material = material

    def do(self) -> None:
        self.document.materials.remove(self.material.id)
        self.document.materials_changed.emit()

    def undo(self) -> None:
        if self.material.id not in self.document.materials:
            self.document.materials.add(self.material)
        self.document.materials_changed.emit()


class OrientationCommand(Command):
    """A flip / rotate / swap applied in place to every frame, with its inverse.

    ``flip`` and ``swap`` are their own inverse; ``rotate`` reverses with three
    further quarter turns. The document methods emit the frame/selection signals.
    """

    def __init__(self, document: Document, kind: str, axis: int = 0):
        self.document = document
        self.kind = kind
        self.axis = axis
        self.label = f"{kind} orientation"

    def do(self) -> None:
        self._apply(forward=True)

    def undo(self) -> None:
        self._apply(forward=False)

    def _apply(self, forward: bool) -> None:
        if self.kind == "flip":
            self.document.flip_axis(self.axis)
        elif self.kind == "swap":
            self.document.swap_xy()
        elif self.kind == "rotate":
            self.document.rotate90(1 if forward else 3)


class ReorderFramesCommand(Command):
    """Reorder frames by a permutation of indices; undo applies the inverse."""

    label = "reorder frames"

    def __init__(self, document: Document, new_order: list[int]):
        self.document = document
        self.new_order = list(new_order)
        inverse = [0] * len(new_order)
        for position, index in enumerate(new_order):
            inverse[index] = position
        self.inverse = inverse

    def do(self) -> None:
        self.document.reorder_frames(self.new_order)

    def undo(self) -> None:
        self.document.reorder_frames(self.inverse)


class UndoStack:
    def __init__(self, document: Document):
        self.document = document
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def push(self, command: Command) -> None:
        command.do()
        if self._undo and self._undo[-1].merge(command):
            pass                              # coalesced into the previous entry
        else:
            self._undo.append(command)
        self._redo.clear()

    def undo(self) -> None:
        if not self._undo:
            return
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)

    def redo(self) -> None:
        if not self._redo:
            return
        cmd = self._redo.pop()
        cmd.do()
        self._undo.append(cmd)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
