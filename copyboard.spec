# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for CopyBoard.

Build locally:
    pip install pyinstaller
    pyinstaller copyboard.spec
"""

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = collect_submodules("internal")
hiddenimports += [
    "zeroconf",
    "cryptography",
    "PIL",
    "pystray",
    "ttkbootstrap",
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "logging.handlers",
]

if sys.platform == "darwin":
    hiddenimports += ["pyobjc_framework_Cocoa"]

a = Analysis(
    ["cmd/main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="copyboard",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=True,
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
        upx=True,
        upx_exclude=[],
        name="copyboard",
    )
    app = BUNDLE(
        coll,
        name="copyboard.app",
        icon=None,
        bundle_identifier="com.copyboard.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSUIElement": True,
            "NSAppTransportSecurity": {
                "NSAllowsLocalNetworking": True,
            },
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="copyboard",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
