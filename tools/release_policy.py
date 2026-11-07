#!/usr/bin/env python3
"""Offline maintainer tool. Never uploads or pushes policy or key material."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from modemdiag.core.release_policy import canonical_json, verify_policy, PolicyError


def private_path(value):
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("Private signing keys must be outside the repository")
    return path


def generate_key(path):
    path = private_path(path)
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Exclusive creation prevents accidentally replacing an existing signing key.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                           serialization.PublicFormat.Raw)
    return base64.b64encode(public).decode("ascii")


def sign_policy(key_path, output, sequence, versions=(), revoke_all=False, message="", days=90,
                latest_version="", update_message=""):
    if not 1 <= days <= 365:
        raise ValueError("Validity must be between 1 and 365 days")
    key_path = private_path(key_path)
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("An Ed25519 signing key is required")
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")
    output = Path(output)
    if output.exists():
        previous = verify_policy(output.read_bytes(), public)
        if sequence <= previous.sequence:
            raise ValueError(f"Use a sequence greater than {previous.sequence}")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    data = {
        "app": "ModemDiag", "schema": 1, "sequence": sequence,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "revoke_all": revoke_all, "revoked_versions": sorted(set(versions)), "message": message,
    }
    if latest_version or update_message:
        data.update(latest_version=latest_version, update_message=update_message)
    payload = canonical_json(data)
    raw = canonical_json({"payload": base64.b64encode(payload).decode("ascii"),
                          "signature": base64.b64encode(key.sign(payload)).decode("ascii")}) + b"\n"
    verify_policy(raw, public)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, mode="wb", delete=False) as stream:
            temporary = stream.name
            stream.write(raw)
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    generate = commands.add_parser("keygen", help="Create a private key outside the repository")
    generate.add_argument("--key", required=True, help="Private PEM file to create; keep a secure backup")
    sign = commands.add_parser("sign", help="Write a signed policy; empty revocation list restores access")
    sign.add_argument("--key", required=True)
    sign.add_argument("--output", default=str(ROOT / "release-policy.json"))
    sign.add_argument("--sequence", required=True, type=int)
    sign.add_argument("--revoke", action="append", default=[], metavar="VERSION")
    sign.add_argument("--revoke-all", action="store_true")
    sign.add_argument("--message", default="")
    sign.add_argument("--valid-days", type=int, default=90)
    sign.add_argument("--latest-version", default="", metavar="X.Y[.Z]",
                      help="Announce a published stable release without blocking older versions")
    sign.add_argument("--update-message", default="", help="Optional text for the update notice")
    args = parser.parse_args()
    try:
        if args.action == "keygen":
            public = generate_key(args.key)
            print("Private signing key created outside the repository. Keep a secure backup.")
            print("Public verification key (base64): " + public)
        else:
            public = sign_policy(args.key, args.output, args.sequence, args.revoke,
                                 args.revoke_all, args.message, args.valid_days,
                                 args.latest_version, args.update_message)
            print(f"Signed policy written to {args.output}; nothing was published.")
            print("Public verification key (base64): " + public)
    except (OSError, ValueError, PolicyError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
