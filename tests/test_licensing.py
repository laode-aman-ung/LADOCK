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
