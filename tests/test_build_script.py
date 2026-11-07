"""Exercise build setup without downloading packages or building an executable."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class BuildScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ModemDiag build ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copy2(ROOT / "build.sh", self.root / "build.sh")
        self.bin = self.root / "tools"
        self.bin.mkdir()
        self.log = self.root / "calls.log"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "BUILD_TEST_LOG": str(self.log)}
        self.template = self.root / "environment-python"
        self.template.write_text('''#!/usr/bin/env bash
printf 'env %s\n' "$*" >> "$BUILD_TEST_LOG"
if [[ "$*" == "-m pip --version" ]]; then
    test -f .venv/ready
elif [[ "$*" == "-m pip install "* ]]; then
    test "${BUILD_TEST_FAIL_INSTALL:-0}" != 1
elif [[ "$*" == "-m PyInstaller tools/build/ModemDiag.spec --distpath local-ignored/dist --workpath local-ignored/build --noconfirm" ]]; then
    mkdir -p local-ignored/dist
    touch local-ignored/dist/ModemDiag
elif [[ "$*" == "tools/release_bundle.py package" ]]; then
    test "${BUILD_TEST_FAIL_PACKAGE:-0}" != 1
fi
''')
        self.template.chmod(0o755)
        system_python = self.bin / "python3"
        system_python.write_text('''#!/usr/bin/env bash
printf 'system %s\n' "$*" >> "$BUILD_TEST_LOG"
if [[ "$*" == "--version" ]]; then
    echo 'Python 3.10 (build test)'
elif [[ "$*" == "-m venv .venv" ]]; then
    if [[ "${BUILD_TEST_FAIL_VENV:-0}" == 1 ]]; then exit 1; fi
    mkdir -p .venv/bin
    cp environment-python .venv/bin/python
    touch .venv/ready
else
    exit 1
fi
''')
        system_python.chmod(0o755)

    def run_build(self, *arguments):
        return subprocess.run(["bash", "build.sh", *arguments], cwd=self.root, env=self.env,
                              text=True, capture_output=True, timeout=15)

    def make_ready_environment(self):
        (self.root / ".venv/bin").mkdir(parents=True)
        shutil.copy2(self.template, self.root / ".venv/bin/python")
        (self.root / ".venv/ready").touch()

    def test_usable_environment_does_not_require_an_activation_script(self):
        self.make_ready_environment()
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("system -m venv", self.log.read_text())
        self.assertIn("env -m PyInstaller", self.log.read_text())
        self.assertTrue((self.root / "local-ignored/dist/ModemDiag").exists())
        self.assertNotIn("env tools/release_bundle.py package", self.log.read_text())

    def test_release_packaging_is_explicit(self):
        self.make_ready_environment()
        result = self.run_build("--release")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("env tools/release_bundle.py package", self.log.read_text())

    def test_release_source_build_uses_the_recorded_dependency_versions(self):
        self.make_ready_environment()
        (self.root / "requirements-release.txt").write_text("example==1.0\n")
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.log.read_text()
        self.assertIn("-r requirements-release.txt", calls)
        self.assertNotIn("--upgrade pip", calls)

    def test_shared_source_build_selects_this_platforms_recorded_versions(self):
        import platform
        self.make_ready_environment()
        requirements = self.root / f"release/linux-{platform.machine()}/requirements-release.txt"
        requirements.parent.mkdir(parents=True)
        requirements.write_text("example==2.0\n")
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"-r release/linux-{platform.machine()}/requirements-release.txt", self.log.read_text())

    def test_missing_release_materials_prevent_a_success_message(self):
        self.make_ready_environment()
        self.env["BUILD_TEST_FAIL_PACKAGE"] = "1"
        result = self.run_build("--release")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("✓ Build complete!", result.stdout)

    def test_incomplete_environment_is_repaired_without_removing_existing_files(self):
        (self.root / ".venv").mkdir()
        marker = self.root / ".venv/keep.txt"
        marker.write_text("existing environment file")
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("system -m venv .venv", self.log.read_text())
        self.assertEqual(marker.read_text(), "existing environment file")

    def test_venv_creation_failure_preserves_previous_build_and_gives_guidance(self):
        self.env["BUILD_TEST_FAIL_VENV"] = "1"
        previous = self.root / "local-ignored/dist/ModemDiag"
        previous.parent.mkdir(parents=True)
        previous.write_text("previous build")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python3-venv", result.stdout)
        self.assertNotIn("env -m pip install", self.log.read_text())
        self.assertEqual(previous.read_text(), "previous build")

    def test_dependency_failure_stops_before_cleaning_previous_build(self):
        self.make_ready_environment()
        self.env["BUILD_TEST_FAIL_INSTALL"] = "1"
        previous = self.root / "local-ignored/dist/ModemDiag"
        previous.parent.mkdir(parents=True)
        previous.write_text("previous build")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("env -m PyInstaller", self.log.read_text())
        self.assertEqual(previous.read_text(), "previous build")


if __name__ == "__main__":
    unittest.main()
