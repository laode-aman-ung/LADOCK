"""Regression tests for the LADOCK CLI agent.

Plain ``unittest`` so it runs with no extra dependency::

    python -m unittest discover -s tests -v

Every test here pins down a bug that was actually shipped; the docstrings name
the failure so a future change that reintroduces it fails loudly.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ladock.cli import agent


def _write(tmp: Path, name: str, text: str) -> str:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class MlsdCombinatoricsTest(unittest.TestCase):
    """MLSD group counting used to enumerate every tuple just to count them."""

    def test_counts_match_itertools_for_small_inputs(self):
        import itertools

        for n, k in ((5, 2), (6, 3), (4, 4), (3, 1)):
            for arrangement, gen in (("combination", itertools.combinations),
                                     ("permutation", itertools.permutations)):
                self.assertEqual(
                    agent.mlsd_group_count(n, k, arrangement),
                    sum(1 for _ in gen(range(n), k)),
                    f"n={n} k={k} {arrangement}")

    def test_k_greater_than_n_is_zero(self):
        self.assertEqual(agent.mlsd_group_count(2, 3, "combination"), 0)
        self.assertEqual(agent.mlsd_group_count(0, 1, "permutation"), 0)

    def test_huge_library_counts_instantly(self):
        """20k ligands taken 3 at a time is ~1.3e12 groups: must not enumerate."""
        start = time.monotonic()
        count = agent.mlsd_group_count(20000, 3, "combination")
        self.assertEqual(count, math.comb(20000, 3))
        self.assertLess(time.monotonic() - start, 0.5)

    def test_groups_are_lazy(self):
        groups = agent.mlsd_groups(list(range(50000)), 3, "combination")
        self.assertEqual(next(iter(groups)), (0, 1, 2))   # no full materialisation


class SmilesRowsTest(unittest.TestCase):
    """A CSV without a header row used to lose its first molecule."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_headerless_csv_keeps_first_row(self):
        path = _write(self.tmp, "lib.csv", "CCO,ethanol\nCCC,propane\n")
        rows = list(agent._iter_smiles_rows(path))
        self.assertEqual([smiles for _name, smiles in rows], ["CCO", "CCC"])

    def test_header_row_is_consumed_and_used(self):
        path = _write(self.tmp, "lib.csv", "name,smiles\nethanol,CCO\npropane,CCC\n")
        rows = list(agent._iter_smiles_rows(path))
        self.assertEqual(rows, [("ethanol", "CCO"), ("propane", "CCC")])

    def test_headerless_tsv_keeps_first_row(self):
        path = _write(self.tmp, "lib.tsv", "CCO\tethanol\nCCC\tpropane\n")
        self.assertEqual(len(list(agent._iter_smiles_rows(path))), 2)

    def test_chembl_export_columns_are_understood(self):
        """A real ChEMBL dump names nothing "name" or "smiles" exactly; falling
        back to row_1, row_2, … makes the results table unmappable to the input."""
        path = _write(self.tmp, "chembl.csv",
                      "molecule_chembl_id,canonical_smiles,standard_value,pref_name\n"
                      "CHEMBL2325101,Clc1ccccc1,35900.0,Receptor tyrosine kinase\n"
                      "CHEMBL2325100,Cc1ccccc1,45300.0,Receptor tyrosine kinase\n")
        self.assertEqual(list(agent._iter_smiles_rows(path)),
                         [("CHEMBL2325101", "Clc1ccccc1"),
                          ("CHEMBL2325100", "Cc1ccccc1")])

    def test_identifier_column_wins_over_a_shared_name(self):
        lower = ["molecule_chembl_id", "canonical_smiles", "pref_name"]
        self.assertEqual(agent._name_column(lower, smiles_idx=1), 0)

    def test_name_column_never_returns_the_smiles_column(self):
        self.assertEqual(agent._name_column(["smiles"], smiles_idx=0), -1)

    def test_library_count_matches_rows_actually_read(self):
        """The MLSD gate counts molecules; it must not assume a header exists."""
        for name, text in (("headed.csv", "smiles,name\nCCO,a\nCCC,b\n"),
                           ("bare.csv", "CCO,a\nCCC,b\n")):
            path = _write(self.tmp, name, text)
            self.assertEqual(agent._count_library_molecules([path]),
                             len(list(agent._iter_smiles_rows(path))), name)


