#!/usr/bin/env python3
"""Build separate binary and source ZIPs with matching platform build records.

Source downloads are cached separately from application source. No upload or Git mutation occurs.
The packaging recipe calls prepare() with its actual native-library inventory.
"""
import argparse
import concurrent.futures
import hashlib
from importlib import metadata
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("MODEMDIAG_SOURCE_CACHE", ROOT / "local-ignored" / "cache"))
OUTPUT = ROOT / "local-ignored" / "build" / "release"
PYTHON_PACKAGES = (
    "PyQt6", "PyQt6-sip", "pyserial", "PyYAML", "cryptography", "cffi", "pycparser",
    "typing_extensions", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "packaging",
    "pip", "setuptools", "wheel", "tomli", "pywin32-ctypes", "pefile",
)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def slug(value):
    return re.sub(r"[^A-Za-z0-9_.+-]", "_", value)


def platform_label():
    system = "windows" if sys.platform == "win32" else sys.platform
    machine = platform.machine()
    if system == "windows" and machine.lower() in ("amd64", "x86_64"):
        machine = "x64"
    return slug(f"{system}-{machine}")


def cache_path(url):
    name = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name) or "response.json"
    return CACHE / (hashlib.sha256(url.encode()).hexdigest()[:20] + "-" + slug(name))


def fetch(url, expected=None):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("Source downloads must use HTTPS")
    CACHE.mkdir(parents=True, exist_ok=True)
    target = cache_path(url)
    if target.is_file() and (expected is None or digest(target) == expected):
        return target
    if expected is not None:
        # The same upstream archive may already be cached from another official mirror.
        suffix = slug(urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name))
        for candidate in CACHE.glob("*-" + suffix):
            if candidate.is_file() and digest(candidate) == expected:
                return candidate
    for attempt in range(3):
        temporary = None
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ModemDiag-release-builder"})
            with urllib.request.urlopen(request, timeout=45) as response, \
                    tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as stream:
                temporary = Path(stream.name)
                shutil.copyfileobj(response, stream)
            if expected is not None and digest(temporary) != expected:
                raise ValueError(f"Source checksum mismatch: {url}")
            os.replace(temporary, target)
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if (isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504)) or attempt == 2:
                raise RuntimeError(f"Source download failed: {url}: {error}") from error
            time.sleep(0.5 * (attempt + 1))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return target


def json_url(url):
    return json.loads(fetch(url).read_text(encoding="utf-8"))


def source_record(component, version, url, checksum=None):
    path = fetch(url, checksum)
    filename = slug(urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name))
    return {"component": component, "version": version, "url": url,
            "sha256": digest(path), "file": f"{slug(component)}-{slug(version)}/{filename}"}


def cached_record(record):
    return fetch(record["url"], record["sha256"])


