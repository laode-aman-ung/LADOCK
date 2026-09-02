"""Regression tests for flexible-receptor handling in LADOCK Desktop.

The first tests to cover ``ladock/desktop/``. They exist because flexible
docking in the GUI never worked: the panels passed their own display string
straight to prepare_flexreceptor4.py, which rejected it, wrote an empty flex
file, and let Vina dock rigid without a word.

Only pure functions are exercised, so no Qt application is created -- but the
module they live in imports PySide6 at import time, hence the skip.

    python -m pytest tests/test_desktop_flexres.py -v
"""
from __future__ import annotations

import unittest

try:
    from ladock.desktop.gui.panels.native_redocking_panel import (
        drop_non_rotatable, flexres_spec, NON_ROTATABLE_SIDE_CHAINS,
    )
    HAVE_QT = True
except Exception:                                          # noqa: BLE001
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DesktopFlexResSpecTest(unittest.TestCase):
    """The GUI used to send prepare_flexreceptor4.py its own list format,
    "A:MET:49_B:ASN:29", which MGLTools answers with

        no residue found using string  A:MET:49,B:ASN:29

    and an empty flex.pdbqt. The old guard only checked that the file existed,
    so the run continued and Vina, which ignores an empty --flex, docked rigid.
    Flexible mode in Desktop therefore never did anything at all."""

    def test_residue_name_and_number_are_concatenated(self):
        """MET49, not MET:49 -- the colon form is what MGLTools rejected."""
        spec = flexres_spec("/tmp/receptor.pdbqt", ["A:MET:49"])
        self.assertEqual(spec, "receptor:A:MET49")
        self.assertNotIn("MET:49", spec)

    def test_molecule_name_repeats_on_every_chain(self):
        spec = flexres_spec("/tmp/receptor.pdbqt",
                            ["A:MET:49", "B:ASN:29", "B:THR:72"])
        self.assertEqual(spec, "receptor:A:MET49,receptor:B:ASN29_THR72")

    def test_every_comma_group_is_a_complete_triple(self):
        spec = flexres_spec("/tmp/rec.pdbqt", ["A:MET:49", "B:ASN:29"])
        for piece in spec.split(","):
            self.assertEqual(len(piece.split(":")), 3, piece)
            self.assertEqual(piece.split(":")[0], "rec")

    def test_stem_comes_from_the_receptor_filename(self):
        self.assertTrue(
            flexres_spec("/a/b/prepared_target.pdbqt", ["A:MET:49"])
            .startswith("prepared_target:"))

    def test_malformed_entries_are_skipped(self):
        self.assertEqual(
            flexres_spec("/tmp/receptor.pdbqt", ["A:MET:49", "nonsense"]),
            "receptor:A:MET49")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class DesktopNonRotatableTest(unittest.TestCase):
    """agfr refuses an entire job when handed an alanine, while
    prepare_flexreceptor4.py drops it silently."""

    def test_alanine_and_glycine_are_dropped(self):
        keep, dropped = drop_non_rotatable(
            ["A:ALA:37", "A:MET:49", "B:GLY:71", "B:ASN:29"])
        self.assertEqual(keep, ["A:MET:49", "B:ASN:29"])
        self.assertEqual(dropped, ["A:ALA:37", "B:GLY:71"])

    def test_proline_is_kept(self):
        """AGFR supports proline; dropping it would discard real flexibility."""
        self.assertEqual(drop_non_rotatable(["A:PRO:47"]), (["A:PRO:47"], []))

    def test_matches_the_cli_definition(self):
        """Desktop and CLI must agree on what has no rotatable side chain,
        or the same receptor behaves differently in the two front-ends."""
        from ladock.cli import agent
        self.assertEqual(NON_ROTATABLE_SIDE_CHAINS,
                         agent.NON_ROTATABLE_SIDE_CHAINS)

    def test_spec_matches_the_cli_for_the_same_input(self):
        from ladock.cli import agent
        residues = ["A:MET:49", "B:ASN:29", "B:THR:72"]
        self.assertEqual(flexres_spec("/tmp/receptor.pdbqt", residues),
                         agent._flexres_spec("/tmp/receptor.pdbqt", residues))


if __name__ == "__main__":
    unittest.main()