# A minimal complex: protein chain A, a 2-atom DNA residue on its OWN chain C
# (so it cannot be swept in by the protein-chain rule), one 3-atom and one
# 1-atom water, a zinc, and a 2-atom native ligand on chain B.
_PDB = """\
ATOM      1  N   ALA A   1      11.000  11.000  11.000  1.00  0.00           N
ATOM      2  CA  ALA A   1      12.000  11.000  11.000  1.00  0.00           C
ATOM      3  N   GLY A   2      13.000  11.000  11.000  1.00  0.00           N
ATOM      4  P    DA C  10      20.000  20.000  20.000  1.00  0.00           P
ATOM      5  O5'  DA C  10      21.000  20.000  20.000  1.00  0.00           O
HETATM    6  O   HOH A 201      30.000  30.000  30.000  1.00  0.00           O
HETATM    7  H1  HOH A 201      30.500  30.000  30.000  1.00  0.00           H
HETATM    8  H2  HOH A 201      30.000  30.500  30.000  1.00  0.00           H
HETATM    9  O   HOH A 202      31.000  30.000  30.000  1.00  0.00           O
HETATM   10 ZN    ZN A 301      40.000  40.000  40.000  1.00  0.00          ZN
HETATM   11  C1  LIG B 401      50.000  50.000  50.000  1.00  0.00           C
HETATM   12  C2  LIG B 401      51.000  50.000  50.000  1.00  0.00           C
END
"""


class ComponentsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.pdb = _write(self.tmp, "complex.pdb", _PDB)
        self.components = agent.parse_pdb_components(self.pdb)

    def _of_type(self, ctype: str) -> list[dict]:
        return [c for c in self.components if c["type"] == ctype]

    def test_water_residues_are_counted_as_residues_not_atoms(self):
        """A 3-atom water used to be reported as 3 residues."""
        water = self._of_type("Water")[0]
        self.assertEqual(water["n_atoms"], 4)      # 3 + 1
        self.assertEqual(water["n_residues"], 2)   # two distinct HOH residues

    def test_nucleic_acid_is_classified_as_other(self):
        other = self._of_type("Other")
        self.assertEqual(len(other), 1)
        self.assertIn("DA", other[0]["resname"])
        self.assertEqual(other[0]["n_atoms"], 2)
        self.assertEqual(other[0]["n_residues"], 1)   # 2 atoms, one residue

    def test_selecting_other_keeps_nucleic_acid_and_excludes_the_ligand(self):
        """'Other' was matched against HETATM, so DNA was dropped and LIG kept."""
        selection = self._of_type("Protein") + self._of_type("Other")
        out = agent.extract_selected_components(
            self.pdb, str(self.tmp / "sel.pdb"), selection)
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn(" DA C", text)              # nucleic acid retained
        self.assertNotIn("LIG B", text)           # native ligand excluded
        self.assertNotIn("HOH", text)
        self.assertNotIn("ZN ", text)

    def test_protein_selection_does_not_sweep_in_non_amino_acids(self):
        """Selecting a protein chain must not silently drag in whatever else
        happens to share an ATOM record — that is what "Other" is for."""
        out = agent.extract_selected_components(
            self.pdb, str(self.tmp / "prot.pdb"), self._of_type("Protein"))
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("ALA A", text)
        self.assertNotIn(" DA C", text)

    def test_selecting_protein_and_metal_excludes_ligand_and_water(self):
        selection = self._of_type("Protein") + self._of_type("Metal Ion")
        out = agent.extract_selected_components(
            self.pdb, str(self.tmp / "sel2.pdb"), selection)
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("ALA A", text)
        self.assertIn("ZN", text)
        self.assertNotIn("LIG B", text)
        self.assertNotIn("HOH", text)

    def test_ligand_selection_is_residue_specific(self):
        ligand = self._of_type("Ligand")[0]
        self.assertEqual((ligand["resname"], ligand["chain"], ligand["n_atoms"]),
                         ("LIG", "B", 2))
        out = agent.extract_selected_components(
            self.pdb, str(self.tmp / "sel3.pdb"), [ligand])
        text = Path(out).read_text(encoding="utf-8")
        self.assertIn("LIG B", text)
        self.assertNotIn("ALA A", text)

    def test_center_of_native_ligand(self):
        self.assertEqual(agent.compute_ligand_center(self.pdb, "LIG", "B", "401"),
                         (50.5, 50.0, 50.0))