def copy_archive_notices(record, destination):
    """Read regular notice files without extracting upstream paths onto disk."""
    source = cached_record(record)
    if not tarfile.is_tarfile(source):
        return
    with tarfile.open(source) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            name = path.name.lower()
            is_notice = (name.startswith(("license", "copying", "copyright", "notice"))
                         or name == "qt_attribution.json"
                         or ("LICENSES" in path.parts and path.suffix in (".txt", ".md")))
            if not member.isfile() or not is_notice or member.size > 2 * 1024 * 1024:
                continue
            if path.is_absolute() or ".." in path.parts or path.suffix.lower() in (
                    ".c", ".h", ".py", ".cpp", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp"):
                continue
            target = destination / slug(record["component"]) / Path(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)


def pypi_source(name):
    package = metadata.distribution(name)
    info = json_url(f"https://pypi.org/pypi/{name}/{package.version}/json")
    source = next((item for item in info["urls"] if item["packagetype"] == "sdist"), None)
    if source is None:
        raise ValueError(f"No matching source distribution for {name} {package.version}")
    return source_record(name, package.version, source["url"], source["digests"]["sha256"])


def libyaml_source(binaries):
    """Account for LibYAML inside the PyYAML extension, including static wheels."""
    extensions = [Path(source).resolve() for name, source, kind in binaries
                  if kind == "EXTENSION" and name.replace("\\", "/").startswith("yaml/_yaml")]
    if not extensions:
        return None
    from yaml import _yaml
    if Path(_yaml.__file__).resolve() not in extensions:
        raise ValueError("Bundled PyYAML extension differs from the version being inspected")
    version = _yaml.get_version_string()
    # Unknown versions need an upstream source/checksum review rather than a guessed URL.
    checksums = {"0.2.5": "c642ae9b75fee120b2d96c712538bd2cf283228d2337df2cf2988e3c02678ef4"}
    if version not in checksums:
        raise ValueError(f"Add a verified LibYAML source record for version {version}")
    return source_record("libyaml", version,
                         f"https://pyyaml.org/download/libyaml/yaml-{version}.tar.gz", checksums[version])


def windows_native_sources(binaries, output, records, review_path=None):
    """Require explicit evidence for every bundled DLL/PYD, including wheel contents.

    The review input groups file hashes by component, with a redistributable notice
    and source records or a documented source exception. It is not legal approval.
    """
    output = Path(output)
    inventory = [{"file": name.replace("\\", "/"), "sha256": digest(source)}
                 for name, source, kind in binaries if kind in ("BINARY", "EXTENSION")]
    inventory.sort(key=lambda item: item["file"])
    (output / "windows-native-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    if review_path is None:
        raise ValueError("Windows native dependencies need review. See "
                         f"{output / 'windows-native-inventory.json'} and README; "
                         "use build.bat to load the included review, or set MODEMDIAG_WINDOWS_REVIEW")
    review_path = Path(review_path)
    components = json.loads(review_path.read_text())["components"]
    actual = {item["file"]: item["sha256"] for item in inventory}
    covered, native, extra = set(), [], []
    known = {(item["component"], item["version"]) for item in records}
    for component in components:
        name, version = component["component"], component["version"]
        files = component["files"]
        if not name.strip() or not version.strip() or not files or not component["redistribution_basis"].strip():
            raise ValueError("Windows review requires component, version, files and redistribution_basis")
        for filename, checksum in files.items():
            if filename in covered or actual.get(filename) != checksum:
                raise ValueError(f"Stale, duplicate or unknown Windows native file: {filename}")
            covered.add(filename)
        sources = component.get("sources", [])
        exception = component.get("source_exception", "").strip()
        if bool(sources) == bool(exception):
            raise ValueError(f"Specify sources or a documented source_exception for {name}")
        for source in sources:
            if "url" in source:
                if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
                    raise ValueError(f"Invalid Windows source checksum: {name}")
                extra.append(source_record(source["component"], source["version"],
                                           source["url"], source["sha256"]))
            elif (source["component"], source["version"]) not in known:
                raise ValueError(f"Windows source has no matching release record: {source}")
        notice = Path(component["notice_file"])
        if not notice.is_absolute():
            notice = review_path.parent / notice
        if not notice.is_file() or not notice.read_bytes().strip():
            raise ValueError(f"Missing Windows redistribution notice: {name}")
        target = output / "licenses" / "windows" / (slug(name) + "-" + slug(version) + ".txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != notice.read_bytes():
            raise ValueError(f"Conflicting Windows redistribution notices: {name}")
        shutil.copyfile(notice, target)
        native.append({"component": name, "version": version, "files": files,
                       "sources": sources, "source_exception": exception,
                       "redistribution_basis": component["redistribution_basis"]})
    if covered != actual.keys():
        raise ValueError("Unreviewed Windows native files: " + ", ".join(sorted(actual.keys() - covered)))
    return extra, native


def qt_source(module, version):
    series = ".".join(version.split(".")[:2])
    url = f"https://download.qt.io/archive/qt/{series}/{version}/submodules/{module}-everywhere-src-{version}.tar.xz"
    checksum = fetch(url + ".sha256").read_text().split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError(f"Invalid Qt checksum: {module}")
    return source_record(module, version, url, checksum)


def debian_owners(binaries):
    aliases = {}
    for _, filename, kind in binaries:
        if kind not in ("BINARY", "EXTENSION") or "/site-packages/" in filename:
            continue
        options = {filename, str(Path(filename).resolve())}
        for path in list(options):
            if path.startswith("/usr/lib/"):
                options.add(path.removeprefix("/usr"))
            elif path.startswith("/lib/"):
                options.add("/usr" + path)
        aliases[filename] = options
    if not aliases:
        return set()
    # Load the package database once, instead of once per native library.
    result = subprocess.run(["dpkg-query", "-S", *sorted(set().union(*aliases.values()))],
                            capture_output=True, text=True)
    owners = {}
    for line in result.stdout.splitlines():
        if ": " in line:
            package, path = line.rsplit(": ", 1)
            if path.startswith("/") and " " not in package:
                owners[path] = package.split(",")[0]
    packages = set()
    for filename, options in aliases.items():
        owner = next((owners[path] for path in sorted(options) if path in owners), None)
        if owner is None:
            raise ValueError(f"Native library has no recorded package owner: {Path(filename).name}")
        packages.add(owner)
    return packages


def ubuntu_source(name, version):
    base = "https://api.launchpad.net/1.0/ubuntu/+archive/primary?"
    parameters = {"ws.op": "getPublishedSources", "source_name": name,
                  "version": version, "exact_match": "true"}
    entries = []
    for status in (None, "Superseded", "Deleted"):
        query = {**parameters, **({"status": status} if status else {})}
        entries = json_url(base + urllib.parse.urlencode(query)).get("entries", [])
        if entries:
            break
    if not entries:
        raise ValueError(f"Matching Ubuntu source not found: {name} {version}")
    urls = json_url(entries[0]["self_link"] + "?ws.op=sourceFileUrls")
    def record(url, checksum=None):
        if cache_path(url).is_file():
            return source_record(name, version, url, checksum)
        # Use Ubuntu's faster pool as a transport, but publish Launchpad's retained
        # location: cached pool URLs can disappear after a source revision ages out.
        filename = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name)
        prefix = name[:4] if name.startswith("lib") else name[0]
        component = entries[0].get("component_name", "main")
        mirror = f"https://archive.ubuntu.com/ubuntu/pool/{component}/{prefix}/{name}/{filename}"
        try:
            result = source_record(name, version, mirror, checksum)
            result["url"] = url
            return result
        except RuntimeError:
            return source_record(name, version, url, checksum)
    descriptor = next(url for url in urls if url.endswith(".dsc"))
    records = [record(descriptor)]
    content = cached_record(records[0]).read_text()
    checksums = {filename: checksum for checksum, _, filename in
                 re.findall(r"(?m)^ ([0-9a-f]{64})\s+(\d+)\s+(\S+)\s*$", content)}
    for url in urls:
        if url == descriptor:
            continue
        filename = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name)
        if filename not in checksums:
            raise ValueError(f"Missing source checksum: {name}/{filename}")
        records.append(record(url, checksums[filename]))
    return records


def rust_sources(cryptography_record):
    # Cargo.lock records exact crate versions and source-archive checksums.
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with tarfile.open(cached_record(cryptography_record)) as archive:
        lock = next(member for member in archive if member.name.endswith("/Cargo.lock"))
        content = tomllib.loads(archive.extractfile(lock).read().decode())
    jobs = []
    for item in content["package"]:
        if item.get("source", "").startswith("registry+"):
            name, version = item["name"], item["version"]
            url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
            jobs.append((name, version, url, item["checksum"]))
        elif "source" in item:
            raise ValueError("An unhandled Rust source provider needs an explicit source record")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(lambda args: source_record(*args), jobs))


