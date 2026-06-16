"""Read/write the ``.mmvox`` binary format described in :mod:`voxmat.io.formats`.

The on-disk layout is intentionally simple and sparse so a Unity ``BinaryReader``
importer can mirror it field-for-field.
"""

from __future__ import annotations

import struct
from pathlib import Path

from ..core.document import Document
from ..core.frame import Frame
from ..core.grid import VoxelGrid
from ..core.material import Material, MaterialLibrary
from . import formats as fmt


# -- helpers --------------------------------------------------------------

def _write_str(buf: bytearray, s: str, length_bytes: int = 1) -> None:
    data = s.encode("utf-8")
    if length_bytes == 1:
        if len(data) > 255:
            data = data[:255]
        buf += struct.pack("<B", len(data))
    else:
        buf += struct.pack("<H", len(data))
    buf += data


def _read_str(data: bytes, off: int, length_bytes: int = 1) -> tuple[str, int]:
    if length_bytes == 1:
        n = data[off]
        off += 1
    else:
        n = struct.unpack_from("<H", data, off)[0]
        off += 2
    s = data[off:off + n].decode("utf-8")
    return s, off + n


# -- write ----------------------------------------------------------------

def _pack_material(buf: bytearray, m: Material) -> None:
    buf += struct.pack("<H", m.id)
    _write_str(buf, m.name, 1)
    # flags bit 0 carries "emission uses voxel colour" (see formats.MAT_FLAG_*).
    flags = (m.flags & ~fmt.MAT_FLAG_EMISSION_USE_VOXEL_COLOR)
    if m.emission_use_voxel_color:
        flags |= fmt.MAT_FLAG_EMISSION_USE_VOXEL_COLOR
    buf += struct.pack(
        fmt.MATERIAL_FIXED_STRUCT,
        1 if m.use_voxel_color else 0,
        m.albedo[0], m.albedo[1], m.albedo[2], m.albedo[3],
        m.metallic, m.roughness,
        m.emission_color[0], m.emission_color[1], m.emission_color[2],
        m.emission_strength, m.transmission, m.ior, flags & 0xFFFFFFFF,
    )
    buf += struct.pack("<H", len(m.extra))
    for key, value in m.extra.items():
        _write_str(buf, key, 1)
        buf += struct.pack("<f", float(value))


def _pack_frame(buf: bytearray, frame: Frame) -> None:
    _write_str(buf, frame.name, 1)
    grid = frame.grid
    coords = grid.filled_coords()                       # (N, 3) x,y,z
    buf += struct.pack("<I", len(coords))
    if len(coords) == 0:
        return
    xs, ys, zs = coords[:, 0], coords[:, 1], coords[:, 2]
    mids = grid.material_id[xs, ys, zs]
    cols = grid.rgba[xs, ys, zs]
    # Pack per voxel (u16 x3, u16, u8 x4); block counts are small (sparse blocks).
    for i in range(len(coords)):
        buf += struct.pack(fmt.VOXEL_STRUCT,
                           int(xs[i]), int(ys[i]), int(zs[i]), int(mids[i]),
                           int(cols[i, 0]), int(cols[i, 1]),
                           int(cols[i, 2]), int(cols[i, 3]))


def write_mmvox(path: str | Path, doc: Document) -> None:
    if not doc.frames:
        raise ValueError("Document has no frames to export")
    x, y, z = doc.dims
    buf = bytearray()
    buf += struct.pack(fmt.HEADER_STRUCT, fmt.MAGIC, fmt.VERSION, 0,
                       x, y, z, len(doc.frames), len(doc.materials))
    for m in doc.materials:
        _pack_material(buf, m)
    for frame in doc.frames:
        _pack_frame(buf, frame)
    Path(path).write_bytes(buf)


# -- read -----------------------------------------------------------------

def _unpack_material(data: bytes, off: int) -> tuple[Material, int]:
    (mid,) = struct.unpack_from("<H", data, off); off += 2
    name, off = _read_str(data, off, 1)
    fixed = struct.unpack_from(fmt.MATERIAL_FIXED_STRUCT, data, off)
    off += struct.calcsize(fmt.MATERIAL_FIXED_STRUCT)
    (use_vc, ar, ag, ab, aa, metallic, roughness,
     er, eg, eb, emis, transmission, ior, flags) = fixed
    (extra_count,) = struct.unpack_from("<H", data, off); off += 2
    extra: dict[str, float] = {}
    for _ in range(extra_count):
        key, off = _read_str(data, off, 1)
        (val,) = struct.unpack_from("<f", data, off); off += 4
        extra[key] = val
    emis_voxel = bool(flags & fmt.MAT_FLAG_EMISSION_USE_VOXEL_COLOR)
    mat = Material(
        id=mid, name=name, use_voxel_color=bool(use_vc),
        albedo=(ar, ag, ab, aa), metallic=metallic, roughness=roughness,
        emission_color=(er, eg, eb), emission_strength=emis,
        emission_use_voxel_color=emis_voxel,
        transmission=transmission, ior=ior,
        flags=flags & ~fmt.MAT_FLAG_EMISSION_USE_VOXEL_COLOR, extra=extra,
    )
    return mat, off


def read_mmvox(path: str | Path) -> Document:
    data = Path(path).read_bytes()
    off = 0
    magic, version, flags, x, y, z, frame_count, mat_count = \
        struct.unpack_from(fmt.HEADER_STRUCT, data, off)
    off += struct.calcsize(fmt.HEADER_STRUCT)
    if magic != fmt.MAGIC:
        raise ValueError(f"Not an mmvox file (magic={magic!r})")
    if version != fmt.VERSION:
        raise ValueError(f"Unsupported mmvox version {version}")

    library = MaterialLibrary()       # already contains default id 0
    for _ in range(mat_count):
        mat, off = _unpack_material(data, off)
        if mat.id == 0:
            library.update(mat)       # overwrite default with stored one
        else:
            library.add(mat)

    frames: list[Frame] = []
    for _ in range(frame_count):
        name, off = _read_str(data, off, 1)
        (voxel_count,) = struct.unpack_from("<I", data, off); off += 4
        grid = VoxelGrid.empty((x, y, z))
        rec_size = struct.calcsize(fmt.VOXEL_STRUCT)
        for _v in range(voxel_count):
            vx, vy, vz, mid, r, g, b, a = \
                struct.unpack_from(fmt.VOXEL_STRUCT, data, off)
            off += rec_size
            grid.rgba[vx, vy, vz] = (r, g, b, a)
            grid.material_id[vx, vy, vz] = mid
        frames.append(Frame(name, grid))

    doc = Document(frames)
    doc.materials = library
    return doc