class Ad4SeedTest(unittest.TestCase):
    """--seed never reached AutoDock4: its DPF `seed` line takes two values."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_seed_inserted_after_parameter_version(self):
        dpf = _write(self.tmp, "dock.dpf",
                     "autodock_parameter_version 4.2\nga_run 10\n")
        agent._set_dpf_seed(dpf, 42)
        lines = Path(dpf).read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "autodock_parameter_version 4.2")
        self.assertEqual(lines[1], "seed 42 43")
        self.assertIn("ga_run 10", lines)

    def test_existing_seed_line_is_replaced(self):
        dpf = _write(self.tmp, "dock.dpf",
                     "autodock_parameter_version 4.2\nseed pid time\nga_run 10\n")
        agent._set_dpf_seed(dpf, 7)
        text = Path(dpf).read_text(encoding="utf-8")
        self.assertNotIn("seed pid time", text)
        self.assertEqual(text.count("seed "), 1)

    def test_seed_prepended_when_no_version_line(self):
        dpf = _write(self.tmp, "dock.dpf", "ga_run 10\n")
        agent._set_dpf_seed(dpf, 5)
        self.assertEqual(Path(dpf).read_text(encoding="utf-8").splitlines()[0],
                         "seed 5 6")


class VinaSeedTest(unittest.TestCase):
    """An explicit --seed must reach the engine verbatim; an unset one must not.

    `if cfg.seed:` conflated "no seed given" with "seed 0". Vina happens to read
    0 as "choose randomly", so this is about forwarding the user's choice
    faithfully rather than guessing at it.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.captured: list[list[str]] = []

        def fake_run_cmd(cmd, tag, cwd=None, use_wsl_backend=False, wsl_distro="",
                         timeout=None):
            self.captured.append([str(c) for c in cmd])
            out = cmd[cmd.index("--out") + 1]
            Path(out).write_text("REMARK VINA RESULT:  -1.0  0.0  0.0\n",
                                 encoding="utf-8")

        self._real = agent.run_cmd
        agent.run_cmd = fake_run_cmd
        self.addCleanup(lambda: setattr(agent, "run_cmd", self._real))

    def _run(self, seed):
        cfg = agent.DockConfig(receptor="r.pdbqt", ligands=["l.pdbqt"],
                               out_dir=str(self.tmp), center=(0.0, 0.0, 0.0), seed=seed)
        tools = agent.ToolConfig(vina_path="vina", ag4_path="", ad4_path="",
                                 autodockgpu="", pythonsh="", prepare_receptor="",
                                 prepare_ligand="", prepare_gpf="", prepare_dpf="",
                                 prepare_flexreceptor="")
        agent.run_vina(cfg, tools, str(self.tmp), "r.pdbqt", "l.pdbqt", "vina")
        return self.captured[-1]

    def test_seed_zero_is_passed_through(self):
        cmd = self._run(0)
        self.assertIn("--seed", cmd)
        self.assertEqual(cmd[cmd.index("--seed") + 1], "0")

    def test_unset_seed_is_omitted(self):
        self.assertNotIn("--seed", self._run(None))


