"""Source resources live in resources/; personal source-run data in local-ignored/.

Packaged resources use the extracted bundle, while installed applications keep
personal data in the operating system's existing ModemDiag data directory.
"""

import os
import shutil
import sys
from pathlib import Path


# ── Root directories ───────────────────────────────────────────────────────────

def get_project_dir() -> Path:
    return Path(__file__).resolve().parents[3]


def get_data_dir() -> Path:
    """
    Root of the read-only bundled data (commands/ YAML files, assets/).

    Frozen (PyInstaller):  sys._MEIPASS   – temp dir where the bundle is extracted
    Source:                <project>/resources
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return get_project_dir() / "resources"


def _migrate_source_data(project: Path, destination: Path):
    """Move legacy local data without overwriting an existing destination file."""
    files = [project / name for name in ("user_commands.yaml", "quick_send.yaml", "config.yaml",
                                        "appearance.json", "release-policy-cache.json")]
    for directory in (project / "logs", project / "macros"):
        if directory.is_dir() and not directory.is_symlink():
            files.extend(path for path in directory.rglob("*") if path.is_file())
    for source in files:
        target = destination / source.relative_to(project)
        if source.is_file() and not source.is_symlink() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), target)
    for directory in (project / "logs", project / "macros"):
        if directory.is_dir() and not directory.is_symlink():
            for child in sorted(directory.rglob("*"), reverse=True):
                if child.is_dir() and not any(child.iterdir()):
                    child.rmdir()
            if not any(directory.iterdir()):
                directory.rmdir()


def get_user_dir() -> Path:
    """
    Writable directory for user-specific data: favorites, user commands, logs.
    Created automatically on first run.

    Frozen:  OS user-data dir (see table above)
    Source:  <project>/local-ignored/runtime; legacy settings migrate automatically
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", Path.home()))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:  # Linux / BSD
            base = Path(
                os.environ.get("XDG_DATA_HOME",
                               Path.home() / ".local" / "share")
            )
        user_dir = base / "ModemDiag"
    else:
        user_dir = get_project_dir() / "local-ignored" / "runtime"

    user_dir.mkdir(parents=True, exist_ok=True)
    if not getattr(sys, "frozen", False):
        _migrate_source_data(get_project_dir(), user_dir)
    return user_dir


# ── Convenience shortcuts ──────────────────────────────────────────────────────

def commands_dir() -> Path:
    """Bundled commands YAML directory."""
    return get_data_dir() / "commands"


def user_commands_file() -> Path:
    """User-added / user-removed commands (writable)."""
    return get_user_dir() / "user_commands.yaml"


def favorites_file() -> Path:
    """Quick-send favorites list (writable)."""
    return get_user_dir() / "quick_send.yaml"


def logs_dir() -> Path:
    """Session log directory (writable)."""
    d = get_user_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def diagnostics_dir() -> Path:
    """Bundled diagnostic suites (read-only when packaged)."""
    return get_data_dir() / "diagnostics"


def macros_dir() -> Path:
    """
    User automations, seeded with bundled examples without overwriting local files.
    """
    d = get_user_dir() / "macros"
    d.mkdir(parents=True, exist_ok=True)
    bundled = get_data_dir() / "macros"
    if bundled.is_dir() and bundled != d:
        for f in bundled.glob("*.yaml"):
            if not (d / f.name).exists():
                shutil.copy2(f, d / f.name)
    return d


def assets_dir() -> Path:
    """Bundled assets directory (icons, etc.)."""
    return get_data_dir() / "assets"
