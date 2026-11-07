import io
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import release_bundle as bundle


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.exe_name = "ModemDiag.exe" if sys.platform == "win32" else "ModemDiag"
        files = {
            "LICENSE": "MIT fixture", "README.md": "Example", "main.py": "# launcher",
            "src/modemdiag/__init__.py": '__version__ = "1.0.1"',
            "src/modemdiag/app.py": "# application",
            "resources/macros/example_lte_check.yaml": "steps: []",
            "local-ignored/build/release/licenses/common/GPL-3": "GPL fixture",
            f"local-ignored/dist/{self.exe_name}": "executable fixture",
        }
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.metadata = self.root / "local-ignored/build/release"
        self.upstream = self.root / "upstream.tar.gz"
        with tarfile.open(self.upstream, "w:gz") as archive:
            payload = b"# third-party source\n"
            item = tarfile.TarInfo("example-1/source.c")
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
        self.record = {"component": "example", "version": "1", "url": "https://example.invalid/source.tar.gz",
                       "sha256": bundle.digest(self.upstream), "file": "example-1/source.tar.gz"}
        (self.metadata / "sources.json").write_text(json.dumps({"platform": bundle.platform_label(), "sources": [self.record]}))
        (self.metadata / "requirements-release.txt").write_text("example==1\n")
        bundle.compact_notices(self.root, self.metadata, {"sources": [self.record]})
        (self.metadata / "executable.sha256").write_text(bundle.digest(self.root / "local-ignored/dist" / self.exe_name))
        self.snapshot()

    def snapshot(self):
        snapshot = {path.relative_to(self.root).as_posix(): bundle.digest(path)
                    for path in bundle.project_files(self.root)}
        (self.metadata / "project-files.json").write_text(json.dumps(snapshot))

    def test_release_contains_matching_source_notices_and_valid_checksums(self):
        with patch.object(bundle, "cached_record", side_effect=AssertionError("No dependency downloads while packaging")):
            bundle.package(self.root)
        release = self.root / "local-ignored/dist" / f"ModemDiag-1.0.1-{bundle.platform_label()}.zip"
        with zipfile.ZipFile(release) as archive:
            self.assertEqual(set(archive.namelist()), {self.exe_name, "NOTICES.txt"})
            notices = archive.read("NOTICES.txt").decode()
            self.assertIn("MIT fixture", notices)
            self.assertIn("GPL fixture", notices)
            self.assertIn(self.record["url"], notices)
            self.assertIn(self.record["sha256"], notices)
            self.assertIn("fetch-sources", notices)
            for line in archive.comment.decode().splitlines():
                label, expected = line.split(": ")
                self.assertEqual(hashlib.sha256(archive.read(label.removeprefix("SHA-256 "))).hexdigest(), expected)
        source_path = self.root / "local-ignored/dist/ModemDiag-1.0.1-source.zip"
        with zipfile.ZipFile(source_path) as source:
            self.assertIn("modemdiag/src/modemdiag/app.py", source.namelist())
            manifest = json.loads(source.read("modemdiag/sources.json"))
            self.assertEqual(manifest["platforms"], [bundle.platform_label()])
            self.assertEqual(manifest["sources"], [{**self.record, "file": bundle.platform_label() + "/" + self.record["file"]}])
            original = json.loads(source.read(f"modemdiag/release/{bundle.platform_label()}/sources.json"))
            self.assertEqual(original["sources"], [self.record])
            self.assertFalse(any("third-party-source/" in name for name in source.namelist()))
        self.assertEqual(len(list((self.root / "local-ignored/dist").iterdir())), 3)

    def test_linux_only_source_ships_complete_windows_bootstrap_inputs(self):
        folder = self.root / "tools/build/windows-x64"
        folder.mkdir(parents=True)
        review = {"components": [{"component": "fixture", "notice_file": "windows-notices.txt"}]}
        (folder / "windows-release-review.json").write_text(json.dumps(review))
        (folder / "windows-notices.txt").write_text("Redistributable Windows notices")
        (folder / "requirements-release.txt").write_text("fixture==1.0\n")
        (folder / "private-evidence.txt").write_text("Do not export")
        self.snapshot()
        bundle.package_sources(self.root)
        with zipfile.ZipFile(self.root / "local-ignored/dist/ModemDiag-1.0.1-source.zip") as archive:
            extracted = self.root / "extracted"
            archive.extractall(extracted)
            self.assertFalse(any("private-evidence" in name for name in archive.namelist()))
        bootstrap = extracted / "modemdiag/tools/build/windows-x64"
        shipped = json.loads((bootstrap / "windows-release-review.json").read_text())
        notice = bootstrap / shipped["components"][0]["notice_file"]
        self.assertEqual(notice.read_text(), "Redistributable Windows notices")
        self.assertEqual((bootstrap / "requirements-release.txt").read_text(), "fixture==1.0\n")
        snapshot = json.loads((extracted / "modemdiag/project-files.json").read_text())
        for name in ("windows-release-review.json", "windows-notices.txt", "requirements-release.txt"):
            self.assertEqual(snapshot[f"tools/build/windows-x64/{name}"], bundle.digest(bootstrap / name))

    def other_platform_metadata(self):
        other = self.root / "other-platform"
        shutil.copytree(self.metadata, other)
        target = "windows-x64" if bundle.platform_label() != "windows-x64" else "linux-x86_64"
        manifest = json.loads((other / "sources.json").read_text())
        manifest["platform"] = target
        manifest["sources"] = [{**self.record, "version": "2"}]
        (other / "sources.json").write_text(json.dumps(manifest))
        return other, target

    def test_shared_source_keeps_both_platforms_and_downloads_colliding_source_filenames(self):
        other, target = self.other_platform_metadata()
        (other / "private-review.md").write_text("Do not export")
        bundle.package_sources(self.root, [self.metadata, other])
        source_path = self.root / "local-ignored/dist/ModemDiag-1.0.1-source.zip"
        with zipfile.ZipFile(source_path) as source:
            names = source.namelist()
            self.assertEqual(names.count("modemdiag/src/modemdiag/app.py"), 1)
            self.assertFalse(any("private-review" in name for name in names))
            manifest = json.loads(source.read("modemdiag/sources.json"))
            self.assertEqual(set(manifest["platforms"]), {target, bundle.platform_label()})
            self.assertEqual(len({record["file"] for record in manifest["sources"]}), 2)
            extracted = self.root / "extracted"
            source.extractall(extracted)
        with patch.object(bundle, "cached_record", return_value=self.upstream):
            bundle.download_sources(extracted / "modemdiag")
        for record in manifest["sources"]:
            self.assertEqual(bundle.digest(extracted / "modemdiag/local-ignored/third-party-source" / record["file"]), record["sha256"])

    def test_source_merge_rejects_mismatched_snapshot_and_preserves_previous_archive(self):
        bundle.package_sources(self.root)
        source_path = self.root / "local-ignored/dist/ModemDiag-1.0.1-source.zip"
        before = source_path.read_bytes()
        other, _ = self.other_platform_metadata()
        (other / "project-files.json").write_text('{}')
        with self.assertRaisesRegex(ValueError, "different source snapshots"):
            bundle.package_sources(self.root, [self.metadata, other])
        self.assertEqual(source_path.read_bytes(), before)

    def test_single_platform_packaging_cannot_drop_other_platforms_from_shared_source(self):
        other, _ = self.other_platform_metadata()
        bundle.package_sources(self.root, [self.metadata, other])
        source_path = self.root / "local-ignored/dist/ModemDiag-1.0.1-source.zip"
        before = source_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "provide all their metadata"):
            bundle.package(self.root)
        self.assertEqual(source_path.read_bytes(), before)
        self.assertFalse(list(source_path.parent.glob('*.tmp')))

    def test_shared_source_rejects_duplicate_platforms_and_unsafe_dependency_paths(self):
        with self.assertRaisesRegex(ValueError, "Duplicate platform"):
            bundle.package_sources(self.root, [self.metadata, self.metadata])
        manifest = {"platform": bundle.platform_label(), "sources": [{**self.record, "file": "../private"}]}
        (self.metadata / "sources.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Invalid source archive path"):
            bundle.package_sources(self.root)

    def test_consolidation_preserves_attributions_and_deduplicates_only_identical_text(self):
        first = self.metadata / "licenses/first/LICENSE"
        second = self.metadata / "licenses/second/COPYING"
        for path in (first, second):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Same complete license text\nCopyright Example\n")
        other = self.metadata / "licenses/third/NOTICE"
        other.parent.mkdir(parents=True)
        other.write_text("Different attribution that must stay")
        bundle.compact_notices(self.root, self.metadata)
        text = (self.metadata / "NOTICES.txt").read_text()
        self.assertEqual(text.count("Same complete license text"), 1)
        self.assertIn("first/LICENSE", text)
        self.assertIn("second/COPYING", text)
        self.assertIn("Different attribution that must stay", text)
        self.assertFalse((self.metadata / "licenses").exists())

    def test_source_downloader_checks_hashes_and_reuses_verified_destination(self):
        destination = self.root / "downloaded-sources"
        with patch.object(bundle, "cached_record", return_value=self.upstream) as download:
            bundle.download_sources(self.root, destination)
            target = destination / self.record["file"]
            self.assertEqual(bundle.digest(target), self.record["sha256"])
            bundle.download_sources(self.root, destination)
            download.assert_called_once()
        target.write_text("corrupted copy")
        with patch.object(bundle, "cached_record", return_value=target):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                bundle.download_sources(self.root, destination)

    def test_source_downloader_rejects_paths_outside_destination(self):
        record = {**self.record, "file": "../outside.tar.gz"}
        (self.metadata / "sources.json").write_text(json.dumps({"sources": [record]}))
        with patch.object(bundle, "cached_record") as download:
            with self.assertRaisesRegex(ValueError, "Invalid source archive path"):
                bundle.download_sources(self.root)
            download.assert_not_called()

    def test_successful_packaging_removes_obsolete_generated_files_only(self):
        label = f"ModemDiag-0.9.0-{sys.platform}-{platform.machine()}"
        old = [self.root / "local-ignored/dist" / (label + suffix) for suffix in (".zip", "-source.tar.gz", "-SHA256SUMS.txt")]
        for path in old:
            path.write_text("old generated output")
        unrelated = self.root / "local-ignored/dist/user-notes.txt"
        unrelated.write_text("keep this")
        bundle.package(self.root)
        self.assertTrue(all(not path.exists() for path in old))
        self.assertEqual(unrelated.read_text(), "keep this")

    def test_failure_preserves_previous_zip_and_source_files(self):
        label = f"ModemDiag-1.0.1-{sys.platform}-{platform.machine()}"
        previous = self.root / "local-ignored/dist" / (label + ".zip")
        previous.write_text("previous ZIP")
        old_source = self.root / "local-ignored/dist" / (label + "-source.tar.gz")
        old_source.write_text("previous source")
        (self.metadata / "NOTICES.txt").unlink()
        with self.assertRaises(FileNotFoundError):
            bundle.package(self.root)
        self.assertEqual(previous.read_text(), "previous ZIP")
        self.assertEqual(old_source.read_text(), "previous source")
        self.assertFalse(previous.with_suffix(".zip.tmp").exists())

    def test_replacing_executable_requires_a_new_release_build(self):
        (self.root / "local-ignored/dist" / self.exe_name).write_text("different executable")
        with self.assertRaisesRegex(ValueError, "Executable changed"):
            bundle.package(self.root)

    def test_private_data_drafts_and_symlinks_are_excluded(self):
        for name in ("user_commands.yaml", "quick_send.yaml", "appearance.json", ".env",
                     ".codex/config.toml", ".claude/settings.json", "local-ignored/review/private-notes.md",
                     "macros/private.yaml", "src/private.pem"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("private fixture")
        (self.root / "src/linked.py").symlink_to(self.root / ".env")
        selected = {path.relative_to(self.root).as_posix() for path in bundle.project_files(self.root)}
        self.assertEqual(selected, {"main.py", "README.md", "LICENSE", "src/modemdiag/__init__.py",
                                    "src/modemdiag/app.py", "resources/macros/example_lte_check.yaml"})

    def test_changed_application_files_require_a_rebuild(self):
        (self.root / "src/modemdiag/app.py").write_text("# changed after build")
        with self.assertRaisesRegex(ValueError, "changed since the build"):
            bundle.package(self.root)
        self.assertFalse(list((self.root / "local-ignored/dist").glob("*.zip")))

    def test_notice_extraction_does_not_follow_archive_traversal(self):
        with tarfile.open(self.upstream, "w:gz") as archive:
            for name in ("example/LICENSE", "../LICENSE", "/LICENSE", "example/src.c",
                         "example/doc/licensewizard-example.png", "example/doc/LICENSE-example.PNG"):
                payload = b"fixture"
                item = tarfile.TarInfo(name)
                item.size = len(payload)
                archive.addfile(item, io.BytesIO(payload))
        with patch.object(bundle, "cached_record", return_value=self.upstream):
            bundle.copy_archive_notices(self.record, self.root / "notices")
        actual = [path.relative_to(self.root / "notices").as_posix()
                  for path in (self.root / "notices").rglob("*") if path.is_file()]
        self.assertEqual(actual, ["example/example/LICENSE"])

    def test_libyaml_records_the_bundled_extension_version_and_pinned_hash(self):
        from yaml import _yaml
        binaries = [("yaml/_yaml.pyd", _yaml.__file__, "EXTENSION")]
        with patch.object(_yaml, "get_version_string", return_value="0.2.5"), \
                patch.object(bundle, "source_record", return_value=self.record) as source:
            self.assertEqual(bundle.libyaml_source(binaries), self.record)
        source.assert_called_once_with("libyaml", "0.2.5",
            "https://pyyaml.org/download/libyaml/yaml-0.2.5.tar.gz",
            "c642ae9b75fee120b2d96c712538bd2cf283228d2337df2cf2988e3c02678ef4")
        with patch.object(_yaml, "get_version_string", return_value="99.0"):
            with self.assertRaisesRegex(ValueError, "verified LibYAML"):
                bundle.libyaml_source(binaries)
        with self.assertRaisesRegex(ValueError, "differs"):
            bundle.libyaml_source([("yaml/_yaml.pyd", str(self.upstream), "EXTENSION")])
        with patch.object(bundle, "source_record") as source:
            self.assertIsNone(bundle.libyaml_source([]))
            source.assert_not_called()

    def windows_review(self):
        runtime = self.root / "runtime.dll"
        runtime.write_bytes(b"runtime")
        notice = self.root / "runtime-notice.txt"
        notice.write_text("Runtime copyright and redistribution terms")
        component = {"component": "Example runtime", "version": "1",
                     "files": {"runtime.dll": bundle.digest(runtime)},
                     "notice_file": notice.name,
                     "redistribution_basis": "Example license permits binary redistribution",
                     "sources": [{"component": "example", "version": "1"}]}
        review = self.root / "private-windows-review.json"
        review.write_text(json.dumps({"components": [component]}))
        return [("runtime.dll", str(runtime), "BINARY")], review, component

    def test_windows_requires_review_even_for_a_dll(self):
        binaries, _, _ = self.windows_review()
        with self.assertRaisesRegex(ValueError, "need review"):
            bundle.windows_native_sources(binaries, self.metadata, [self.record])
        inventory = json.loads((self.metadata / "windows-native-inventory.json").read_text())
        self.assertEqual(inventory[0]["file"], "runtime.dll")
        self.assertNotIn(str(self.root), json.dumps(inventory))

    def test_windows_review_preserves_notices_without_private_paths(self):
        binaries, review, component = self.windows_review()
        extra, native = bundle.windows_native_sources(binaries, self.metadata, [self.record], review)
        self.assertEqual(extra, [])
        self.assertEqual(native[0]["files"], component["files"])
        self.assertNotIn("notice_file", native[0])
        bundle.compact_notices(self.root, self.metadata, {"sources": [self.record]})
        self.assertIn("Runtime copyright and redistribution terms", (self.metadata / "NOTICES.txt").read_text())

    def test_windows_rejects_changed_and_unreviewed_binaries(self):
        binaries, review, _ = self.windows_review()
        Path(binaries[0][1]).write_bytes(b"changed runtime")
        with self.assertRaisesRegex(ValueError, "Stale"):
            bundle.windows_native_sources(binaries, self.metadata, [self.record], review)
        binaries, review, _ = self.windows_review()
        binaries.append(("unknown.pyd", str(self.upstream), "EXTENSION"))
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            bundle.windows_native_sources(binaries, self.metadata, [self.record], review)

    def test_windows_rejects_missing_notices_sources_and_redistribution_basis(self):
        binaries, review, component = self.windows_review()
        for field, value, message in (("notice_file", "missing.txt", "Missing"),
                                     ("sources", [], "Specify sources"),
                                     ("redistribution_basis", "", "redistribution_basis"),
                                     ("sources", [{"component": "unknown", "version": "2"}], "no matching")):
            with self.subTest(field=field, value=value):
                review.write_text(json.dumps({"components": [{**component, field: value}]}))
                with self.assertRaisesRegex(ValueError, message):
                    bundle.windows_native_sources(binaries, self.metadata, [self.record], review)

    def test_windows_allows_documented_source_exception_and_verified_extra_sources(self):
        binaries, review, component = self.windows_review()
        component.update(sources=[], source_exception="Proprietary runtime: license permits redistribution without source")
        review.write_text(json.dumps({"components": [component]}))
        _, native = bundle.windows_native_sources(binaries, self.metadata, [], review)
        self.assertIn("without source", native[0]["source_exception"])
        component.pop("source_exception")
        component["sources"] = [self.record]
        review.write_text(json.dumps({"components": [component]}))
        with patch.object(bundle, "source_record", return_value=self.record) as source:
            extra, _ = bundle.windows_native_sources(binaries, self.metadata, [], review)
        self.assertEqual(extra, [self.record])
        source.assert_called_once_with("example", "1", self.record["url"], self.record["sha256"])

    def test_source_verification_reports_corruption_and_link_failures(self):
        with patch.object(bundle, "cached_record", return_value=self.upstream):
            self.assertTrue(bundle.verify_sources(self.root))
            with patch.object(bundle.urllib.request, "urlopen", side_effect=OSError("unreachable")):
                self.assertFalse(bundle.verify_sources(self.root, check_links=True))
        report = json.loads((self.root / "local-ignored/reports/source-verification.json").read_text())
        self.assertIn("unreachable", report["results"][0]["error"])
        self.upstream.write_bytes(b"corrupted archive")
        with patch.object(bundle, "cached_record", return_value=self.upstream):
            self.assertFalse(bundle.verify_sources(self.root))

    def test_ubuntu_sources_record_retained_urls_when_downloaded_from_pool(self):
        base = "https://launchpad.net/ubuntu/+archive/primary/+sourcefiles/example/1/"
        urls = [base + "example_1.dsc", base + "example_1.tar.gz"]
        descriptor = self.root / "example.dsc"
        descriptor.write_text("Checksums-Sha256:\n " + "a" * 64 + " 123 example_1.tar.gz\n")
        def record(name, version, url, checksum=None):
            return {**self.record, "component": name, "version": version, "url": url}
        with patch.object(bundle, "json_url", side_effect=[
                {"entries": [{"self_link": "https://api.launchpad.net/source", "component_name": "main"}]}, urls]), \
                patch.object(bundle, "cache_path", return_value=self.root / "not-cached"), \
                patch.object(bundle, "source_record", side_effect=record) as download, \
                patch.object(bundle, "cached_record", return_value=descriptor):
            records = bundle.ubuntu_source("example", "1")
        self.assertEqual([item["url"] for item in records], urls)
        self.assertTrue(all(call.args[2].startswith("https://archive.ubuntu.com/")
                            for call in download.call_args_list))
