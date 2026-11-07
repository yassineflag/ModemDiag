#!/usr/bin/env python3
"""
ModemDiag — Open-Source AT Command Tester & Cellular Modem Diagnostic Tool
Copyright (c) 2026 Yassine El Harami — MIT License
"""
from pathlib import Path
import sys

# Keep `python main.py` convenient without installing the source checkout.
SOURCE_DIR = Path(__file__).resolve().parent / "src"
if SOURCE_DIR.is_dir():
    sys.path.insert(0, str(SOURCE_DIR))

from modemdiag.app import main


if __name__ == "__main__":
    main()
