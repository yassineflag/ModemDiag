"""Local appearance preference, kept separate from command-library data."""
import json
import os
from pathlib import Path
import tempfile


def load_theme(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        theme = value.get("theme") if isinstance(value, dict) else None
        return theme if theme in ("dark", "light") else "dark"
    except (OSError, ValueError, UnicodeError):
        return "dark"


def save_theme(path: Path, theme: str):
    if theme not in ("dark", "light"):
        raise ValueError("Unknown theme")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".appearance-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"theme": theme}, stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
