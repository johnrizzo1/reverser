# PyInstaller spec for the reverser GUI service.
# Build with: pyinstaller desktop/reverser-service.spec
# Output: desktop/python-dist/reverser-service/
#
# We use --onedir mode (the default for a .spec file with COLLECT) for
# instant startup and simpler code signing. The Electron supervisor spawns
# desktop/python-dist/reverser-service/reverser-service directly.
#
# CI runs this on one platform per matrix job; there's no platform-disambiguation
# subdir under python-dist/. Local developers who switch platforms should
# `rm -rf desktop/python-dist/` before rebuilding.

from pathlib import Path

# Entry point: a thin wrapper that imports reverser.gui_service as a package,
# then calls its main() function. We cannot point PyInstaller directly at
# __main__.py because that file uses relative imports (from .app import …)
# which only work when Python loads it as part of the package; running it as a
# standalone script causes ImportError at boot. The wrapper (checked in next to
# this spec) does `from reverser.gui_service.__main__ import main` which forces
# Python to load the full package hierarchy first.
a = Analysis(
    ["reverser_service_entry.py"],
    pathex=["../src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        # FastAPI/Starlette/Pydantic v2 use runtime introspection that
        # PyInstaller's static analysis sometimes misses. Listing the
        # modules explicitly is the standard mitigation.
        "uvicorn.logging",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.loops",
        "uvicorn.loops.auto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev-only deps — never used at runtime
        "pytest",
        "pytest_asyncio",
        "_pytest",
        # GUI toolkit not used
        "tkinter",
        # Jupyter / IPython not used
        "IPython",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="reverser-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="reverser-service",
)