def project_version(root):
    namespace = {}
    exec((Path(root) / "src/modemdiag/__init__.py").read_text(), namespace)
    return namespace["__version__"]


def compact_notices(root, output, manifest=None):
    """Keep every attribution, sharing one copy of byte-identical license texts."""
    root, output = Path(root), Path(output)
    notices = output / "licenses"
    own_notice = notices / "ModemDiag" / "LICENSE"
    own_notice.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "LICENSE", own_notice)
    documents = {}
    for path in sorted(notices.rglob("*")):
        if path.is_file():
            content = path.read_bytes()
            key = hashlib.sha256(content).hexdigest()
            document = documents.setdefault(key, {"content": content, "paths": []})
            document["paths"].append(path.relative_to(notices).as_posix())
    snapshot = {path.relative_to(root).as_posix(): digest(path) for path in project_files(root)}
    (output / "project-files.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    snapshot_hash = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    version = project_version(root)
    destination = output / "NOTICES.txt"
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            f"ModemDiag {version} - licenses and source code\n"
            "=============================================\n\n"
            "ModemDiag's own source retains its MIT license.\n"
            "The combined executable using open-source PyQt6 is distributed under GPLv3 terms.\n"
            "Other components retain their applicable licenses and copyright notices below.\n"
            "You may modify and redistribute under those terms. No warranty is provided.\n\n"
            "Project and releases: https://github.com/yassineflag/ModemDiag\n"
            "License/source guide: https://github.com/yassineflag/ModemDiag#license\n"
            f"Project snapshot SHA-256: {snapshot_hash}\n\n"
            "SOURCE CODE\n===========\n"
        )
        if manifest is not None:
            stream.write(
                f"Download ModemDiag-{version}-source.zip separately from the same release page:\n"
                f"  https://github.com/yassineflag/ModemDiag/releases/tag/v{version}\n"
                "It contains this application's matching source, build scripts, pinned dependency\n"
                "versions and sources.json. Extract it, then read modemdiag/README.md.\n"
                "Exact, unmodified dependency sources are available without charge at the URLs\n"
                "below. Ubuntu source records include upstream archives and distribution patches.\n"
                "To download and verify them, from the extracted modemdiag directory run:\n"
                "  python3 tools/release_bundle.py fetch-sources\n"
                "Then unpack Ubuntu packages using: dpkg-source -x <package>.dsc\n"
                "Each upstream archive contains its own build instructions.\n"
                "A distributor must keep these exact sources available while distributing the binary;\n"
                "the builder keeps downloaded archives in local-ignored/cache/ as a local backup.\n\n"
            )
            for record in manifest["sources"]:
                stream.write(f"{record['component']} {record['version']} - {record['file']}\n"
                             f"  {record['url']}\n  SHA-256: {record['sha256']}\n\n")
        else:
            stream.write("This is a local development build. Use build.sh --release or build.bat\n"
                         "--release to generate a distribution ZIP with matching source information.\n\n")
        stream.write("LICENSE TEXTS AND COPYRIGHT NOTICES\n==================================\n"
                     "Byte-identical texts are included once, with all associated component paths.\n"
                     "References to original license filenames refer to the sections below.\n\n")
        for number, document in enumerate(documents.values(), 1):
            stream.write(f"--- Document {number} ---\nApplies to:\n")
            for path in document["paths"]:
                stream.write(f"  {path}\n")
            try:
                content = document["content"].decode("utf-8")
            except UnicodeDecodeError:
                content = document["content"].decode("latin-1")
            stream.write("\n" + content + "\n\n")
    shutil.rmtree(notices)
    return destination