class WizardControlFlowTest(unittest.TestCase):
    def test_restart_loops_instead_of_recursing(self):
        """'Jalankan docking baru' used to call run_wizard() from inside itself."""
        calls = []
        real = agent._wizard_session

        def fake_session():
            calls.append(len(calls))
            return agent._WIZARD_RESTART if len(calls) < 4 else 0

        agent._wizard_session = fake_session
        self.addCleanup(lambda: setattr(agent, "_wizard_session", real))
        self.assertEqual(agent.run_wizard(), 0)
        self.assertEqual(len(calls), 4)

    def test_eof_on_stdin_exits_cleanly(self):
        """Piped input running out used to raise EOFError out of input()."""
        import builtins

        real_input = builtins.input
        builtins.input = lambda *_a, **_k: (_ for _ in ()).throw(EOFError())
        self.addCleanup(lambda: setattr(builtins, "input", real_input))
        devnull = open(os.devnull, "w", encoding="utf-8")
        self.addCleanup(devnull.close)
        import contextlib

        with contextlib.redirect_stdout(devnull):
            self.assertEqual(agent.run_wizard(), 0)


_PDBQT = """\
REMARK  a two-atom ligand
ATOM      1  C   LIG A   1      50.000  50.000  50.000  1.00  0.00     0.000 C
ATOM      2  O   LIG A   1      51.000  50.000  50.000  1.00  0.00    -0.300 OA
"""


