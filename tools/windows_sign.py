#!/usr/bin/env python3
"""Sign the Windows application using a certificate already in the Windows store.

No private keys or passwords are accepted by this script or stored in the repo.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

try:
    from tools.release_bundle import ROOT, read_bundle, record_bundle
except ModuleNotFoundError:
    from release_bundle import ROOT, read_bundle, record_bundle


def sign_build(root=ROOT, environment=None):
    environment = os.environ if environment is None else environment
    thumbprint = re.sub(r"\s", "", environment.get("MODEMDIAG_SIGN_CERT_SHA1", ""))
    if not thumbprint:
        if environment.get("MODEMDIAG_REQUIRE_SIGNING") == "1":
            raise ValueError("Signing is required. Set MODEMDIAG_SIGN_CERT_SHA1 to a trusted code-signing certificate in the Windows certificate store.")
        print("UNSIGNED build: no code-signing identity configured. Windows SmartScreen may warn users.")
        return False
    if sys.platform != "win32":
        raise ValueError("Windows Authenticode signing must run on Windows.")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", thumbprint):
        raise ValueError("MODEMDIAG_SIGN_CERT_SHA1 must be a 40-digit certificate thumbprint.")
    timestamp = environment.get("MODEMDIAG_TIMESTAMP_URL", "")
    parts = urlsplit(timestamp)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Set MODEMDIAG_TIMESTAMP_URL to your certificate provider's RFC 3161 timestamp URL.")
    tool = environment.get("MODEMDIAG_SIGNTOOL") or shutil.which("signtool")
    if not tool:
        raise ValueError("Install Windows SDK SignTool and put it on PATH, or set MODEMDIAG_SIGNTOOL.")
    metadata = Path(root) / "local-ignored/build" / ("release" if environment.get("MODEMDIAG_RELEASE", "1") == "1" else "notices")
    dist = Path(root) / "local-ignored/dist"
    executable, _, folder = read_bundle(metadata, dist)
    subprocess.run([tool, "sign", "/sha1", thumbprint, "/s", "My", "/fd", "SHA256",
                    "/tr", timestamp, "/td", "SHA256", "/d", "ModemDiag", str(executable)], check=True)
    subprocess.run([tool, "verify", "/pa", "/all", "/v", str(executable)], check=True)
    # Release checksums must describe the verified signed bytes, not the unsigned build.
    record_bundle(metadata, dist, executable, folder)
    print("Verified Authenticode signature and updated application checksums.")
    return True


if __name__ == "__main__":
    try:
        sign_build()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Signing failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
