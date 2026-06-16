"""Frame — a named voxel volume. A Document holds an ordered list of frames."""

from __future__ import annotations

from dataclasses import dataclass

from .grid import VoxelGrid


@dataclass
class Frame:
    name: str
    grid: VoxelGrid

    @property
    def dims(self):
        return self.grid.dims

    def copy(self) -> "Frame":
        return Frame(self.name, self.grid.copy())

    def __repr__(self) -> str:
        return f"Frame({self.name!r}, {self.grid!r})"
