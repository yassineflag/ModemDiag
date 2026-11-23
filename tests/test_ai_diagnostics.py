import json
from pathlib import Path
import tempfile
import unittest

from modemdiag.core.ai_diagnostics import (
    AIConfig, command_access, diagnostic_context, execution_steps, load_config,
    parse_plan, redact, save_config,
)
from modemdiag.core.network_status import NetworkSnapshot, CellInfo


class AIDiagnosticsTests(unittest.TestCase):
    def test_only_preferences_are_saved_and_secrets_are_never_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ai-settings.json"
            config = AIConfig(api_key="sk-test-123456789")
            save_config(path, config)
            content = path.read_text()
            self.assertNotIn(config.api_key, content)
            self.assertNotIn("api_key", content)
            self.assertEqual(load_config(path).model, config.model)
            path.write_text('{"api_key":"do-not-load","provider":"openai"}')
            self.assertEqual(load_config(path).api_key, "")
            path.write_text('broken')
            self.assertEqual(load_config(path).provider, "openai")

    def test_endpoint_validation_protects_bearer_credentials(self):
        for endpoint in ("http://remote.example/v1", "https://user:pass@example.test/v1", "https://example.test/v1?key=secret",
                         "https://example.test/v1#fragment", "file:///tmp/api", "https://example.test:bad/v1", "https://[broken"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                AIConfig("compatible", "local-model", endpoint).validate()
        for endpoint in ("https://example.test/v1", "http://localhost:11434/v1", "http://127.0.0.1:8080/v1", "http://[::1]:8080/v1"):
            AIConfig("compatible", "local-model", endpoint).validate()
        with self.assertRaises(ValueError):
            AIConfig(api_key="").validate()
        with self.assertRaises(ValueError):
            AIConfig(api_key="token\r\nInjected: header").validate()

    def test_plans_are_structured_and_prose_never_becomes_executable(self):
        plan = parse_plan(json.dumps({"reply": "Inspect registration.", "commands": [{"command": "AT+CEREG?", "reason": "Read registration"}]}))
        self.assertEqual(plan["commands"][0]["access"], "read")
        prose = parse_plan('Try AT+CFUN=1,1 or ```AT&F```')
        self.assertEqual(prose["commands"], [])
        fenced = parse_plan('```json\n{"reply":"Ready","commands":[]}\n```')
        self.assertEqual(fenced["reply"], "Ready")

    def test_malformed_or_chained_commands_reject_the_whole_plan(self):
        for command in ("AT\r\nAT&F", "AT+CSQ;AT&F", "rm -rf /", " AT+CSQ", "AT+CSQ\x00", "AT+" + "X" * 300):
            with self.subTest(command=command), self.assertRaises(ValueError):
                parse_plan(json.dumps({"reply": "Try these", "commands": [{"command": "AT+CSQ", "reason": "Signal"}, {"command": command, "reason": "Invalid"}]}))
        with self.assertRaises(ValueError):
            parse_plan(json.dumps({"reply": "Too many", "commands": [{"command": "AT", "reason": "probe"}] * 7}))

    def test_host_policy_does_not_trust_the_models_access_claims(self):
        self.assertEqual(command_access('AT+QENG="servingcell"'), "read")
        self.assertEqual(command_access("AT+CGPADDR=1,2"), "read")
        self.assertEqual(command_access('AT+CGDCONT=1,"IP","internet.example"'), "review")
        for command in ("AT&F", "ATZ", "AT+CFUN=1,1", "AT+CPIN=1234", 'AT+QFDEL="file"', "AT+COPS=?", "AT+UNKNOWN?"):
            self.assertEqual(command_access(command), "manual")
            with self.assertRaises(ValueError):
                execution_steps([{"command": command, "access": "read"}], automatic=True)
        with self.assertRaises(ValueError):
            execution_steps([{"command": "AT+CEREG=2", "access": "read"}], automatic=True)
        steps = execution_steps([{"command": "AT+CEREG=2"}], automatic=False)
        self.assertEqual(steps[0]["on_failure"], "stop")
        self.assertNotIn("payload", steps[0])

    def test_context_is_bounded_and_redacts_credentials_and_identifiers(self):
        snapshot = NetworkSnapshot(device="Module IMEI 123456789012345")
        snapshot.cells = [CellInfo("Serving", "LTE", "1234567", source="unrelated secret URC")]
        context = diagnostic_context(snapshot, 'AT+CPIN="1234"\npassword=abc\nprivate-token\n' + 'tail\n' * 70,
                                     connected=True, secrets=("private-token",))
        self.assertNotIn("123456789012345", context)
        self.assertNotIn("unrelated secret URC", context)
        self.assertNotIn('"source"', context)
        self.assertIn("1234567", context)
        clean = redact('AT+CPIN="1234"\npassword=abc\nprivate-token\n123456789012345', ("private-token",))
        for secret in ("1234", "abc", "private-token", "123456789012345"):
            self.assertNotIn(secret, clean)
        data = json.loads(diagnostic_context(None, connected=False))
        self.assertEqual(data, {"modem_connected": False})