class LigandAtomTypesTest(unittest.TestCase):
    """Grid maps depend on the ligand's atom types — the AD4 cache key."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_types_read_from_the_last_column(self):
        path = _write(self.tmp, "lig.pdbqt", _PDBQT)
        self.assertEqual(agent.ligand_atom_types(path), frozenset({"C", "OA"}))

    def test_same_types_share_a_cache_slug(self):
        a = agent._types_slug(frozenset({"C", "OA", "HD"}))
        b = agent._types_slug(frozenset({"HD", "C", "OA"}))
        self.assertEqual(a, b)
        self.assertNotEqual(a, agent._types_slug(frozenset({"C", "OA"})))

    def test_slug_is_stable_across_processes(self):
        """A salted hash() would rename the cache directory on every run."""
        import subprocess as sp

        code = ("from ladock.cli.agent import _types_slug;"
                "print(_types_slug(frozenset({'C','OA','N','SA','HD','NA','F','Cl','Br'})))")
        first = sp.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
        second = sp.run([sys.executable, "-c", code], capture_output=True, text=True,
                        env={**os.environ, "PYTHONHASHSEED": "1"}).stdout
        self.assertEqual(first.strip(), second.strip())
        self.assertTrue(first.strip())


class GridCacheKeyTest(unittest.TestCase):
    """The on-disk map store is only safe if the key covers everything the maps
    depend on — and nothing they don't."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.rec = _write(self.tmp, "rec.pdbqt", _PDBQT)
        self.lig = _write(self.tmp, "lig.pdbqt", _PDBQT)
        self.cfg = agent.DockConfig(receptor=self.rec, ligands=[self.lig], out_dir=str(self.tmp),
                                    center=(1.0, 2.0, 3.0), size=(20.0, 20.0, 20.0))

    def _key(self, cfg=None, mode="rigid", flex=""):
        return agent.grid_cache_key(cfg or self.cfg, mode, self.rec, self.lig, flex)

    def test_same_inputs_give_the_same_key(self):
        self.assertEqual(self._key(), self._key())

    def test_box_change_invalidates(self):
        other = agent.DockConfig(receptor=self.rec, ligands=[self.lig], out_dir=str(self.tmp),
                                 center=(1.0, 2.0, 3.0), size=(24.0, 20.0, 20.0))
        self.assertNotEqual(self._key(), self._key(other))

    def test_centre_change_invalidates(self):
        other = agent.DockConfig(receptor=self.rec, ligands=[self.lig], out_dir=str(self.tmp),
                                 center=(9.0, 2.0, 3.0), size=(20.0, 20.0, 20.0))
        self.assertNotEqual(self._key(), self._key(other))

    def test_receptor_content_change_invalidates(self):
        """Keyed by content, not by name: same filename, moved atom."""
        before = self._key()
        Path(self.rec).write_text(_PDBQT.replace("50.000", "55.000"), encoding="utf-8")
        self.assertNotEqual(before, self._key())

    def test_flexible_split_invalidates(self):
        flex = _write(self.tmp, "flex.pdbqt", _PDBQT)
        self.assertNotEqual(self._key(), self._key(flex=flex))

    def test_mode_invalidates(self):
        self.assertNotEqual(self._key(mode="rigid"), self._key(mode="flexible"))

    def test_ligand_identity_does_not_invalidate(self):
        """A different ligand with the same atom types must hit the same maps."""
        twin = _write(self.tmp, "twin.pdbqt", _PDBQT.replace("50.000", "12.000"))
        self.assertEqual(
            agent.grid_cache_key(self.cfg, "rigid", self.rec, self.lig),
            agent.grid_cache_key(self.cfg, "rigid", self.rec, twin))

    def test_extra_atom_type_invalidates(self):
        other = _write(self.tmp, "other.pdbqt",
                       _PDBQT + "ATOM      3  N   LIG A   1      52.000  50.000  50.000"
                                "  1.00  0.00    -0.200 N\n")
        self.assertNotEqual(
            agent.grid_cache_key(self.cfg, "rigid", self.rec, self.lig),
            agent.grid_cache_key(self.cfg, "rigid", self.rec, other))

    def _cache_with_stubbed_build(self, store: Path):
        """A _GridCache whose AutoGrid4 step is replaced by writing a fake map set."""
        cache = agent._GridCache(store, self.cfg, None)
        calls: list[str] = []

        def fake_publish(key, rec, lig, flex):
            calls.append(key)
            entry = store / key
            entry.mkdir(parents=True, exist_ok=True)
            (entry / "receptor.maps.fld").write_text("real\n", encoding="utf-8")
            (entry / agent._COMPLETE_MARKER).write_text("ok\n", encoding="utf-8")
            return str(entry / "receptor.maps.fld"), str(entry / "grid.gpf")

        cache._publish = fake_publish
        return cache, calls

    def test_incomplete_cache_entry_is_not_trusted(self):
        """A run killed mid-build must not leave maps a later run would reuse."""
        store = self.tmp / "grids"
        key = agent.grid_cache_key(self.cfg, "rigid", self.rec, self.lig)
        partial = store / key
        partial.mkdir(parents=True)
        (partial / "receptor.maps.fld").write_text("truncated\n", encoding="utf-8")
        self.assertFalse((partial / agent._COMPLETE_MARKER).exists())

        cache, calls = self._cache_with_stubbed_build(store)
        fld, _gpf = cache.materialise("rigid", self.tmp / "dest", self.rec, self.lig)
        self.assertEqual(calls, [key], "partial entry must be rebuilt, not reused")
        self.assertEqual((cache.misses, cache.disk_hits), (1, 0))
        self.assertEqual(Path(fld).read_text(encoding="utf-8"), "real\n")

    def test_complete_cache_entry_is_reused_by_a_later_run(self):
        store = self.tmp / "grids"
        first, calls = self._cache_with_stubbed_build(store)
        first.materialise("rigid", self.tmp / "dest1", self.rec, self.lig)
        self.assertEqual((first.misses, first.disk_hits), (1, 0))

        second, calls2 = self._cache_with_stubbed_build(store)   # a fresh process
        second.materialise("rigid", self.tmp / "dest2", self.rec, self.lig)
        self.assertEqual(calls2, [], "AutoGrid4 must not run again")
        self.assertEqual((second.misses, second.disk_hits), (0, 1))
        self.assertTrue((self.tmp / "dest2" / "receptor.maps.fld").is_file())


