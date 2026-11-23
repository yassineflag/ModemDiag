# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).parents[1]
bundle_mode = os.environ.get("MODEMDIAG_BUNDLE_MODE", "onedir" if sys.platform == "win32" else "onefile")
if bundle_mode not in ("onedir", "onefile"):
    raise ValueError("MODEMDIAG_BUNDLE_MODE must be onedir or onefile")

datas = [
    (str(ROOT / "LICENSE"),     "."),
    (str(ROOT / "resources" / "commands"),    "commands"),
    (str(ROOT / "resources" / "diagnostics"), "diagnostics"),
    (str(ROOT / "resources" / "macros"), "macros"),
    (str(ROOT / "resources" / "assets"),      "assets"),
]

hiddenimports = [
    "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.QtNetwork", "PyQt6.sip",
    "serial", "serial.serialutil", "serial.serialposix", "serial.serialwin32",
    "yaml",
    "modemdiag", "modemdiag.app", "modemdiag.core", "modemdiag.core.paths",
    "modemdiag.core.logger", "modemdiag.core.serial_manager",
    "modemdiag.core.command_runner", "modemdiag.core.at_parser", "modemdiag.core.port_detector",
    "modemdiag.core.release_policy", "modemdiag.core.release_check", "modemdiag.core.appearance",
    "modemdiag.core.network_status", "modemdiag.core.network_history",
    "modemdiag.core.ai_client", "modemdiag.core.ai_diagnostics",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "modemdiag.ui", "modemdiag.ui.main_window", "modemdiag.ui.command_panel", "modemdiag.ui.terminal_panel",
    "modemdiag.ui.port_selector", "modemdiag.ui.automation_panel", "modemdiag.ui.styles", "modemdiag.ui.theme",
    "modemdiag.ui.dashboard_panel", "modemdiag.ui.network_visuals", "modemdiag.ui.ai_panel",
    # Windows-only — safe to list; ignored on Linux
    "winreg",
]

_ico = ROOT / "resources" / "assets" / "icon.ico"
_png = ROOT / "resources" / "assets" / "icon.png"
icon_file = str(_ico) if (sys.platform == "win32" and _ico.exists()) else \
            str(_png) if _png.exists() else None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "scipy",
              "PIL", "cv2", "IPython", "jupyter", "setuptools",
              "doctest", "unittest", "pdb"],
    cipher=block_cipher,
    noarchive=False,
)

# Collect notices and matching sources from the actual native-library inventory.
sys.path.insert(0, str(ROOT / "tools"))
from release_bundle import installed_notices, prepare, record_bundle
release_metadata = (prepare(ROOT, a.binaries) if os.environ.get("MODEMDIAG_RELEASE") == "1"
                    else installed_notices(ROOT, a.binaries))
a.datas.append(("release/NOTICES.txt", str(release_metadata / "NOTICES.txt"), "DATA"))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

version_file = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
    from release_bundle import project_version
    version = project_version(ROOT)
    numbers = [int(part) for part in version.split(".")]
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple((numbers + [0] * 4)[:4]), prodvers=tuple((numbers + [0] * 4)[:4]), fileType=1),
        kids=[StringFileInfo([StringTable("040904B0", [StringStruct(key, value) for key, value in {
            "CompanyName": "Yassine El Harami", "FileDescription": "ModemDiag AT Command Tester",
            "FileVersion": version, "ProductName": "ModemDiag", "ProductVersion": version,
            "OriginalFilename": "ModemDiag.exe", "LegalCopyright": "Copyright 2026 Yassine El Harami",
        }.items()])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])

exe = EXE(
    pyz, a.scripts, *([] if bundle_mode == "onedir" else [a.binaries, a.zipfiles, a.datas]), [],
    name="ModemDiag",
    debug=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=icon_file,
    version=version_file,
    exclude_binaries=bundle_mode == "onedir",
)

executable = Path(DISTPATH) / ("ModemDiag.exe" if sys.platform == "win32" else "ModemDiag")
folder = None
if bundle_mode == "onedir":
    collection = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="ModemDiag-app")
    folder = Path(DISTPATH) / "ModemDiag-app"
    executable = folder / executable.name
record_bundle(release_metadata, DISTPATH, executable, folder)
