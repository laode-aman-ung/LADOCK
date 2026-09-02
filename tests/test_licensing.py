"""Tests for the licence deadline and the wound-back-clock guard."""

import datetime
import importlib
import os
import tempfile
import unittest


def _fresh(home):
    """Import ladock.licensing with LADOCK_HOME pointed at a clean directory."""
    os.environ["LADOCK_HOME"] = home
    import ladock.licensing as m
    return importlib.reload(m)


class LicenceDeadlineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_home = os.environ.get("LADOCK_HOME")
        self.addCleanup(lambda: os.environ.__setitem__("LADOCK_HOME", self._old_home)
                        if self._old_home else os.environ.pop("LADOCK_HOME", None))
        self.m = _fresh(self._tmp.name)

    def _pin_today(self, y, mo, d):
        real = datetime.date

        class Fake(real):
            @classmethod
            def today(cls):
                return cls(y, mo, d)

        self.m._dt.date = Fake
        self.addCleanup(lambda: setattr(self.m._dt, "date", real))

    def test_free_period_is_the_documented_date(self):
        self.assertEqual(self.m.FREE_UNTIL, datetime.date(2029, 12, 31))

    def test_not_expired_on_the_last_free_day(self):
        self._pin_today(2029, 12, 31)
        self.assertFalse(self.m.expired())

    def test_expired_the_day_after(self):
        self._pin_today(2030, 1, 1)
        self.assertTrue(self.m.expired())

    def test_winding_the_clock_back_does_not_restore_access(self):
        self._pin_today(2030, 6, 1)
        self.assertTrue(self.m.expired())          # records 2030-06-01
        self._pin_today(2027, 1, 1)                # user sets the clock back
        self.assertEqual(self.m.effective_today(), datetime.date(2030, 6, 1))
        self.assertTrue(self.m.expired())

    def test_small_clock_error_is_tolerated(self):
        self._pin_today(2027, 6, 10)
        self.m.effective_today()                   # records 2027-06-10
        self._pin_today(2027, 6, 9)                # one day slow, e.g. stale RTC
        self.assertEqual(self.m.effective_today(), datetime.date(2027, 6, 9))
        self.assertFalse(self.m.expired())

    def test_unreadable_state_file_does_not_crash(self):
        self.m._SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.m._SEEN_FILE.write_text("not a date")
        self._pin_today(2027, 1, 1)
        self.assertEqual(self.m.effective_today(), datetime.date(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()


class KeySignatureTest(unittest.TestCase):
    """The client must be able to verify a key but never to forge one."""

    def setUp(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        import ladock.licensing as lic
        from ladock.desktop.core import license_manager as lm

        self.lic, self.lm, self.b64 = lic, lm, base64
        self.priv = Ed25519PrivateKey.generate()
        pub = self.priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self._old = lic.LICENSE_PUBLIC_KEY_B64
        lic.LICENSE_PUBLIC_KEY_B64 = base64.b64encode(pub).decode()
        self.addCleanup(lambda: setattr(lic, "LICENSE_PUBLIC_KEY_B64", self._old))

    def _issue(self, expires="2099-01-01", ltype="COMMERCIAL"):
        import json
        payload = {"type": ltype, "name": "T", "email": "t@ung.ac.id",
                   "issued": "2026-01-01", "expires": expires}
        pb = self.b64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()).decode()
        sig = self.b64.urlsafe_b64encode(self.priv.sign(pb.encode())).decode().rstrip("=")
        return f"LADOCK-{pb}.{sig}"

    def test_a_properly_signed_key_validates(self):
        self.assertTrue(self.lm.validate_key(self._issue()).is_valid)

    def test_tampered_payload_is_rejected(self):
        key = self._issue()
        head, sig = key.rsplit(".", 1)
        forged = head[:-4] + "AAAA" + "." + sig
        self.assertFalse(self.lm.validate_key(forged).is_valid)

    def test_key_signed_by_a_different_holder_is_rejected(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        attacker, self.priv = self.priv, Ed25519PrivateKey.generate()
        forged = self._issue()          # signed with the attacker's key
        self.priv = attacker
        self.assertFalse(self.lm.validate_key(forged).is_valid)

    def test_retired_hmac_keys_are_refused_with_an_explanation(self):
        key = self._issue()
        head, _ = key.rsplit(".", 1)
        old_style = head + "." + "a" * 32
        info = self.lm.validate_key(old_style)
        self.assertFalse(info.is_valid)
        self.assertIn("retired", info.message.lower())

    def test_client_cannot_issue_keys(self):
        with self.assertRaises(RuntimeError):
            self.lm.generate_key("COMMERCIAL", "T", "t@ung.ac.id")