class LigandCacheTest(unittest.TestCase):
    """A multi-receptor run must not re-convert the same library per receptor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_ready_pdbqt_is_detected(self):
        good = _write(self.tmp, "good.pdbqt", _PDBQT)
        self.assertTrue(agent._pdbqt_ready(good))

    def test_empty_or_truncated_file_is_rejected(self):
        empty = _write(self.tmp, "empty.pdbqt", "")
        header_only = _write(self.tmp, "part.pdbqt", "REMARK interrupted\n")
        self.assertFalse(agent._pdbqt_ready(empty))
        self.assertFalse(agent._pdbqt_ready(header_only))
        self.assertFalse(agent._pdbqt_ready(str(self.tmp / "missing.pdbqt")))

    def test_conversion_is_skipped_when_the_pdbqt_exists(self):
        calls = []

        def fake_convert(in_path, out_path, *a, **k):
            calls.append(out_path)
            Path(out_path).write_text(_PDBQT, encoding="utf-8")
            return True

        real = agent._convert_file_to_pdbqt
        agent._convert_file_to_pdbqt = fake_convert
        self.addCleanup(lambda: setattr(agent, "_convert_file_to_pdbqt", real))

        src = _write(self.tmp, "lig.pdb", "ATOM      1  C   LIG A   1      0.0 0.0 0.0\n")
        out_dir = str(self.tmp / "ready")
        first = agent.expand_to_pdbqt(src, out_dir, log_fn=None)
        second = agent.expand_to_pdbqt(src, out_dir, log_fn=None)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1, "second pass must reuse the cached PDBQT")


class RunCmdTimeoutTest(unittest.TestCase):
    """A hung engine used to block the whole run: run_cmd had no timeout."""

    def test_silent_hang_is_killed(self):
        start = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            agent.run_cmd([sys.executable, "-c", "import time; time.sleep(30)"],
                          "sleeper", timeout=1)
        self.assertIn("time limit", str(ctx.exception))
        self.assertLess(time.monotonic() - start, 10)

    def test_fast_command_is_unaffected(self):
        agent.run_cmd([sys.executable, "-c", "print('hi')"], "quick", timeout=30)

    def test_failure_still_reports_the_exit_code(self):
        with self.assertRaises(RuntimeError) as ctx:
            agent.run_cmd([sys.executable, "-c", "raise SystemExit(3)"], "boom", timeout=30)
        self.assertIn("exit code 3", str(ctx.exception))


_MOLECULES = ["CCO", "c1ccccc1", "CC(=O)Oc1ccccc1C(=O)O", "[nH]1cccc1",
              "CN1C=NC2=C1C(=O)N(C)C(=O)N2C", "C[C@H](N)C(=O)O", "[Fe+2]",
              "N[C@@H](Cc1ccc(O)cc1)C(O)=O", "Br"]
_NOT_MOLECULES = ["Name", "smiles", "ethanol", "Compound", "aspirin", "ID",
                  "caffeine", "zinc_id", "title", "Molecule 1", "", "   ",
                  "NCC OO", "n/a", "TRUE"]


class SmilesDetectionTest(unittest.TestCase):
    """The old test passed anything containing one of "CcNnOoSFPB([=" — which
    is most English words, so names and headers were docked as molecules."""

    def test_molecules_are_accepted(self):
        for smiles in _MOLECULES:
            self.assertTrue(agent._looks_like_smiles(smiles), smiles)

    def test_names_and_headers_are_rejected(self):
        for text in _NOT_MOLECULES:
            self.assertFalse(agent._looks_like_smiles(text), repr(text))

    def test_fallback_scanner_agrees_without_rdkit(self):
        """The bracket/atom scanner is what runs when RDKit is absent."""
        for smiles in _MOLECULES:
            self.assertTrue(agent._scan_smiles(smiles), smiles)
        for text in _NOT_MOLECULES:
            self.assertFalse(agent._scan_smiles(text.strip()), repr(text))

    def test_unclosed_bracket_is_rejected(self):
        self.assertFalse(agent._scan_smiles("CC[nH"))

    def test_name_column_is_not_mistaken_for_structure(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "lib.csv",
                      "name,smiles\ncaffeine,CN1C=NC2=C1C(=O)N(C)C(=O)N2C\n"
                      "aspirin,CC(=O)Oc1ccccc1C(=O)O\n")
        self.assertEqual(list(agent._iter_smiles_rows(path)),
                         [("caffeine", "CN1C=NC2=C1C(=O)N(C)C(=O)N2C"),
                          ("aspirin", "CC(=O)Oc1ccccc1C(=O)O")])


class MultiReceptorReportTest(unittest.TestCase):
    """A multi-receptor run left its results scattered over N per-receptor CSVs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.rows = [
            {"receptor": "r1.pdb", "ligand": "a", "mode": "rigid", "scoring": "vina",
             "energy": "-7.5", "out_path": "r1/a"},
            {"receptor": "r1.pdb", "ligand": "b", "mode": "rigid", "scoring": "vina",
             "energy": "-9.1", "out_path": "r1/b"},
            {"receptor": "r2.pdb", "ligand": "a", "mode": "rigid", "scoring": "vina",
             "energy": "-10.4", "out_path": "r2/a"},
            {"receptor": "r2.pdb", "ligand": "b", "mode": "rigid", "scoring": "vina",
             "energy": "", "out_path": "r2/b"},
        ]

    def test_ranking_is_best_first_and_unparsable_energies_sort_last(self):
        ranked = agent.rank_rows(self.rows)
        self.assertEqual([r["energy"] for r in ranked], ["-10.4", "-9.1", "-7.5", ""])

    def test_best_per_receptor(self):
        best = agent.rank_rows(self.rows, per_receptor=True)
        self.assertEqual([(r["receptor"], r["energy"]) for r in best],
                         [("r2.pdb", "-10.4"), ("r1.pdb", "-9.1")])

    def test_combined_csv_carries_the_receptor_column(self):
        agent.write_results(self.tmp / "results_all.csv", self.rows)
        header = (self.tmp / "results_all.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(header.startswith("receptor,ligand,"))

    def test_single_receptor_csv_has_no_receptor_column(self):
        rows = [{k: v for k, v in r.items() if k != "receptor"} for r in self.rows]
        agent.write_results(self.tmp / "results.csv", rows)
        header = (self.tmp / "results.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(header.startswith("ligand,"))

    def test_report_writes_all_three_artefacts(self):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()) as out:
            agent._report_multi_receptor(self.tmp, self.rows, [("r3.pdb", "no native ligand")])
        for name in ("results_all.csv", "ranking.csv", "multi_receptor.summary.json"):
            self.assertTrue((self.tmp / name).is_file(), name)
        summary = json.loads((self.tmp / "multi_receptor.summary.json").read_text())
        self.assertEqual(summary["n_results"], 4)
        self.assertEqual(summary["receptors_docked"], ["r1.pdb", "r2.pdb"])
        self.assertEqual(summary["receptors_skipped"],
                         [{"receptor": "r3.pdb", "error": "no native ligand"}])
        self.assertIn("r3.pdb", out.getvalue())      # skipped receptors are surfaced

    def test_report_survives_a_run_where_every_receptor_failed(self):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            agent._report_multi_receptor(self.tmp, [], [("r1.pdb", "boom")])
        summary = json.loads((self.tmp / "multi_receptor.summary.json").read_text())
        self.assertEqual(summary["n_results"], 0)
        self.assertFalse((self.tmp / "ranking.csv").exists())


class UniqueLabelsTest(unittest.TestCase):
    """Two ligands with the same name used to share one output directory, so the
    second silently overwrote the first's poses."""

    def test_duplicates_are_disambiguated(self):
        self.assertEqual(agent.unique_labels(["mol", "mol", "mol"]),
                         ["mol", "mol_2", "mol_3"])

    def test_distinct_names_are_untouched(self):
        self.assertEqual(agent.unique_labels(["a", "b"]), ["a", "b"])

    def test_collision_with_an_existing_suffix_is_resolved(self):
        self.assertEqual(agent.unique_labels(["m", "m_2", "m"]), ["m", "m_2", "m_3"])

    def test_unsafe_characters_are_sanitised(self):
        self.assertEqual(agent.unique_labels(["a/b", "a b"]), ["a_b", "a_b_2"])


_ADFR_POSES = """\
MODEL 1
USER: ADFR SOLUTION from run 3
USER: SCORE 12.219520 LL:  -0.881 LR: -11.339 RR:   0.000 FEB:  -9.847
USER: RMSD -1
ATOM      1  C   LIG A   1      50.000  50.000  50.000  1.00  0.00     0.000 C
ENDMDL
"""


class AdfrEngineTest(unittest.TestCase):
    """ADFR/AGFR as a real engine, not just a green badge."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_adfr_is_a_recognised_scoring_function(self):
        self.assertIn("adfr", agent.ALL_SCORING)
        self.assertIn("adfr", agent.ADFR_SCORING)
        self.assertFalse(agent.ADFR_SCORING & agent.VINA_SCORING)
        self.assertFalse(agent.ADFR_SCORING & agent.AD4_SCORING)

    def test_energy_comes_from_the_feb_field(self):
        """ADFR reports the free energy of binding as FEB on the pose's USER line;
        the summary table only carries one decimal place."""
        path = _write(self.tmp, "poses.pdbqt", _ADFR_POSES)
        self.assertEqual(agent.parse_result(path, "adfr")["energy"], "-9.847")

    def test_missing_feb_is_reported_as_blank_not_a_crash(self):
        path = _write(self.tmp, "empty.pdbqt", "MODEL 1\nENDMDL\n")
        self.assertEqual(agent.parse_result(path, "adfr"),
                         {"energy": "", "rmsd_lb": "", "rmsd_ub": ""})

    def test_agfr_flex_syntax_groups_by_chain(self):
        """AGFR wants A:ILE10,VAL32;B:SER48 — not prepare_flexreceptor4's format."""
        self.assertEqual(
            agent._agfr_flex_spec(["A:ILE:10", "A:VAL:32", "B:SER:48"]),
            "A:ILE10,VAL32;B:SER48")

    def test_agfr_flex_syntax_ignores_malformed_entries(self):
        self.assertEqual(agent._agfr_flex_spec(["A:ILE:10", "rubbish"]), "A:ILE10")
        self.assertEqual(agent._agfr_flex_spec([]), "")

    def test_missing_adfrsuite_fails_with_an_actionable_message(self):
        cfg = agent.DockConfig(receptor="r.pdbqt", ligands=["l.pdbqt"],
                               out_dir=str(self.tmp), center=(0.0, 0.0, 0.0),
                               scoring=["adfr"])
        tools = agent.ToolConfig(vina_path="vina", ag4_path="", ad4_path="",
                                 autodockgpu="", pythonsh="", prepare_receptor="",
                                 prepare_ligand="", prepare_gpf="", prepare_dpf="",
                                 prepare_flexreceptor="",
                                 adfr_path="/nope/adfr", agfr_path="/nope/agfr")
        with self.assertRaises(RuntimeError) as ctx:
            agent.validate_rules(cfg, tools)
        message = str(ctx.exception)
        self.assertIn("ADFRsuite", message)
        self.assertIn("--adfrsuite", message)

    def test_unknown_scoring_still_rejected(self):
        cfg = agent.DockConfig(receptor="r", ligands=["l"], out_dir=str(self.tmp),
                               center=(0.0, 0.0, 0.0), scoring=["nonsense"])
        tools = agent.ToolConfig(vina_path="vina", ag4_path="", ad4_path="",
                                 autodockgpu="", pythonsh="", prepare_receptor="",
                                 prepare_ligand="", prepare_gpf="", prepare_dpf="",
                                 prepare_flexreceptor="")
        with self.assertRaises(RuntimeError):
            agent.validate_rules(cfg, tools)


class GridPointsTest(unittest.TestCase):
    def test_grid_points_are_even_and_at_least_two(self):
        for size, spacing in ((20.0, 0.375), (22.5, 0.375), (0.1, 0.375), (30.0, 0.5)):
            n = agent._grid_points(size, spacing)
            self.assertEqual(n % 2, 0, f"{size}/{spacing} -> {n}")
            self.assertGreaterEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
