import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from modemdiag.core.release_policy import PolicyError, PolicyState, verify_policy, MAX_POLICY_BYTES
from tests.release_policy_fixtures import policy_document, public_key


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name) / "cache.json"
        self.key = Ed25519PrivateKey.generate()
        self.public = public_key(self.key)
        self.state = PolicyState("1.0.0", self.cache, self.public)

    def test_first_launch_offline_is_allowed(self):
        self.assertFalse(self.state.revoked)
        self.assertFalse(self.cache.exists())

    def test_version_specific_policy_does_not_revoke_other_releases(self):
        self.state.accept(policy_document(self.key, versions=["2.0.0"]))
        self.assertFalse(self.state.revoked)
        self.state.accept(policy_document(self.key, sequence=2, versions=["1.0.0"]))
        self.assertTrue(self.state.revoked)

    def test_revoke_all(self):
        self.state.accept(policy_document(self.key, revoke_all=True))
        self.assertTrue(self.state.revoked)

    def test_cached_revocation_survives_offline_restart_and_expiration(self):
        past = datetime.now(timezone.utc) - timedelta(days=90)
        raw = policy_document(self.key, versions=["1.0.0"], now=past)
        self.cache.write_bytes(raw)
        restored = PolicyState("1.0.0", self.cache, self.public)
        self.assertTrue(restored.revoked)
        with self.assertRaises(PolicyError):
            restored.accept(raw)
        self.assertTrue(restored.revoked)

    def test_newer_signed_policy_restores_access(self):
        self.state.accept(policy_document(self.key, sequence=2, revoke_all=True))
        self.assertTrue(self.state.accept(policy_document(self.key, sequence=3)))
        self.assertFalse(self.state.revoked)
        self.assertFalse(PolicyState("1.0.0", self.cache, self.public).revoked)

    def test_replay_cannot_restore_access_including_after_restart(self):
        allow = policy_document(self.key, sequence=1)
        self.state.accept(policy_document(self.key, sequence=2, revoke_all=True))
        restored = PolicyState("1.0.0", self.cache, self.public)
        with self.assertRaises(PolicyError):
            restored.accept(allow)
        self.assertTrue(restored.revoked)

    def test_same_sequence_cannot_change_decision(self):
        original = policy_document(self.key, sequence=2, revoke_all=True)
        self.state.accept(original)
        self.assertFalse(self.state.accept(original))
        with self.assertRaises(PolicyError):
            self.state.accept(policy_document(self.key, sequence=2))
        self.assertTrue(self.state.revoked)

    def test_wrong_signer_cannot_disable_or_enable(self):
        other = Ed25519PrivateKey.generate()
        with self.assertRaises(PolicyError):
            self.state.accept(policy_document(other, revoke_all=True))
        self.assertFalse(self.state.revoked)
        self.state.accept(policy_document(self.key, revoke_all=True))
        with self.assertRaises(PolicyError):
            self.state.accept(policy_document(other, sequence=2))
        self.assertTrue(self.state.revoked)

    def test_changed_payload_rejects_signature(self):
        document = json.loads(policy_document(self.key))
        payload = json.loads(base64.b64decode(document["payload"]))
        payload["revoke_all"] = True
        document["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
        with self.assertRaises(PolicyError):
            self.state.accept(json.dumps(document).encode())
        self.assertFalse(self.state.revoked)

    def test_expired_or_future_network_policy_does_not_change_state(self):
        now = datetime.now(timezone.utc)
        for time in [now - timedelta(days=2), now + timedelta(days=2)]:
            with self.subTest(time=time), self.assertRaises(PolicyError):
                self.state.accept(policy_document(self.key, revoke_all=True, now=time), now)
        self.assertFalse(self.state.revoked)

    def test_unsigned_malformed_and_oversize_policies_are_rejected(self):
        for raw in [b'{}', b'not json', b'\xff', b'{"payload":"!","signature":"!"}',
                    b'x' * (MAX_POLICY_BYTES + 1)]:
            with self.subTest(raw=raw[:40]), self.assertRaises(PolicyError):
                self.state.accept(raw)
        self.assertFalse(self.state.revoked)

    def test_duplicate_envelope_keys_are_rejected(self):
        valid = policy_document(self.key)
        raw = b'{"payload":"duplicate",' + valid[1:]
        with self.assertRaises(PolicyError):
            self.state.accept(raw)

    def test_schema_is_strict(self):
        for changes in [{"app": "OtherApp"}, {"schema": True}, {"sequence": True},
                        {"revoke_all": "true"}, {"revoked_versions": ["*"]},
                        {"revoked_versions": ["1.0.0", "1.0.0"]}, {"message": "x" * 501},
                        {"extra": "unsupported"}, {"issued_at": "2026-99-99T00:00:00Z"}]:
            with self.subTest(changes=changes), self.assertRaises(PolicyError):
                verify_policy(policy_document(self.key, **changes), self.public)

    def test_corrupt_cache_does_not_authenticate_a_revocation(self):
        self.cache.write_text('{"revoke_all":true}')
        restored = PolicyState("1.0.0", self.cache, self.public)
        self.assertFalse(restored.revoked)
        self.assertTrue(restored.cache_error)

    def test_cache_failure_still_revokes_current_session_and_preserves_existing_file(self):
        self.state.accept(policy_document(self.key))
        previous = self.cache.read_bytes()
        with patch("modemdiag.core.release_policy.os.replace", side_effect=OSError("disk full")):
            self.state.accept(policy_document(self.key, sequence=2, revoke_all=True))
        self.assertTrue(self.state.revoked)
        self.assertTrue(self.state.persistence_error)
        self.assertEqual(self.cache.read_bytes(), previous)
        self.assertEqual(list(self.cache.parent.iterdir()), [self.cache])

    def test_update_announcement_does_not_revoke_and_is_cached_offline(self):
        self.assertTrue(self.state.accept(policy_document(
            self.key, latest_version="1.1.0", update_message="Improved command editing.")))
        self.assertFalse(self.state.revoked)
        self.assertTrue(self.state.update_available)
        self.assertIn("1.1.0", self.state.update_message)
        restored = PolicyState("1.0.0", self.cache, self.public)
        self.assertTrue(restored.update_available)
        self.assertFalse(restored.revoked)
        self.assertIn("Improved command editing.", restored.update_message)

    def test_version_comparison_is_numeric_and_only_offers_newer_stable_versions(self):
        raw = policy_document(self.key, latest_version="1.10.0")
        for version, expected in [("1.9.0", True), ("1.10.0", False), ("2.0.0", False),
                                  ("1.9", True), ("1.10", False), ("2.0", False),
                                  ("1.10.0rc1", False), ("custom-build", False)]:
            with self.subTest(version=version):
                state = PolicyState(version, self.cache.parent / (version + ".json"), self.public)
                state.accept(raw)
                self.assertEqual(state.update_available, expected)
                self.assertFalse(state.revoked)

    def test_two_part_release_version_matches_zero_patch_and_can_receive_updates(self):
        state = PolicyState("1.1", self.cache, self.public)
        state.accept(policy_document(self.key, latest_version="1.1.0"))
        self.assertFalse(state.update_available)
        state.accept(policy_document(self.key, sequence=2, latest_version="1.2"))
        self.assertTrue(state.update_available)

    def test_update_can_be_required_for_one_version_without_revoking_the_target(self):
        self.state.accept(policy_document(self.key, versions=["1.0.0"], latest_version="1.1.0", message=""))
        self.assertTrue(self.state.revoked)
        self.assertTrue(self.state.update_available)
        self.assertEqual(self.state.message,
                         "This version is no longer supported. Please update ModemDiag.")
        self.assertFalse(verify_policy(self.cache.read_bytes(), self.public).revokes("1.1.0"))

    def test_a_revoked_target_is_not_advertised_as_an_available_update(self):
        self.state.accept(policy_document(self.key, versions=["1.1.0"], latest_version="1.1.0"))
        self.assertFalse(self.state.update_available)
        self.state.accept(policy_document(self.key, sequence=2, revoke_all=True, latest_version="1.1.0"))
        self.assertFalse(self.state.update_available)

    def test_newer_policy_can_remove_an_update_notice(self):
        self.state.accept(policy_document(self.key, latest_version="1.1.0"))
        self.assertTrue(self.state.accept(policy_document(self.key, sequence=2)))
        self.assertFalse(self.state.update_available)
        self.assertFalse(self.state.revoked)

    def test_invalid_update_metadata_is_rejected(self):
        for changes in [{"latest_version": "v2.0.0"}, {"latest_version": "2.0.0rc1"},
                        {"latest_version": None}, {"latest_version": "02.0.0"},
                        {"latest_version": "https://example.invalid"},
                        {"update_message": "No target version"},
                        {"latest_version": "2.0.0", "update_message": "x" * 501}]:
            with self.subTest(changes=changes), self.assertRaises(PolicyError):
                self.state.accept(policy_document(self.key, **changes))


if __name__ == "__main__":
    unittest.main()
