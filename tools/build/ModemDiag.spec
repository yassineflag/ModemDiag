# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).parents[1]

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
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "modemdiag.ui", "modemdiag.ui.main_window", "modemdiag.ui.command_panel", "modemdiag.ui.terminal_panel",
    "modemdiag.ui.port_selector", "modemdiag.ui.automation_panel", "modemdiag.ui.styles", "modemdiag.ui.theme",
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
from release_bundle import digest, installed_notices, prepare
release_metadata = (prepare(ROOT, a.binaries) if os.environ.get("MODEMDIAG_RELEASE") == "1"
                    else installed_notices(ROOT, a.binaries))
a.datas.append(("release/NOTICES.txt", str(release_metadata / "NOTICES.txt"), "DATA"))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="ModemDiag",
    debug=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=icon_file,
)

executable = Path(DISTPATH) / ("ModemDiag.exe" if sys.platform == "win32" else "ModemDiag")
(release_metadata / "executable.sha256").write_text(digest(executable) + "\n")