def release_manifest(root):
    root = Path(root)
    manifest_path = root / "sources.json"
    if not manifest_path.is_file():
        manifest_path = root / "local-ignored/build/release/sources.json"
    return json.loads(manifest_path.read_text())


def verify_sources(root=ROOT, check_links=False):
    """Verify cached source bytes and, optionally, current upstream reachability."""
    def verify(record):
        result = {"file": record["file"], "url": record["url"]}
        try:
            if digest(cached_record(record)) != record["sha256"]:
                raise ValueError("Source checksum mismatch")
            if check_links:
                request = urllib.request.Request(record["url"], headers={
                    "User-Agent": "ModemDiag-release-builder", "Range": "bytes=0-0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    if urllib.parse.urlsplit(response.geturl()).scheme != "https":
                        raise ValueError("Source URL redirected away from HTTPS")
                    if not response.read(1):
                        raise ValueError("Empty source response")
            result["ok"] = True
        except (OSError, ValueError, RuntimeError) as error:
            result.update(ok=False, error=str(error))
        return result
    records = release_manifest(root)["sources"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(verify, records))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "checked_links": check_links,
              "results": results}
    path = Path(root) / "local-ignored/reports/source-verification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    failed = sum(not item["ok"] for item in results)
    print(f"Source verification: {len(results) - failed}/{len(results)} passed; report: {path}")
    return failed == 0


def download_sources(root=ROOT, destination=None):
    """Fetch the exact public source archives named in a release source manifest."""
    root = Path(root)
    manifest = release_manifest(root)
    destination = Path(destination or root / "local-ignored" / "third-party-source").resolve()
    for record in manifest["sources"]:
        relative = PurePosixPath(record["file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid source archive path")
        target = (destination / Path(*relative.parts)).resolve()
        if not target.is_relative_to(destination):
            raise ValueError("Source archive path escapes its destination")
        if target.is_file() and digest(target) == record["sha256"]:
            continue
        cached = cached_record(record)
        if digest(cached) != record["sha256"]:
            raise ValueError(f"Source checksum mismatch: {record['file']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, target)
    print(f"Verified {len(manifest['sources'])} source archives in {destination}")


def installed_notices(root, binaries):
    """Collect locally installed notices without any network requests."""
    output = Path(root) / "local-ignored" / "build" / "notices"
    if output.exists():
        shutil.rmtree(output)
    notices = output / "licenses"
    notices.mkdir(parents=True)
    for name in (*PYTHON_PACKAGES, "PyQt6-Qt6"):
        try:
            package = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        for item in package.files or []:
            path = Path(str(item))
            if ".dist-info" not in str(item) or not path.name.lower().startswith(("license", "copying", "notice")):
                continue
            source = package.locate_file(item)
            if source.is_file():
                target = notices / slug(name) / path.name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source, target)
    if sys.platform.startswith("linux") and shutil.which("dpkg-query"):
        packages = debian_owners(binaries)
        for name in packages:
            source = Path("/usr/share/doc") / name.split(":")[0] / "copyright"
            if source.is_file():
                target = notices / "system" / (slug(name) + ".txt")
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source, target)
        shutil.copytree("/usr/share/common-licenses", notices / "common", dirs_exist_ok=True)
    compact_notices(root, output)
    return output


def prepare(root, binaries):
    global ROOT, OUTPUT
    ROOT = Path(root)
    OUTPUT = ROOT / "local-ignored" / "build" / "release"
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    notices = OUTPUT / "licenses"
    notices.mkdir(exist_ok=True)
    if sys.platform == "win32" and not os.environ.get("MODEMDIAG_WINDOWS_REVIEW"):
        windows_native_sources(binaries, OUTPUT, [])
    elif not sys.platform.startswith("linux") and sys.platform != "win32":
        raise ValueError("Release native-library accounting supports Linux and Windows only")
    print("Collecting exact dependency sources and notices...", flush=True)
    packages = []
    for name in PYTHON_PACKAGES:
        try:
            packages.append(metadata.distribution(name).metadata["Name"])
        except metadata.PackageNotFoundError:
            pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(pypi_source, packages))
    libyaml = libyaml_source(binaries)
    if libyaml is not None:
        records.append(libyaml)
    cryptography_record = next(record for record in records if record["component"].lower() == "cryptography")
    records += rust_sources(cryptography_record)
    qt_version = metadata.version("PyQt6-Qt6")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        records += list(pool.map(lambda name: qt_source(name, qt_version),
                                 ("qtbase", "qtwayland", "qtsvg", "qtimageformats", "qttranslations")))
    # ICU is provided as a separate binary by the Linux Qt wheel.
    icu_files = [source for _, source, kind in binaries if kind == "BINARY" and "/site-packages/" in source
                 and Path(source).name.startswith("libicuuc.so.")]
    if icu_files:
        import ctypes
        major = Path(icu_files[0]).name.rsplit(".", 1)[-1]
        library = ctypes.CDLL(icu_files[0])
        values = (ctypes.c_uint8 * 4)()
        getattr(library, "u_getVersion_" + major)(values)
        version = f"{values[0]}.{values[1]}"
        tag = version.replace(".", "-")
        records.append(source_record("icu", version,
            f"https://github.com/unicode-org/icu/releases/download/release-{tag}/icu4c-{tag.replace('-', '_')}-src.tgz"))
    # Preserve the cryptography wheel's exact statically linked OpenSSL record.
    crypto = metadata.distribution("cryptography")
    for item in crypto.files or []:
        if str(item).endswith("/sboms/sbom.json"):
            sbom = json.loads(crypto.locate_file(item).read_text())
            shutil.copy2(crypto.locate_file(item), OUTPUT / "cryptography-sbom.json")
            for component in sbom.get("components", []):
                reference = next(ref for ref in component["externalReferences"] if ref["type"] == "distribution")
                checksum = next(value["content"] for value in component["hashes"] if value["alg"] == "SHA-256")
                records.append(source_record(component["name"], component["version"], reference["url"], checksum))
    native_packages = debian_owners(binaries) if sys.platform.startswith("linux") else set()
    native_manifest = []
    sources = set()
    for name in sorted(native_packages):
        value = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}\t${source:Package}\t${source:Version}", name], text=True).split("\t")
        version, source_name, source_version = value
        sources.add((source_name, source_version))
        copyright_file = Path("/usr/share/doc") / name.split(":")[0] / "copyright"
        if not copyright_file.is_file():
            raise ValueError(f"Missing native copyright notice: {name}")
        target = notices / "system" / (slug(name) + ".txt")
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(copyright_file, target)
        native_manifest.append({"package": name, "version": version, "source": source_name, "source_version": source_version})
    if sources:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(lambda pair: ubuntu_source(*pair), sorted(sources)):
                records += result
    if not any(record["component"].startswith("python3.") for record in records):
        version = platform.python_version()
        records.append(source_record("CPython", version,
            f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz"))
    windows_native = []
    if sys.platform == "win32":
        extra, windows_native = windows_native_sources(
            binaries, OUTPUT, records, os.environ["MODEMDIAG_WINDOWS_REVIEW"])
        records += extra
    common = Path("/usr/share/common-licenses")
    if common.is_dir():
        shutil.copytree(common, notices / "common", dirs_exist_ok=True)
    for record in records:
        if (record["component"], record["version"]) not in sources:
            copy_archive_notices(record, notices / "upstream")
    installed = sorted((package.metadata["Name"], package.version) for package in metadata.distributions())
    (OUTPUT / "requirements-release.txt").write_text("".join(f"{name}=={version}\n" for name, version in installed))
    # Exact hashes make subsequent packaging independent of upstream changes.
    records = sorted({record["file"]: record for record in records}.values(), key=lambda record: record["file"])
    manifest = {"platform": platform_label(), "sources": records, "native_packages": native_manifest}
    if windows_native:
        manifest["windows_native"] = windows_native
    (OUTPUT / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n")
    compact_notices(ROOT, OUTPUT, manifest)
    print(f"Prepared one consolidated notice and {len(records)} verified source links.", flush=True)
    return OUTPUT


def project_files(root):
    """Explicit source allowlist: local settings, drafts and captures stay out."""
    root = Path(root)
    for name in ("main.py", "README.md", "LICENSE", ".gitignore", "requirements.txt", "build.sh", "build.bat",
                 "release-policy.json", ".github/CONTRIBUTING.md", "docs/THIRD_PARTY_NOTICES.md"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            yield path
    for folder, suffixes in (("src", {".py"}), ("tests", {".py"}), ("tools", {".py", ".spec"}),
                             ("resources/commands", {".yaml"}), ("resources/diagnostics", {".yaml"}),
                             ("resources/assets", {".png", ".ico"})):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in suffixes and "__pycache__" not in path.parts:
                yield path
    for path in sorted((root / "resources" / "macros").glob("example_*.yaml")):
        if path.is_file() and not path.is_symlink():
            yield path
    for name in ("fibocom.png", "fibocom-light.png", "automation.png"):
        path = root / "docs" / name
        if path.is_file():
            yield path
    # Public, pinned Windows bootstrap inputs belong in every source download,
    # including one produced on Linux before Windows metadata is available.
    for name in ("requirements-release.txt", "windows-release-review.json", "windows-notices.txt"):
        path = root / "tools" / "build" / "windows-x64" / name
        if path.is_file() and not path.is_symlink():
            yield path


def clean_release_files(root=ROOT, keep=None):
    """Remove obsolete generated archives for this platform, keeping the executable."""
    suffix = "(?:" + "|".join(re.escape(value) for value in
                               (f"-{sys.platform}-{platform.machine()}", "-" + platform_label())) + ")"
    pattern = re.compile(r"ModemDiag-[\w.+-]+" + suffix + r"(?:\.zip|-source\.tar\.gz|-SHA256SUMS\.txt)")
    for path in (Path(root) / "local-ignored" / "dist").glob("ModemDiag-*"):
        if path.is_file() and not path.is_symlink() and path != keep and pattern.fullmatch(path.name):
            path.unlink()


SOURCE_METADATA = ("sources.json", "requirements-release.txt", "project-files.json",
                   "cryptography-sbom.json", "NOTICES.txt", "executable.sha256")


def write_source_zip(root, metadata_dirs, destination):
    """Share application files once, keeping dependency/build records per platform."""
    root = Path(root)
    snapshot = {path.relative_to(root).as_posix(): digest(path) for path in project_files(root)}
    builds = {}
    sources = []
    for directory in metadata_dirs:
        directory = Path(directory)
        if json.loads((directory / "project-files.json").read_text()) != snapshot:
            raise ValueError("Project files changed since the build; cannot combine different source snapshots")
        manifest = json.loads((directory / "sources.json").read_text())
        target = manifest["platform"]
        if not re.fullmatch(r"(?:linux|windows)-[A-Za-z0-9_+-]+", target):
            raise ValueError(f"Invalid release platform: {target}")
        if target in builds:
            raise ValueError(f"Duplicate platform metadata: {target}")
        for name in ("requirements-release.txt", "NOTICES.txt", "executable.sha256"):
            if not (directory / name).is_file():
                raise FileNotFoundError(directory / name)
        builds[target] = (directory, manifest)
        for record in manifest["sources"]:
            relative = PurePosixPath(record["file"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid source archive path")
            sources.append({**record, "file": target + "/" + relative.as_posix()})
    if not builds:
        raise ValueError("At least one platform's release metadata is required")
    existing = root / "local-ignored/dist" / f"ModemDiag-{project_version(root)}-source.zip"
    if existing.is_file():
        with zipfile.ZipFile(existing) as archive:
            previous = json.loads(archive.read("modemdiag/sources.json"))
        if set(previous["platforms"]) - builds.keys():
            raise ValueError("Existing source ZIP includes other platforms; provide all their metadata to preserve them")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in project_files(root):
            archive.write(path, "modemdiag/" + path.relative_to(root).as_posix())
        archive.writestr("modemdiag/project-files.json", json.dumps(snapshot, indent=2) + "\n")
        archive.writestr("modemdiag/sources.json", json.dumps(
            {"platforms": sorted(builds), "sources": sources}, indent=2) + "\n")
        for target, (directory, manifest) in sorted(builds.items()):
            prefix = f"modemdiag/release/{target}/"
            for name in SOURCE_METADATA:
                path = directory / name
                if path.is_file():
                    archive.write(path, prefix + name)
            if manifest.get("windows_native"):
                components = [{**component, "notice_file": "NOTICES.txt"}
                              for component in manifest["windows_native"]]
                archive.writestr(prefix + "windows-release-review.json",
                                 json.dumps({"components": components}, indent=2) + "\n")
    return sorted(builds)


def package_sources(root=ROOT, metadata_dirs=None):
    root = Path(root)
    destination = root / "local-ignored/dist" / f"ModemDiag-{project_version(root)}-source.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.tmp")
    try:
        targets = write_source_zip(root, metadata_dirs or [root / "local-ignored/build/release"], temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Source: {destination}\nPlatforms: {', '.join(targets)}\nSHA-256: {digest(destination)}", flush=True)


def package(root=ROOT):
    root = Path(root)
    output = root / "local-ignored" / "build" / "release"
    snapshot = json.loads((output / "project-files.json").read_text())
    actual = {path.relative_to(root).as_posix(): digest(path) for path in project_files(root)}
    if snapshot != actual:
        raise ValueError("Project files changed since the build; rebuild before packaging")
    version = project_version(root)
    label = f"ModemDiag-{version}-{platform_label()}"
    executable = root / "local-ignored" / "dist" / ("ModemDiag.exe" if sys.platform == "win32" else "ModemDiag")
    if not executable.is_file():
        raise ValueError("Build the executable before packaging")
    if not (output / "sources.json").is_file():
        raise ValueError("Build with --release to record matching dependency sources")
    if (output / "executable.sha256").read_text().strip() != digest(executable):
        raise ValueError("Executable changed since the release build; rebuild with --release")
    if json.loads((output / "sources.json").read_text())["platform"] != platform_label():
        raise ValueError("Release metadata belongs to a different platform")
    source_archive = root / "local-ignored/dist" / f"ModemDiag-{version}-source.zip"
    source_temporary = source_archive.with_suffix(".zip.tmp")
    release = root / "local-ignored" / "dist" / f"{label}.zip"
    temporary = release.with_suffix(".zip.tmp")
    try:
        write_source_zip(root, [output], source_temporary)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(executable, executable.name)
            archive.write(output / "NOTICES.txt", "NOTICES.txt")
            archive.comment = (f"SHA-256 {executable.name}: {digest(executable)}\n"
                               f"SHA-256 NOTICES.txt: {digest(output / 'NOTICES.txt')}\n").encode()
        source_temporary.replace(source_archive)
        temporary.replace(release)
    finally:
        temporary.unlink(missing_ok=True)
        source_temporary.unlink(missing_ok=True)
    clean_release_files(root, keep=release)
    print(f"Binary: {release}\nContains: executable and NOTICES.txt.\n"
          f"Source: {source_archive}\nPublish both together; combine platform metadata before a multi-platform release.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("package", "source", "fetch-sources", "verify-sources", "clean"))
    parser.add_argument("--metadata", action="append", type=Path,
                        help="Release metadata directory for source ZIP; repeat for each platform")
    parser.add_argument("--output", type=Path, help="Destination for fetch-sources")
    parser.add_argument("--check-links", action="store_true", help="Check current upstream source URLs too")
    arguments = parser.parse_args()
    if arguments.action == "package":
        package()
    elif arguments.action == "source":
        package_sources(metadata_dirs=arguments.metadata)
    elif arguments.action == "fetch-sources":
        download_sources(destination=arguments.output)
    elif arguments.action == "verify-sources":
        raise SystemExit(0 if verify_sources(check_links=arguments.check_links) else 1)
    else:
        clean_release_files()
