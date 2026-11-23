"""Portable, versioned configuration archives; no logs or release-policy state."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

import yaml

from modemdiag import __version__

SETTINGS = {"user_commands.yaml", "quick_send.yaml", "appearance.json", "ai-settings.json"}
MAX_FILE = 5 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
MAX_FILES = 1000


def _allowed(name):
    if name in SETTINGS:
        return True
    parts = PurePosixPath(name).parts
    return (len(parts) == 2 and parts[0] == "macros" and name == "/".join(parts)
            and parts[1].endswith(".yaml") and not parts[1].startswith(".")
            and not re.search(r'[\\:<>"|?*\x00-\x1f]', parts[1])
            and not re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])\..*", parts[1], re.I))


def _plain(value, depth=0, budget=None):
    # Bound YAML alias expansion and reject recursive/non-portable structures.
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if depth > 20 or budget[0] < 0:
        raise ValueError("Configuration structure is too large or deeply nested.")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Configuration keys must be text.")
        for item in value.values():
            _plain(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _plain(item, depth + 1, budget)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("Configuration contains an unsupported value.")


def _strings(data, keys):
    for key in keys:
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"'{key}' must be text.")


def _entries(value, command_required=True):
    if not isinstance(value, list):
        raise ValueError("Commands and steps must be lists.")
    for entry in value:
        if not isinstance(entry, dict) or (command_required and not isinstance(entry.get("command"), str)):
            raise ValueError("Each command or step must have a text command.")
        _strings(entry, ("command", "name", "label", "description", "function", "manufacturer", "model",
                         "get", "caps", "expect", "expect_regex", "wait_for", "fail_on", "prompt",
                         "payload", "payload_encoding", "payload_ending", "final_ok", "on_failure"))
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise ValueError("Step enabled must be true or false.")
        for key, minimum in (("timeout_ms", 1), ("delay_ms", 0)):
            if key in entry and (type(entry[key]) is not int or not minimum <= entry[key] <= 2147483647):
                raise ValueError(f"Invalid {key}.")
        if "params" in entry:
            if not isinstance(entry["params"], list):
                raise ValueError("Command parameters must be a list.")
            for parameter in entry["params"]:
                if not isinstance(parameter, dict):
                    raise ValueError("Invalid command parameter.")
                _strings(parameter, ("id", "name", "label", "type", "hint"))
                if "options" in parameter and not isinstance(parameter["options"], list):
                    raise ValueError("Parameter options must be a list.")


def _validate(name, raw):
    try:
        value = (json.loads(raw.decode("utf-8")) if name.endswith(".json")
                 else yaml.safe_load(raw.decode("utf-8")))
        _plain(value)
        if name == "quick_send.yaml":
            _entries(value)
            return
        if not isinstance(value, dict):
            raise ValueError("Expected a configuration object.")
        if name == "user_commands.yaml":
            for key in ("added", "removed"):
                _entries(value.get(key, []))
            for key in ("vendors", "user_categories", "sidebar_order", "hidden_categories"):
                if key in value and (not isinstance(value[key], list)
                                     or not all(isinstance(v, str) for v in value[key])):
                    raise ValueError(f"Invalid {key}.")
            for key in ("sidebar_renames", "order"):
                if key in value:
                    if not isinstance(value[key], dict):
                        raise ValueError(f"Invalid {key}.")
                    for entry in value[key].values():
                        if key == "order":
                            if not isinstance(entry, list) or not all(isinstance(v, str) for v in entry):
                                raise ValueError("Invalid command order.")
                        elif not isinstance(entry, str):
                            raise ValueError("Invalid category name.")
        elif name.startswith("macros/"):
            _strings(value, ("name", "description", "icon", "manufacturer"))
            if not isinstance(value.get("name"), str):
                raise ValueError("Automation needs a name.")
            _entries(value.get("steps", []))
        elif name == "appearance.json":
            if value.get("theme") not in ("dark", "light"):
                raise ValueError("Unknown theme.")
        elif name == "ai-settings.json":
            if set(value) - {"provider", "model", "base_url"}:
                raise ValueError("AI settings may contain only provider, model and base_url.")
            _strings(value, value.keys())
    except (ValueError, TypeError, RecursionError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid {name}: {exc}") from exc


def read_archive(path):
    """Read and validate every member before allowing any configuration writes."""
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if (len(members) > MAX_FILES + 1 or len(set(n.casefold() for n in names)) != len(names)
                    or "manifest.json" not in names):
                raise ValueError("Invalid configuration archive contents.")
            if any(m.file_size > MAX_FILE for m in members) or sum(m.file_size for m in members) > MAX_TOTAL:
                raise ValueError("Configuration archive is too large.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if (not isinstance(manifest, dict) or manifest.get("format") != "modemdiag-configuration"
                    or manifest.get("version") != 1):
                raise ValueError("Unsupported configuration archive format or version.")
            files = {}
            for member in members:
                if member.filename == "manifest.json":
                    continue
                if not _allowed(member.filename) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"Unexpected archive entry: {member.filename}")
                raw = archive.read(member)
                _validate(member.filename, raw)
                files[member.filename] = raw
            return files
    except (BadZipFile, UnicodeError, RuntimeError, NotImplementedError, RecursionError) as exc:
        raise ValueError(f"Cannot read configuration archive: {exc}") from exc


def export_configuration(root, destination):
    """Atomically create a portable archive from saved settings only."""
    root, destination = Path(root).resolve(), Path(destination)
    files = {name: root / name for name in SETTINGS if (root / name).exists()}
    macros = root / "macros"
    if macros.is_symlink():
        raise ValueError("The automations folder must not be a symbolic link.")
    if macros.is_dir():
        files.update({"macros/" + p.name: p for p in macros.glob("*.yaml")})
    contents = {}
    for name, path in files.items():
        if not _allowed(name) or path.is_symlink() or not path.is_file():
            raise ValueError(f"Cannot export {name}.")
        if path.stat().st_size > MAX_FILE:
            raise ValueError(f"Configuration file is too large: {name}")
        raw = path.read_bytes()
        if name == "ai-settings.json":
            # Explicitly exclude credentials even if the file was edited manually.
            settings = json.loads(raw.decode("utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("Invalid AI settings.")
            raw = json.dumps({k: v for k, v in settings.items()
                              if k in ("provider", "model", "base_url")}).encode("utf-8")
        _validate(name, raw)
        contents[name] = raw
    if len(contents) > MAX_FILES or sum(map(len, contents.values())) > MAX_TOTAL:
        raise ValueError("Configuration is too large.")
    if len({name.casefold() for name in contents}) != len(contents):
        raise ValueError("Duplicate filenames cannot be transferred to all operating systems.")
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".zip", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({
                "format": "modemdiag-configuration", "version": 1,
                "app_version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
            }))
            for name, raw in sorted(contents.items()):
                archive.writestr(name, raw)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def import_configuration(root, files):
    """Replace included files, retain other files, and back up the previous settings."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        if not _allowed(name):
            raise ValueError(f"Unexpected configuration file: {name}")
        _validate(name, raw)
        target = root / name
        if target.is_symlink() or target.parent.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Cannot replace {name}.")
    backups = root / "backups"
    if backups.is_symlink():
        raise ValueError("The backups folder must not be a symbolic link.")
    backups.mkdir(exist_ok=True)
    backup = backups / datetime.now().strftime("before_import_%Y%m%d_%H%M%S_%f.zip")
    export_configuration(root, backup)
    with tempfile.TemporaryDirectory(prefix=".config-import-", dir=root) as temporary:
        staged = Path(temporary)
        originals = {name: (root / name).read_bytes() if (root / name).exists() else None for name in files}
        for name, raw in files.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        installed = []
        try:
            for name in files:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged / name, target)
                installed.append(name)
        except OSError:
            for name in reversed(installed):
                target = root / name
                if originals[name] is None:
                    target.unlink()
                else:
                    restored = staged / name
                    restored.write_bytes(originals[name])
                    os.replace(restored, target)
            raise
    return backup
