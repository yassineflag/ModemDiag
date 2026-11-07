import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QIODevice, QUrl
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QApplication

from modemdiag.core.release_check import ReleaseGuard
from modemdiag.core.release_policy import MAX_POLICY_BYTES
from tests.release_policy_fixtures import Ed25519PrivateKey, policy_document, public_key

APP = QApplication.instance() or QApplication([])


class FakeReply(QNetworkReply):
    def __init__(self, body=b"", status=200):
        super().__init__()
        self.body = body
        self.offset = 0
        self.setUrl(QUrl("https://example.invalid/policy"))
        self.setAttribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute, status)
        self.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Unbuffered)

    def bytesAvailable(self):
        return len(self.body) - self.offset + super().bytesAvailable()

    def readData(self, maximum):
        chunk = self.body[self.offset:self.offset + maximum]
        self.offset += len(chunk)
        return chunk

    def finish(self):
        self.readyRead.emit()
        if not self.isFinished():
            self.setFinished(True)
            self.finished.emit()

    def abort(self):
        if not self.isFinished():
            self.setError(QNetworkReply.NetworkError.OperationCanceledError, "Cancelled")
            self.setFinished(True)
            self.finished.emit()


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.key = Ed25519PrivateKey.generate()
        self.guard = ReleaseGuard("1.0.0", Path(temporary.name) / "cache.json",
                                  public_key_b64=public_key(self.key))
        self.addCleanup(self.guard.stop)

    def receive(self, body, status=200):
        reply = FakeReply(body, status)
        with patch.object(self.guard._network, "get", return_value=reply):
            self.guard.check_now()
            reply.finish()
        return reply

    def test_authenticated_response_revokes(self):
        changes = []
        self.guard.state_changed.connect(lambda revoked, message: changes.append(revoked))
        self.receive(policy_document(self.key, revoke_all=True))
        self.assertTrue(self.guard.revoked)
        self.assertEqual(changes, [True])
        self.assertIsNone(self.guard._reply)

    def test_http_error_and_invalid_response_do_not_disable(self):
        self.receive(policy_document(self.key, revoke_all=True), status=404)
        self.assertFalse(self.guard.revoked)
        self.receive(b'not a signed policy')
        self.assertFalse(self.guard.revoked)

    def test_network_failure_does_not_lift_revocation(self):
        self.guard.apply_policy(policy_document(self.key, revoke_all=True))
        reply = FakeReply()
        with patch.object(self.guard._network, "get", return_value=reply):
            self.guard.check_now()
            self.guard._abort_request()
        self.assertTrue(self.guard.revoked)
        self.assertIsNone(self.guard._reply)

    def test_network_failure_without_policy_keeps_offline_use(self):
        reply = FakeReply()
        with patch.object(self.guard._network, "get", return_value=reply):
            self.guard.check_now()
            self.guard._abort_request()
        self.assertFalse(self.guard.revoked)

    def test_empty_or_closed_network_reply_cannot_crash_offline_startup(self):
        reply = FakeReply(b"unread bytes")
        with patch.object(self.guard._network, "get", return_value=reply), \
                patch.object(reply, "read", return_value=None):
            self.guard.check_now()
            self.guard._abort_request()
        self.assertFalse(self.guard.revoked)
        self.assertIsNone(self.guard._reply)

    def test_oversize_response_is_aborted_and_bounded(self):
        reply = self.receive(b'x' * (MAX_POLICY_BYTES * 2))
        self.assertEqual(len(self.guard._data), MAX_POLICY_BYTES + 1)
        self.assertFalse(self.guard.revoked)
        self.assertEqual(reply.error(), QNetworkReply.NetworkError.OperationCanceledError)

    def test_redirect_is_not_followed_or_used_as_a_policy(self):
        self.receive(policy_document(self.key, revoke_all=True), status=302)
        self.assertFalse(self.guard.revoked)

    def test_no_identifiers_cookies_or_credentials_are_added_to_request(self):
        reply = FakeReply()
        with patch.object(self.guard._network, "get", return_value=reply) as get:
            self.guard.check_now()
            request = get.call_args.args[0]
            self.assertFalse(request.url().hasQuery())
            self.assertFalse(request.url().userInfo())
            self.assertFalse(request.hasRawHeader(b"Authorization"))
            self.assertEqual(request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute),
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
            self.assertEqual(request.attribute(QNetworkRequest.Attribute.CookieLoadControlAttribute),
                             QNetworkRequest.LoadControl.Manual)
            self.assertTrue(self.guard._deadline.isActive())
            self.guard.stop()

    def test_stop_cancels_request_and_prevents_scheduled_checks(self):
        reply = FakeReply(policy_document(self.key, revoke_all=True))
        with patch.object(self.guard._network, "get", return_value=reply) as get:
            self.guard.check_now()
            self.guard.stop()
            self.guard.check_now()
        self.assertEqual(get.call_count, 1)
        self.assertFalse(self.guard.revoked)
        self.assertFalse(self.guard._deadline.isActive())


if __name__ == "__main__":
    unittest.main()
