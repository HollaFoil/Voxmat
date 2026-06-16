"""Material model.

A ``Material`` is a PBR + GI description referenced by ``VoxelGrid.material_id``.
Fields are explicit for the common PBR/GI properties, plus an open ``extra``
dict of named floats so new properties can be added without breaking the file
format (see ``io.binary_export``). Adding a *first-class* field is a 3-step
change: dataclass field here, default below, and a struct entry in
``io.formats`` — nothing in the UI hard-codes the field list.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass
class Material:
    id: int
    name: str = "Material"
    # If True, the renderer uses the voxel's own imported colour as albedo and
    # ``albedo`` below is treated as a tint/multiplier.
    use_voxel_color: bool = True
    albedo: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    metallic: float = 0.0
    roughness: float = 0.5
    emission_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    emission_strength: float = 0.0
    # When True, emission uses the voxel's own colour instead of emission_color.
    emission_use_voxel_color: bool = False
    transmission: float = 0.0          # 0 = opaque, 1 = fully transmissive
    ior: float = 1.45                  # index of refraction
    flags: int = 0                     # bitfield, reserved (e.g. unlit, double-sided)
    extra: dict[str, float] = field(default_factory=dict)

    def copy(self) -> "Material":
        return replace(self, extra=dict(self.extra))


class MaterialLibrary:
    """Ordered collection of materials keyed by id. Id 0 is the default."""

    def __init__(self):
        self._materials: dict[int, Material] = {}
        self._next_id = 0
        self.add(Material(id=0, name="Default"))

    # -- access -----------------------------------------------------------
    def get(self, material_id: int) -> Material:
        return self._materials[material_id]

    def __contains__(self, material_id: int) -> bool:
        return material_id in self._materials

    def __iter__(self):
        return iter(self._materials.values())

    def __len__(self) -> int:
        return len(self._materials)

    @property
    def materials(self) -> list[Material]:
        return list(self._materials.values())

    # -- mutation ---------------------------------------------------------
    def add(self, material: Material) -> Material:
        """Register ``material``, preserving its id (used when loading a file).

        Raises if the id is already taken; use :meth:`new` to allocate one.
        """
        if material.id in self._materials:
            raise ValueError(f"Material id {material.id} already exists")
        self._materials[material.id] = material
        self._next_id = max(self._next_id, material.id + 1)
        return material

    def new(self, name: str | None = None) -> Material:
        """Create, register and return a fresh material with an allocated id."""
        mat = Material(id=self._next_id, name=name or f"Material {self._next_id}")
        return self.add(mat)

    def remove(self, material_id: int) -> None:
        if material_id == 0:
            raise ValueError("Cannot remove the default material (id 0)")
        self._materials.pop(material_id, None)

    def update(self, material: Material) -> None:
        if material.id not in self._materials:
            raise KeyError(material.id)
        self._materials[material.id] = material
