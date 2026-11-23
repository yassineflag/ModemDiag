import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import unittest
from unittest.mock import patch

from PyQt6.QtNetwork import QNetworkRequest
from modemdiag.core.ai_client import AIClient
from modemdiag.core.ai_diagnostics import AIConfig, MAX_RESPONSE_BYTES
from tests.test_release_check import FakeReply, APP


def response(plan=None, **extra):
    return json.dumps({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(plan or {"reply": "Read signal", "commands": [{"command": "AT+CSQ", "reason": "Signal"}]})}]}], **extra}).encode()


class AIClientTests(unittest.TestCase):
    def setUp(self):
        self.client = AIClient()
        self.config = AIConfig(api_key="sk-test-secret123456")
        self.answers, self.errors = [], []
        self.client.completed.connect(self.answers.append)
        self.client.failed.connect(self.errors.append)
        self.addCleanup(self.client.cancel)

    def receive(self, body, status=200, config=None):
        reply = FakeReply(body, status)
        with patch.object(self.client._network, "post", return_value=reply) as post:
            self.client.request(config or self.config, [{"role": "user", "content": "Diagnose the modem. " + self.config.api_key}])
            request, raw = post.call_args.args
            reply.finish()
        return request, json.loads(bytes(raw))

    def test_openai_uses_responses_structured_outputs_and_no_store(self):
        request, payload = self.receive(response())
        self.assertEqual(request.url().toString(), "https://api.openai.com/v1/responses")
        self.assertEqual(bytes(request.rawHeader(b"Authorization")), b"Bearer sk-test-secret123456")
        self.assertNotIn(self.config.api_key, json.dumps(payload))
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute), QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        self.assertEqual(self.answers[0]["commands"][0]["command"], "AT+CSQ")
        self.assertFalse(self.errors)

    def test_compatible_server_uses_chat_protocol_without_an_openai_key(self):
        config = AIConfig("compatible", "local-model", "http://localhost:11434/v1")
        request, payload = self.receive(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "Plain answer AT+CSQ"}}]}).encode(), config=config)
        self.assertEqual(request.url().toString(), "http://localhost:11434/v1/chat/completions")
        self.assertFalse(request.hasRawHeader(b"Authorization"))
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(self.answers[0]["commands"], [])

    def test_incomplete_refused_and_malformed_responses_never_schedule_commands(self):
        self.receive(response(status="incomplete"))
        self.assertTrue(self.errors)
        self.assertFalse(self.answers)
        self.receive(b'[]')
        self.assertFalse(self.answers)
        self.receive(json.dumps({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "Cannot help"}]}]}).encode())
        self.assertEqual(self.answers[-1]["commands"], [])

    def test_errors_are_redacted_and_redirects_are_not_followed(self):
        self.receive(json.dumps({"error": {"message": "Invalid key " + self.config.api_key}}).encode(), 401)
        self.assertNotIn(self.config.api_key, self.errors[-1])
        self.assertIn("401", self.errors[-1])
        self.receive(b"redirect", 302)
        self.assertFalse(self.answers)

    def test_oversize_timeout_and_cancellation_are_bounded(self):
        self.receive(b"x" * (MAX_RESPONSE_BYTES + 1))
        self.assertIn("size limit", self.errors[-1])
        reply = FakeReply()
        with patch.object(self.client._network, "post", return_value=reply):
            self.client.request(self.config, [])
            self.client._timeout()
        self.assertIn("timed out", self.errors[-1])
        errors = len(self.errors)
        reply = FakeReply()
        with patch.object(self.client._network, "post", return_value=reply):
            self.client.request(self.config, [])
            self.client.cancel()
        self.assertEqual(len(self.errors), errors)
        self.assertFalse(self.client.busy)

    def test_late_cancelled_reply_cannot_complete_a_new_request(self):
        old, new = FakeReply(response()), FakeReply(response())
        with patch.object(self.client._network, "post", side_effect=[old, new]):
            self.client.request(self.config, [])
            self.client.cancel()
            self.client.request(self.config, [])
            old.finished.emit()
            self.assertTrue(self.client.busy)
            self.assertFalse(self.answers)
            new.finish()
        self.assertEqual(len(self.answers), 1)
