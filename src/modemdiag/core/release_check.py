"""Bounded, asynchronous HTTPS fetch of the pinned release policy."""
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from modemdiag.core.release_policy import (
    PolicyState, PolicyError, POLICY_URL, PUBLIC_KEY_B64, CHECK_INTERVAL_MS, MAX_POLICY_BYTES,
)


class ReleaseGuard(QObject):
    state_changed = pyqtSignal(bool, str)
    status_changed = pyqtSignal(str)

    def __init__(self, version, cache_path, parent=None, *, public_key_b64=PUBLIC_KEY_B64,
                 policy_url=POLICY_URL):
        super().__init__(parent)
        self.state = PolicyState(version, cache_path, public_key_b64)
        self.url = QUrl(policy_url)
        self.status = "Release status has not been checked online. Offline use is allowed."
        if self.state.revoked:
            self.status = "This version is no longer supported. See the release notice."
        elif self.state.update_available:
            self.status = self.state.update_message
        elif self.state.cache_error:
            self.status = "Release cache could not be verified. Offline use is allowed."
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._data = bytearray()
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_now)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._abort_request)
        self._oversize = False
        self._stopped = False

    @property
    def revoked(self):
        return self.state.revoked

    @property
    def message(self):
        return self.state.message

    @property
    def update_available(self):
        return self.state.update_available

    @property
    def update_message(self):
        return self.state.update_message

    def _set_status(self, text):
        self.status = text
        self.status_changed.emit(text)

    def start(self):
        self._stopped = False
        self._timer.start()
        QTimer.singleShot(0, self.check_now)

    def stop(self):
        self._stopped = True
        self._timer.stop()
        self._deadline.stop()
        if self._reply is not None:
            self._reply.abort()

    def check_now(self):
        if self._stopped or self._reply is not None:
            return
        if self.url.scheme() != "https" or self.url.userInfo() or not self.url.host():
            self._set_status("Release policy endpoint is invalid; retaining the previous decision.")
            return
        request = QNetworkRequest(self.url)
        request.setTransferTimeout(5000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setAttribute(QNetworkRequest.Attribute.CookieLoadControlAttribute,
                             QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.CookieSaveControlAttribute,
                             QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                             QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        request.setRawHeader(b"User-Agent", b"ModemDiag-Release-Check")
        request.setRawHeader(b"Accept", b"application/json")
        self._data = bytearray()
        self._oversize = False
        self._reply = self._network.get(request)
        self._reply.setReadBufferSize(MAX_POLICY_BYTES + 1)
        self._reply.readyRead.connect(self._read_ready)
        self._reply.finished.connect(self._finished)
        self._deadline.start(10000)
        self._set_status("Checking release status…")

    def _read_ready(self):
        reply = self._reply
        if reply is None or self._oversize or reply.bytesAvailable() <= 0:
            return
        remaining = MAX_POLICY_BYTES + 1 - len(self._data)
        if remaining > 0:
            chunk = reply.read(remaining)
            # PyQt may return None for an empty/closed failed reply.
            if chunk:
                self._data.extend(bytes(chunk))
        if len(self._data) > MAX_POLICY_BYTES:
            self._oversize = True
            reply.abort()

    def _abort_request(self):
        if self._reply is not None:
            self._reply.abort()

    def _finished(self):
        reply = self._reply
        if reply is None:
            return
        self._deadline.stop()
        self._read_ready()
        self._reply = None
        try:
            if self._stopped:
                return
            if (self._oversize or reply.error() != QNetworkReply.NetworkError.NoError
                    or reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200):
                self._set_status("Release check unavailable; retaining the previous decision. Offline use is allowed unless already revoked.")
                return
            self.apply_policy(bytes(self._data))
        finally:
            reply.deleteLater()

    def apply_policy(self, raw):
        """Common receipt path, also used by offline integration tests."""
        try:
            changed = self.state.accept(raw)
        except PolicyError:
            self._set_status("Release policy was invalid or stale; retaining the previous decision.")
            return False
        if changed:
            self.state_changed.emit(self.revoked, self.message)
        status = ("This version is no longer supported. See the release notice." if self.revoked
                  else self.update_message if self.update_available
                  else "This release is supported by the current signed policy.")
        if self.state.persistence_error:
            status += " Could not save the policy cache; the decision applies for this session."
        self._set_status(status)
        return True
