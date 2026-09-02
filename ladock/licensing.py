"""
LADOCK licensing — the one place the free-period deadline is defined.

Both front-ends import from here. Keeping the date in two files invited them
to drift apart, and a licence that expires on different days depending on
which command you ran is worse than no licence at all.

What this module can and cannot do is worth stating plainly, because it is
easy to assume otherwise:

  * It stops the software on a machine whose clock has passed the deadline,
    and it notices a clock wound backwards.
  * It does NOT resist someone editing this file. LADOCK ships as readable
    Python; `pip install ladock` puts this source on disk, and one line of it
    is the deadline. Frozen builds only add a decoding step. Real enforcement
    needs server-side activation, which costs offline use.

So treat this as a deadline that holds for ordinary users, not as a lock.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

# Free for everyone, no key required, up to and including this date.
FREE_UNTIL = _dt.date(2029, 12, 31)

# Ed25519 public key for licence verification. Safe to ship: it can check a
# signature but cannot produce one. The matching private key never leaves the
# owner's machine, which is the whole point of the change away from HMAC —
# that scheme put the signing secret in every copy of the software, so any
# user could mint themselves a perpetual commercial licence.
LICENSE_PUBLIC_KEY_B64 = "VEBpxmHndDWvQon3YKzcuMZv7Y6nl45mOy+0QQb5mUg="


def verify_signature(payload: bytes, signature: bytes) -> bool:
    """True if `signature` was produced by the owner's private key."""
    try:
        import base64
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(LICENSE_PUBLIC_KEY_B64))
        try:
            key.verify(signature, payload)
            return True
        except InvalidSignature:
            return False
    except Exception:
        # A missing or broken crypto stack must not silently accept keys.
        return False

# Highest date the software has ever seen. Compared against the clock so that
# winding it back does not hand out extra time.
_STATE_DIR = Path(os.environ.get("LADOCK_HOME", Path.home() / ".ladock"))
_SEEN_FILE = _STATE_DIR / "last_seen"

# A clock can legitimately be a little wrong — a flat CMOS battery, a laptop
# resuming with a stale RTC, a VM restored from a snapshot. Only a jump larger
# than this counts as winding the clock back.
_CLOCK_SLACK = _dt.timedelta(days=2)


def _read_last_seen() -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(_SEEN_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _record_today(today: _dt.date) -> None:
    """Remember today if it is the furthest we have got. Never fatal."""
    try:
        previous = _read_last_seen()
        if previous is None or today > previous:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            _SEEN_FILE.write_text(today.isoformat())
    except OSError:
        pass


def effective_today() -> _dt.date:
    """Today, or the furthest date seen if the clock has been wound back."""
    today = _dt.date.today()
    previous = _read_last_seen()
    if previous is not None and today < previous - _CLOCK_SLACK:
        return previous
    _record_today(today)
    return today


def expired() -> bool:
    return effective_today() > FREE_UNTIL


def days_remaining() -> int:
    return (FREE_UNTIL - effective_today()).days


def notice() -> str:
    """One plain-text line, for whichever front-end wants to show it."""
    if expired():
        return (f"LADOCK academic free licence ended on {FREE_UNTIL.isoformat()} — "
                "contact laode_aman@ung.ac.id to continue.")
    return (f"LADOCK academic free licence — active until {FREE_UNTIL.isoformat()}, "
            "no key needed.")
