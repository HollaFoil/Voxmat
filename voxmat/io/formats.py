"""Single source of truth for the ``.mmvox`` binary format.

All multi-byte values are little-endian. The Unity importer must mirror these
constants and the field order in :data:`MATERIAL_FIELDS`.

Layout
------
Header::

    char[4] magic       = "MMVX"
    u16     version
    u16     flags
    u16     dim_x, dim_y, dim_z
    u16     frame_count
    u16     material_count

Material table (``material_count`` entries)::

    u16     id
    u8      name_len
    char[name_len] name (utf-8)
    u8      use_voxel_color (0/1)
    f32[4]  albedo (r, g, b, a)
    f32     metallic
    f32     roughness
    f32[3]  emission_color (r, g, b)
    f32     emission_strength
    f32     transmission
    f32     ior
    u32     flags
    u16     extra_count
    extra_count x { u8 key_len; char[key_len] key; f32 value }

Frames (``frame_count`` entries)::

    u8      name_len
    char[name_len] name (utf-8)
    u32     voxel_count
    voxel_count x { u16 x, y, z; u16 material_id; u8 r, g, b, a }
"""

from __future__ import annotations

MAGIC = b"MMVX"
VERSION = 1

# Material ``flags`` bitfield (u32). Bit 0: emission uses the voxel's own colour
# instead of the material's emission_color.
MAT_FLAG_EMISSION_USE_VOXEL_COLOR = 1 << 0

# Header is read/written manually; these struct strings document the records.
HEADER_STRUCT = "<4sHHHHHHH"   # magic, version, flags, x, y, z, frame_count, mat_count

# Fixed-size middle block of a material record (everything except name & extras).
# u8 use_voxel_color, 4f albedo, f metallic, f roughness, 3f emission,
# f emission_strength, f transmission, f ior, I flags
MATERIAL_FIXED_STRUCT = "<B4fff3ffffI"

# Per-voxel record.
VOXEL_STRUCT = "<HHHH4B"        # x, y, z, material_id, r, g, b, a

# Ordered, human-readable description of the first-class material fields. The
# binary codec relies on MATERIAL_FIXED_STRUCT above; this list documents intent
# and is handy for UI generation. Keep the two in sync when adding fields.
MATERIAL_FIELDS = (
    ("use_voxel_color", "bool"),
    ("albedo", "f32x4"),
    ("metallic", "f32"),
    ("roughness", "f32"),
    ("emission_color", "f32x3"),
    ("emission_strength", "f32"),
    ("transmission", "f32"),
    ("ior", "f32"),
    ("flags", "u32"),
)
