"""Native, cross-platform PDBQT preparation via Meeko + RDKit.

This is the "Path B" replacement for MGLTools' ``prepare_receptor4.py`` /
``prepare_ligand4.py``.  Everything here runs inside the application's own
Python interpreter (no WSL, no bundled Linux MGLTools), so receptor and
ligand PDBQT files can be produced identically on Windows, Linux and macOS.

Design notes
------------
* ``mk_prepare_receptor`` reads a *protein-only* PDB (the component filter
  strips HETATM/water before we get here) and writes a rigid receptor PDBQT.
* ``mk_prepare_ligand`` only accepts ``sdf``/``mol2``/``mol`` and requires
  *explicit* hydrogens, so ligands are first normalised through RDKit
  (add Hs, embed 3D if missing) and written to a temporary SDF.  PDB ligands
  (e.g. a native ligand carved out of a crystal structure) are routed through
  OpenBabel first when available, because RDKit's PDB bond perception is
  unreliable without CONECT records.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile


# --------------------------------------------------------------------------- #
# Availability probes
# --------------------------------------------------------------------------- #
def meeko_available() -> bool:
    try:
        import meeko  # noqa: F401
        return True
    except Exception:
        return False


def rdkit_available() -> bool:
    try:
        import rdkit  # noqa: F401
        return True
    except Exception:
        return False


def obabel_available() -> bool:
    return bool(shutil.which("obabel"))


# --------------------------------------------------------------------------- #
# Subprocess helper (Meeko always runs with the app's own interpreter — native)
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], log_fn, cwd: str | None = None) -> int:
    log_fn(f"  $ {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    except FileNotFoundError:
        log_fn(f"  Executable not found: {cmd[0]}")
        return 127
    if r.stdout and r.stdout.strip():
        log_fn(r.stdout.strip())
    if r.stderr and r.stderr.strip():
        log_fn(r.stderr.strip())
    return r.returncode


def _meeko_cmd(module: str) -> list[str]:
    """Invoke a Meeko CLI as a module so it works cross-platform without
    relying on console-script shims being on PATH."""
    return [sys.executable, "-m", f"meeko.cli.{module}"]


# --------------------------------------------------------------------------- #
# Receptor
# --------------------------------------------------------------------------- #
def native_prepare_receptor(in_pdb: str, out_pdbqt: str, log_fn) -> bool:
    """Protein PDB -> receptor PDBQT via Meeko. Returns True on success."""
    if not meeko_available():
        log_fn("  Meeko not available — cannot prepare receptor natively.")
        return False
    cmd = _meeko_cmd("mk_prepare_receptor") + [
        "--read_pdb", in_pdb,
        "-p", out_pdbqt,
        "--allow_bad_res",
    ]
    _run(cmd, log_fn)
    return os.path.isfile(out_pdbqt)


# --------------------------------------------------------------------------- #
# Ligand
# --------------------------------------------------------------------------- #
def _rdkit_load(path: str, log_fn):
    """Load a ligand into an RDKit Mol from common formats. Returns Mol|None."""
    from rdkit import Chem

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".sdf":
            for m in Chem.SDMolSupplier(path, removeHs=False, sanitize=True):
                if m is not None:
                    return m
            return None
        if ext in (".mol", ".mdl"):
            return Chem.MolFromMolFile(path, removeHs=False, sanitize=True)
        if ext == ".mol2":
            return Chem.MolFromMol2File(path, removeHs=False, sanitize=True)
        if ext in (".pdb", ".ent"):
            return Chem.MolFromPDBFile(path, removeHs=False, sanitize=True)
        if ext in (".smi", ".smiles"):
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                token = fh.readline().split()
            return Chem.MolFromSmiles(token[0]) if token else None
    except Exception as exc:  # noqa: BLE001
        log_fn(f"  RDKit failed to read {os.path.basename(path)}: {exc}")
    return None


def _write_meeko_sdf(mol, out_sdf: str, log_fn) -> bool:
    """Add explicit Hs + ensure 3D coords, then write an SDF Meeko accepts."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    try:
        mol = Chem.AddHs(mol, addCoords=True)
        has_3d = mol.GetNumConformers() > 0 and mol.GetConformer().Is3D()
        if not has_3d:
            log_fn("  Embedding 3D conformer (input had no 3D coordinates)…")
            if AllChem.EmbedMolecule(mol, randomSeed=0xF00D) != 0:
                AllChem.EmbedMolecule(mol, randomSeed=0xF00D, useRandomCoords=True)
            try:
                AllChem.MMFFOptimizeMolecule(mol)
            except Exception:  # noqa: BLE001
                pass
        writer = Chem.SDWriter(out_sdf)
        writer.write(mol)
        writer.close()
        return os.path.isfile(out_sdf)
    except Exception as exc:  # noqa: BLE001
        log_fn(f"  RDKit SDF normalisation failed: {exc}")
        return False


