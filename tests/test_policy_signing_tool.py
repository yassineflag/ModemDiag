import os
from pathlib import Path
import stat
import tempfile
import unittest

from modemdiag.core.release_policy import verify_policy
from tools.release_policy import ROOT, generate_key, sign_policy


class SigningToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = self.root / "private.pem"
        self.output = self.root / "release-policy.json"

    def test_generated_key_is_private_and_cannot_be_overwritten(self):
        public = generate_key(self.key)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            generate_key(self.key)
        sign_policy(self.key, self.output, 1, versions=["1.0.0"])
        policy = verify_policy(self.output.read_bytes(), public)
        self.assertTrue(policy.revokes("1.0.0"))

    def test_private_key_is_never_created_inside_repository(self):
        with self.assertRaises(ValueError):
            generate_key(ROOT / "private-signing-key.pem")
        self.assertFalse((ROOT / "private-signing-key.pem").exists())

    def test_sequence_must_increase_and_empty_list_explicitly_restores(self):
        public = generate_key(self.key)
        sign_policy(self.key, self.output, 1, revoke_all=True)
        original = self.output.read_bytes()
        with self.assertRaises(ValueError):
            sign_policy(self.key, self.output, 1)
        self.assertEqual(self.output.read_bytes(), original)
        sign_policy(self.key, self.output, 2)
        self.assertFalse(verify_policy(self.output.read_bytes(), public).revokes("1.0.0"))

    def test_wrong_key_cannot_replace_existing_policy(self):
        generate_key(self.key)
        sign_policy(self.key, self.output, 1)
        second = self.root / "second.pem"
        generate_key(second)
        with self.assertRaises(ValueError):
            sign_policy(second, self.output, 2, revoke_all=True)

    def test_prepared_initial_policy_matches_embedded_public_key(self):
        from modemdiag.core.release_policy import PUBLIC_KEY_B64
        policy = verify_policy((ROOT / "release-policy.json").read_bytes(), PUBLIC_KEY_B64)
        self.assertFalse(policy.revoke_all)
        self.assertFalse(policy.revoked_versions)

    def test_tool_signs_optional_and_required_update_announcements(self):
        public = generate_key(self.key)
        sign_policy(self.key, self.output, 1, latest_version="1.1.0", update_message="New features.")
        policy = verify_policy(self.output.read_bytes(), public)
        self.assertTrue(policy.offers_update("1.0.0"))
        self.assertFalse(policy.revokes("1.0.0"))
        sign_policy(self.key, self.output, 2, versions=["1.0.0"], latest_version="1.1.0")
        policy = verify_policy(self.output.read_bytes(), public)
        self.assertTrue(policy.revokes("1.0.0"))
        self.assertFalse(policy.revokes("1.1.0"))


if __name__ == "__main__":
    unittest.main()
