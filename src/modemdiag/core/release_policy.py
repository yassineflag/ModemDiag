"""Signed release status. Unavailable policies allow offline operation.

Only an authenticated, newer policy can change an accepted release decision.
This is an application control, not tamper resistance against modified clients.
"""
import base64
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


POLICY_URL = "https://raw.githubusercontent.com/yassineflag/ModemDiag/main/release-policy.json"
# Public verification key only. The private signing key must remain outside the repository.
PUBLIC_KEY_B64 = "T+iTmFxyphPhm6CGQOLiUYvJu+WxvK3AcqVDWhNecmU="
CHECK_INTERVAL_MS = 5 * 60 * 1000
MAX_POLICY_BYTES = 64 * 1024
DEFAULT_MESSAGE = "This version is no longer supported. Please update ModemDiag."
VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}\Z")
STABLE_VERSION_PATTERN = re.compile(r"(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})(?:\.(0|[1-9]\d{0,8}))?\Z")


class PolicyError(ValueError):
    pass


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("Duplicate JSON key")
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=_invalid_number)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PolicyError("Invalid policy JSON") from exc


def _invalid_number(_value):
    raise PolicyError("Invalid number")


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise PolicyError("Policy timestamps must be UTC")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PolicyError("Invalid policy timestamp") from exc


@dataclass(frozen=True)
class ReleasePolicy:
    sequence: int
    issued_at: datetime
    expires_at: datetime
    revoke_all: bool
    revoked_versions: tuple[str, ...]
    message: str
    payload: bytes
    latest_version: str = ""
    update_message: str = ""

    def revokes(self, version: str) -> bool:
        return self.revoke_all or version in self.revoked_versions

    def offers_update(self, version: str) -> bool:
        current = STABLE_VERSION_PATTERN.fullmatch(version)
        latest = STABLE_VERSION_PATTERN.fullmatch(self.latest_version)
        return bool(current and latest and not self.revokes(self.latest_version)
                    and tuple(int(part or 0) for part in latest.groups())
                    > tuple(int(part or 0) for part in current.groups()))


def verify_policy(raw: bytes, public_key_b64: str) -> ReleasePolicy:
    """Verify signature and strict schema; freshness is checked on network receipt."""
    if not isinstance(raw, bytes) or len(raw) > MAX_POLICY_BYTES:
        raise PolicyError("Policy exceeds size limit")
    envelope = _json(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise PolicyError("Invalid policy envelope")
    try:
        payload = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        key = base64.b64decode(public_key_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(signature, payload)
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise PolicyError("Policy signature could not be verified") from exc
    data = _json(payload)
    fields = {"app", "schema", "sequence", "issued_at", "expires_at", "revoke_all",
              "revoked_versions", "message"}
    optional = {"latest_version", "update_message"}
    if not isinstance(data, dict) or not fields <= set(data) or set(data) - fields - optional:
        raise PolicyError("Invalid policy fields")
    if data["app"] != "ModemDiag" or type(data["schema"]) is not int or data["schema"] != 1:
        raise PolicyError("Unsupported application or policy schema")
    if type(data["sequence"]) is not int or not 1 <= data["sequence"] <= 2**63 - 1:
        raise PolicyError("Invalid policy sequence")
    if type(data["revoke_all"]) is not bool:
        raise PolicyError("Invalid revoke_all flag")
    versions = data["revoked_versions"]
    if (not isinstance(versions, list) or len(versions) > 1000
            or any(not isinstance(v, str) or not VERSION_PATTERN.fullmatch(v) for v in versions)
            or len(set(versions)) != len(versions)):
        raise PolicyError("Invalid release list")
    if not isinstance(data["message"], str) or len(data["message"]) > 500:
        raise PolicyError("Invalid release message")
    latest = data.get("latest_version", "")
    update_message = data.get("update_message", "")
    if (not isinstance(latest, str) or (latest and not STABLE_VERSION_PATTERN.fullmatch(latest))
            or not isinstance(update_message, str) or len(update_message) > 500
            or (update_message and not latest)):
        raise PolicyError("Invalid update announcement")
    issued, expires = _timestamp(data["issued_at"]), _timestamp(data["expires_at"])
    if expires <= issued:
        raise PolicyError("Invalid policy validity period")
    if payload != canonical_json(data):
        raise PolicyError("Policy payload must use canonical JSON")
    return ReleasePolicy(data["sequence"], issued, expires, data["revoke_all"], tuple(versions),
                         data["message"] or DEFAULT_MESSAGE, payload, latest, update_message)


class PolicyState:
    """Persist the last authenticated policy; expiration never lifts a cached revocation."""

    def __init__(self, version: str, cache_path: Path, public_key_b64: str):
        self.version = version
        self.cache_path = Path(cache_path)
        self.public_key_b64 = public_key_b64
        self.policy: ReleasePolicy | None = None
        self.persistence_error = ""
        self.cache_error = ""
        try:
            with self.cache_path.open("rb") as stream:
                raw = stream.read(MAX_POLICY_BYTES + 1)
            # A cache is reauthenticated, including when offline or expired. Only
            # network policies must be fresh, so clock changes cannot unlock it.
            self.policy = verify_policy(raw, self.public_key_b64)
        except FileNotFoundError:
            pass
        except (OSError, PolicyError) as exc:
            self.cache_error = str(exc)

    @property
    def revoked(self) -> bool:
        return self.policy is not None and self.policy.revokes(self.version)

    @property
    def message(self) -> str:
        return self.policy.message if self.revoked else ""

    @property
    def update_available(self) -> bool:
        return self.policy is not None and self.policy.offers_update(self.version)

    @property
    def update_message(self) -> str:
        if not self.update_available:
            return ""
        text = f"An update is available: ModemDiag {self.policy.latest_version}."
        return text + (" " + self.policy.update_message if self.policy.update_message else "")

    def _decision(self):
        return self.revoked, self.message, self.update_message

    def accept(self, raw: bytes, now: datetime | None = None) -> bool:
        policy = verify_policy(raw, self.public_key_b64)
        now = now or datetime.now(timezone.utc)
        if policy.issued_at > now + timedelta(minutes=5):
            raise PolicyError("Policy is not valid yet")
        if policy.expires_at <= now:
            raise PolicyError("Policy has expired")
        if self.policy is not None:
            if policy.sequence < self.policy.sequence:
                raise PolicyError("Older policy rejected")
            if policy.sequence == self.policy.sequence and policy.payload != self.policy.payload:
                raise PolicyError("Conflicting policy sequence rejected")
        before = self._decision()
        # Apply an authentic revocation even if the disk is full; report that it
        # cannot be remembered reliably across restarts in that situation.
        self.policy = policy
        self.persistence_error = ""
        self.cache_error = ""
        temporary = None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.cache_path.parent, mode="wb", delete=False) as f:
                temporary = f.name
                f.write(raw)
            os.replace(temporary, self.cache_path)
        except OSError as exc:
            self.persistence_error = str(exc)
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return before != self._decision()
