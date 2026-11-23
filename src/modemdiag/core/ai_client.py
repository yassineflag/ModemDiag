"""Cancellable Qt HTTP client for OpenAI Responses and compatible chat APIs."""
import json

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from modemdiag.core.ai_diagnostics import MAX_RESPONSE_BYTES, PLAN_SCHEMA, SYSTEM_PROMPT, parse_plan, redact


class AIClient(QObject):
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._data = bytearray()
        self._config = None
        self._failure = ""
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timeout)

    @property
    def busy(self):
        return self._reply is not None

    def request(self, config, messages):
        if self.busy:
            raise ValueError("An AI request is already in progress.")
        config.validate()
        instructions = SYSTEM_PROMPT
        messages = [{"role": message["role"], "content": redact(message["content"][:24000], (config.api_key,))} for message in messages[-12:]]
        if config.provider == "openai":
            endpoint = config.base_url.rstrip("/") + "/responses"
            payload = {"model": config.model, "instructions": instructions, "input": messages,
                       "store": False, "max_output_tokens": 4096,
                       "text": {"format": {"type": "json_schema", "name": "modem_diagnosis",
                                             "strict": True, "schema": PLAN_SCHEMA}}}
        else:
            endpoint = config.base_url.rstrip("/") + "/chat/completions"
            payload = {"model": config.model, "messages": [{"role": "system", "content": instructions}, *messages],
                       "stream": False, "max_tokens": 4096}
        request = QNetworkRequest(QUrl(endpoint))
        request.setTransferTimeout(90000)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Accept", b"application/json")
        if config.api_key:
            request.setRawHeader(b"Authorization", ("Bearer " + config.api_key).encode("ascii"))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setAttribute(QNetworkRequest.Attribute.CookieLoadControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.CookieSaveControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute, QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        self._data = bytearray()
        self._failure = ""
        self._config = config
        self._reply = self._network.post(request, json.dumps(payload).encode("utf-8"))
        self._reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        reply = self._reply
        reply.readyRead.connect(lambda: self._read_ready(reply))
        reply.finished.connect(lambda: self._finished(reply))
        self._deadline.start(90000)

    def cancel(self):
        self._deadline.stop()
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        self._data = bytearray()
        self._config = None

    def _timeout(self):
        self._failure = "The AI request timed out. You can retry or choose another model."
        if self._reply is not None:
            self._reply.abort()

    def _read_ready(self, expected=None):
        reply = self._reply
        if reply is None or (expected is not None and reply is not expected) or self._failure or reply.bytesAvailable() <= 0:
            return
        chunk = reply.read(MAX_RESPONSE_BYTES + 1 - len(self._data))
        if chunk:
            self._data.extend(bytes(chunk))
        if len(self._data) > MAX_RESPONSE_BYTES:
            self._failure = "The AI response exceeded the size limit."
            reply.abort()

    def _finished(self, expected=None):
        reply = self._reply
        if reply is None or (expected is not None and reply is not expected):
            return
        self._deadline.stop()
        self._read_ready()
        # Oversize reads can synchronously emit finished again while aborting.
        if self._reply is None:
            return
        self._reply = None
        config, self._config = self._config, None
        try:
            if self._failure:
                raise ValueError(self._failure)
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status != 200 or reply.error() != QNetworkReply.NetworkError.NoError:
                detail = ""
                try:
                    error = json.loads(bytes(self._data)).get("error", {})
                    detail = error.get("message", "") if isinstance(error, dict) else str(error)
                except (ValueError, AttributeError):
                    pass
                raise ValueError(f"AI request failed (HTTP {status or 'unavailable'}). " + (detail[:500] or "Check the connection, API key, model and account access."))
            data = json.loads(bytes(self._data))
            if config.provider == "openai":
                if data.get("status") != "completed":
                    raise ValueError("The AI response was incomplete. No commands were scheduled; retry with a smaller request.")
                parts = [part for item in data.get("output", []) if item.get("type") == "message" for part in item.get("content", [])]
                refusal = next((part.get("refusal") for part in parts if part.get("type") == "refusal"), None)
                answer = "\n".join(part.get("text", "") for part in parts if part.get("type") == "output_text")
            else:
                choice = data["choices"][0]
                if choice.get("finish_reason") not in (None, "stop"):
                    raise ValueError("The AI response was cut short. No commands were scheduled.")
                refusal = choice["message"].get("refusal")
                answer = choice["message"].get("content", "")
            if refusal:
                self.completed.emit({"reply": redact(str(refusal), (config.api_key,)), "commands": []})
            elif isinstance(answer, str) and answer.strip():
                self.completed.emit(parse_plan(redact(answer, (config.api_key,))))
            else:
                raise ValueError("The AI returned no text. No commands were scheduled.")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            self.failed.emit(redact(str(exc), (config.api_key,)))
        finally:
            self._data = bytearray()
            reply.deleteLater()
