import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from modemdiag.core.release_policy import canonical_json


def public_key(key):
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")


def policy_document(key, sequence=1, versions=(), revoke_all=False, now=None, **changes):
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    data = {
        "app": "ModemDiag", "schema": 1, "sequence": sequence,
        "issued_at": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "revoke_all": revoke_all, "revoked_versions": list(versions), "message": "Test release status",
    }
    data.update(changes)
    payload = canonical_json(data)
    return canonical_json({"payload": base64.b64encode(payload).decode("ascii"),
                           "signature": base64.b64encode(key.sign(payload)).decode("ascii")})
