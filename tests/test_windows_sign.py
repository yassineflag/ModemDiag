import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import windows_sign as signing
from tools import release_bundle as bundle


class WindowsSigningTests(unittest.TestCase):
    def test_unconfigured_build_is_explicitly_unsigned_and_required_signing_fails(self):
        with patch.object(signing.subprocess, "run") as run:
            self.assertFalse(signing.sign_build(environment={}))
            with self.assertRaisesRegex(ValueError, "Signing is required"):
                signing.sign_build(environment={"MODEMDIAG_REQUIRE_SIGNING": "1"})
        run.assert_not_called()

    def test_sign_verify_then_checksum_describes_the_signed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, metadata = root / "local-ignored/dist", root / "local-ignored/build/release"
            metadata.mkdir(parents=True)
            folder = dist / "ModemDiag-app"
            folder.mkdir(parents=True)
            executable = folder / "ModemDiag.exe"
            executable.write_bytes(b"unsigned")
            bundle.record_bundle(metadata, dist, executable, folder)
            environment = {"MODEMDIAG_SIGN_CERT_SHA1": "a" * 40, "MODEMDIAG_SIGNTOOL": "signtool.exe",
                           "MODEMDIAG_TIMESTAMP_URL": "http://timestamp.example.test"}
            def run(arguments, **kwargs):
                if arguments[1] == "sign":
                    executable.write_bytes(b"signed fixture")
                return subprocess.CompletedProcess(arguments, 0)
            with patch.object(signing.sys, "platform", "win32"), patch.object(signing.subprocess, "run", side_effect=run) as invoked:
                self.assertTrue(signing.sign_build(root, environment))
            self.assertEqual([call.args[0][1] for call in invoked.call_args_list], ["sign", "verify"])
            self.assertIn("/pa", invoked.call_args_list[-1].args[0])
            self.assertEqual((metadata / "executable.sha256").read_text().strip(), bundle.digest(executable))
            self.assertEqual(bundle.read_bundle(metadata, dist)[0], executable)

    def test_failed_verification_does_not_update_release_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dist, metadata = root / "local-ignored/dist", root / "local-ignored/build/release"
            dist.mkdir(parents=True)
            metadata.mkdir(parents=True)
            executable = dist / "ModemDiag.exe"
            executable.write_bytes(b"unsigned")
            bundle.record_bundle(metadata, dist, executable)
            before = (metadata / "executable.sha256").read_text()
            environment = {"MODEMDIAG_SIGN_CERT_SHA1": "a" * 40, "MODEMDIAG_SIGNTOOL": "signtool.exe",
                           "MODEMDIAG_TIMESTAMP_URL": "http://timestamp.example.test"}
            def run(arguments, **kwargs):
                if arguments[1] == "sign":
                    executable.write_bytes(b"unverified signature")
                    return subprocess.CompletedProcess(arguments, 0)
                raise subprocess.CalledProcessError(1, arguments)
            with patch.object(signing.sys, "platform", "win32"), patch.object(signing.subprocess, "run", side_effect=run), \
                    self.assertRaises(subprocess.CalledProcessError):
                signing.sign_build(root, environment)
            self.assertEqual((metadata / "executable.sha256").read_text(), before)
            with self.assertRaisesRegex(ValueError, "changed since the build"):
                bundle.read_bundle(metadata, dist)