def _obabel_to_sdf(src: str, out_sdf: str, log_fn, gen3d: bool = False) -> bool:
    """Convert a ligand to a protonated SDF via OpenBabel.

    ``gen3d`` must be False for inputs that already carry 3D coordinates
    (PDB/SDF/MOL2 ligands, e.g. a redocking native ligand) — otherwise
    ``--gen3d`` would discard the crystal pose and generate a fresh conformer.
    """
    if not obabel_available():
        return False
    cmd = ["obabel", src, "-O", out_sdf, "-p", "7.4"]
    if gen3d:
        cmd.append("--gen3d")
    rc = _run(cmd, log_fn)
    if rc == 0 and os.path.isfile(out_sdf):
        return True
    # retry with plain hydrogen addition only
    rc = _run(["obabel", src, "-O", out_sdf, "-h"], log_fn)
    return rc == 0 and os.path.isfile(out_sdf)


def _rdkit_sdf(src: str, out_sdf: str, log_fn) -> bool:
    if not rdkit_available():
        return False
    mol = _rdkit_load(src, log_fn)
    return mol is not None and _write_meeko_sdf(mol, out_sdf, log_fn)


def native_prepare_ligand(in_mol: str, out_pdbqt: str, log_fn) -> bool:
    """Ligand (sdf/mol2/mol/pdb/smiles) -> PDBQT via Meeko. True on success.

    Several ligand -> SDF strategies are tried in priority order; a strategy
    only counts as successful when Meeko actually writes the PDBQT, so a
    strategy that emits a malformed SDF (e.g. a mis-configured OpenBabel) is
    transparently retried with the next one.
    """
    if not meeko_available():
        log_fn("  Meeko not available — cannot prepare ligand natively.")
        return False

    ext = os.path.splitext(in_mol)[1].lower()
    # Generate fresh 3D coordinates only for inputs that have none (SMILES).
    # Coordinate-bearing formats (PDB/SDF/MOL2/MOL) must keep their pose so
    # redocking a native ligand stays in the crystal binding site.
    needs_3d = ext in (".smi", ".smiles")

    def _obabel_builder(src, out, log):
        return _obabel_to_sdf(src, out, log, gen3d=needs_3d)

    # PDB bond perception is unreliable in RDKit, so OpenBabel goes first there;
    # for chemistry-rich formats RDKit is the more faithful default.
    if ext in (".pdb", ".ent"):
        builders = [("openbabel", _obabel_builder), ("rdkit", _rdkit_sdf)]
    else:
        builders = [("rdkit", _rdkit_sdf), ("openbabel", _obabel_builder)]

    tmp_dir = tempfile.mkdtemp(prefix="ladock_lig_")
    try:
        for name, builder in builders:
            meeko_sdf = os.path.join(tmp_dir, f"ligand_{name}.sdf")
            if os.path.isfile(out_pdbqt):
                os.remove(out_pdbqt)
            if not builder(in_mol, meeko_sdf, log_fn):
                continue
            _run(_meeko_cmd("mk_prepare_ligand") + ["-i", meeko_sdf, "-o", out_pdbqt],
                 log_fn)
            if os.path.isfile(out_pdbqt):
                return True
            log_fn(f"  Ligand prep via {name} did not yield a PDBQT — trying next…")
        log_fn("  Could not prepare ligand into a valid PDBQT.")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
