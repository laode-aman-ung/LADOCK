"""
LADOCK licence key generator — owner only.

Signing needs the Ed25519 private key, which is deliberately NOT part of the
distribution. Keep it out of every repository and back it up somewhere safe:
losing it means no new keys can be issued, and leaking it means anyone can
issue them.

    python tools/generate_license.py

Key location, in order: --key, $LADOCK_SIGNING_KEY, ~/.ladock-signing/license_ed25519.pem
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ladock.desktop.core.license_manager import LicenseType, validate_key  # noqa: E402

_DEFAULT_KEY = Path.home() / ".ladock-signing" / "license_ed25519.pem"


def _load_private_key(explicit: str | None):
    path = Path(explicit or os.environ.get("LADOCK_SIGNING_KEY") or _DEFAULT_KEY)
    if not path.is_file():
        raise SystemExit(
            f"Private signing key not found at {path}.\n"
            "Set --key or $LADOCK_SIGNING_KEY. This file is never distributed;\n"
            "if it is lost, no further keys can be issued for the shipped public key."
        )
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def issue(license_type: str, name: str, email: str,
          expires: str | None, private_key) -> str:
    payload = {
        "type":    license_type,
        "name":    name,
        "email":   email,
        "issued":  date.today().isoformat(),
        "expires": expires,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = base64.urlsafe_b64encode(
        private_key.sign(payload_b64.encode())).decode().rstrip("=")
    return f"LADOCK-{payload_b64}.{signature}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Issue a LADOCK licence key.")
    ap.add_argument("--type", default="ACADEMIC_DISCOUNT",
                    choices=[t.value for t in LicenseType if t != LicenseType.UNLICENSED])
    ap.add_argument("--name", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--years", type=int, default=1,
                    help="validity in years; 0 = perpetual (commercial only)")
    ap.add_argument("--key", help="path to the Ed25519 private key")
    args = ap.parse_args()

    expires = None if args.years == 0 else (
        date.today() + timedelta(days=365 * args.years)).isoformat()

    key = issue(args.type, args.name, args.email, expires,
                _load_private_key(args.key))

    info = validate_key(key)
    if not info.is_valid:
        raise SystemExit(f"Refusing to hand out a key that does not validate: {info.message}")

    print(f"\n  type    : {args.type}")
    print(f"  for     : {args.name} <{args.email}>")
    print(f"  expires : {expires or 'perpetual'}")
    print(f"\n{key}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
