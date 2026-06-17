# PyInstaller build spec for Voxmat.  Build with:  pyinstaller voxmat.spec
# Produces a one-folder app under dist/Voxmat/ (Voxmat[.exe] + an _internal dir).
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# Executable icon (Windows .ico / macOS .icns); ignored elsewhere.
_icon = {"win32": "assets/icon.ico", "darwin": "assets/icon.icns"}.get(sys.platform)
if _icon and not os.path.exists(_icon):
    _icon = None

# Files loaded at runtime by path (not imported), so PyInstaller can't find them
# on its own — keep these in sync with voxmat/_resources.py:resource_root().
datas = [
    ("voxmat/render/shaders", "voxmat/render/shaders"),  # GLSL passes
    ("assets", "assets"),                                # default_env.hdr
    ("samples", "samples"),                              # cornell_box.mmvox
]

# moderngl loads its GL backend through glcontext; imageio loads format plugins
# lazily — pull both in explicitly.
hiddenimports = collect_submodules("glcontext") + collect_submodules("imageio")

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Voxmat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app — no console window
    disable_windowed_traceback=False,
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Voxmat",
)
