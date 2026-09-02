"""
LADOCK — Molecular Preparation Engine
======================================
RDKit-based pipeline untuk preparasi reseptor (protein) dan ligan
sebelum docking.

Fungsi utama
------------
prep_receptor(pdb_path, steps)  → (pdb_str, report_str)
prep_ligand(mol_path, steps)    → (sdf_str, report_str)

Steps yang didukung
-------------------
Receptor:
  'remove_water'    — hapus molekul air (HOH/WAT/H2O)
  'remove_hetatm'   — hapus semua HETATM kecuali yang dipilih
  'remove_metal'    — hapus ion logam
  'keep_chain'      — pertahankan chain tertentu saja (opsional, via kwarg)
  'add_h'           — tambah atom hidrogen (RDKit AddHs)
  'add_charge'      — hitung Gasteiger partial charge dan simpan di B-factor
  'fix_pdb'         — sanitasi / fix format PDB via RDKit

Ligand:
  'add_h'           — tambah H
  'embed_3d'        — generate/embed konformer 3D (ETKDG)
  'optimize'        — optimasi geometri MMFF94
  'add_charge'      — Gasteiger charge
"""

from __future__ import annotations
import os
import re
import copy
import math
from pathlib import Path
from typing import Optional

# ── RDKit imports (all optional — degrade gracefully) ──────────────────────
try:
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdPartialCharges, rdMolDescriptors
    _RDKIT = True
except ImportError:
    _RDKIT = False


# ── Metal element set ───────────────────────────────────────────────────────
METAL_ELEMENTS = {
    'ZN','MG','CA','FE','MN','CU','NI','CO','MO','NA','K','CD','HG',
    'PT','AU','AG','AL','BA','SR','PB','BI','CS','LI','RB','IN','CR','V','W'
}

WATER_RESNAMES = {'HOH','WAT','H2O','DOD','TIP','SOL'}

# Standard amino-acid residue names (must match parse_pdb_components' classifier
# so the Keep table and the filter agree on what each atom is).
STANDARD_AA = {
    'ALA','ARG','ASN','ASP','CYS','GLN','GLU','GLY','HIS','ILE',
    'LEU','LYS','MET','PHE','PRO','SER','THR','TRP','TYR','VAL',
    'HID','HIE','HIP','CYX','CYM','MSE','SEC','PYL','UNK','ACE','NME'
}


def _classify_atom_token(rec: str, resname: str, chain: str,
                         resseq: str, elem: str):
    """Classify one atom into the same bucket parse_pdb_components() uses.

    Returns a hashable token identifying which component the atom belongs to:
      ('protein', chain) | ('water',) | ('metal',)
      ('ligand', chain, resname, resseq) | ('other',)

    Keeping this in one place guarantees the Keep table and the component
    filter never disagree about an atom's identity.
    """
    resname = resname.strip().upper()
    chain   = (chain.strip() or '?').upper()
    resseq  = str(resseq).strip()
    elem    = elem.strip().upper()
    if rec == 'ATOM' and resname in STANDARD_AA:
        return ('protein', chain)
    if resname in WATER_RESNAMES:
        return ('water',)
    if rec == 'HETATM' and (elem in METAL_ELEMENTS or resname in METAL_ELEMENTS):
        return ('metal',)
    if rec == 'HETATM':
        return ('ligand', chain, resname, resseq)
    return ('other',)


def _component_token(comp: dict):
    """Token for a kept-component dict (from parse_pdb_components)."""
    t = comp.get('type')
    chain   = (str(comp.get('chain', '')).strip() or '?').upper()
    if t == 'Protein':
        return ('protein', chain)
    if t == 'Water':
        return ('water',)
    if t == 'Metal Ion':
        return ('metal',)
    if t == 'Other':
        return ('other',)
    # Ligand
    return ('ligand', chain,
            str(comp.get('resname', '')).strip().upper(),
            str(comp.get('resseq', '')).strip())


# ═══════════════════════════════════════════════════════════════════════════ #
# Pure-text PDB manipulation (no RDKit dependency)
# ═══════════════════════════════════════════════════════════════════════════ #

def _parse_atom_line(line: str) -> dict | None:
    """Parse a PDB ATOM/HETATM line into a dict."""
    rec = line[:6].strip()
    if rec not in ('ATOM', 'HETATM'):
        return None
    try:
        return {
            'record': rec,
            'serial': int(line[6:11]),
            'name':   line[12:16].strip(),
            'altLoc': line[16],
            'resName':line[17:20].strip(),
            'chainID':line[21],
            'resSeq': int(line[22:26]),
            'iCode':  line[26],
            'x':      float(line[30:38]),
            'y':      float(line[38:46]),
            'z':      float(line[46:54]),
            'occ':    float(line[54:60]) if line[54:60].strip() else 1.0,
            'bfac':   float(line[60:66]) if line[60:66].strip() else 0.0,
            'elem':   line[76:78].strip().upper() if len(line) > 76 else '',
            'raw':    line,
        }
    except (ValueError, IndexError):
        return None


def pdb_remove_water(pdb_str: str) -> tuple[str, str]:
    """Remove water molecules (HOH, WAT, …) from PDB text."""
    kept, removed = [], 0
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if a and a['resName'] in WATER_RESNAMES:
            removed += 1
        else:
            kept.append(line)
    return ''.join(kept), f"Removed {removed} water atoms."


def pdb_remove_hetatm(pdb_str: str, keep_resnames: set[str] | None = None) -> tuple[str, str]:
    """Remove HETATM records (optionally keeping specified residue names)."""
    keep_resnames = keep_resnames or set()
    kept, removed = [], 0
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if a and a['record'] == 'HETATM' and a['resName'] not in keep_resnames:
            removed += 1
        else:
            kept.append(line)
    return ''.join(kept), f"Removed {removed} HETATM atoms."


def pdb_remove_metals(pdb_str: str) -> tuple[str, str]:
    """Remove metal ion HETATM records."""
    kept, removed = [], 0
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if a and a['record'] == 'HETATM' and a['elem'] in METAL_ELEMENTS:
            removed += 1
        else:
            kept.append(line)
    return ''.join(kept), f"Removed {removed} metal ion atoms."


def pdb_filter_components(pdb_str: str, kept_components: list[dict]) -> tuple[str, str]:
    """Keep only atoms that belong to the checked (kept) components.

    Each atom is classified with the exact same rules parse_pdb_components()
    uses to build the Keep table, so an atom is kept if and only if the
    component it was displayed under is checked.
    """
    keep_tokens = {_component_token(comp) for comp in kept_components}

    result, removed = [], 0
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if not a:
            result.append(line)
            continue

        token = _classify_atom_token(
            a['record'], a['resName'], a['chainID'], a['resSeq'], a['elem'])
        if token in keep_tokens:
            result.append(line)
        else:
            removed += 1

    return ''.join(result), f"Filtered components: removed {removed} atoms."


def pdb_keep_chain(pdb_str: str, chains: list[str]) -> tuple[str, str]:
    """Keep only atoms belonging to the specified chain IDs."""
    chains_set = {c.upper() for c in chains}
    kept, removed = [], 0
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if a and a['chainID'].upper() not in chains_set:
            removed += 1
        else:
            kept.append(line)
    return ''.join(kept), f"Kept chains {chains}; removed {removed} atoms from other chains."


def pdb_renumber_atoms(pdb_str: str) -> str:
    """Renumber ATOM/HETATM serials sequentially starting from 1."""
    out, n = [], 1
    for line in pdb_str.splitlines(keepends=True):
        a = _parse_atom_line(line)
        if a:
            line = f"{line[:6]}{n:5d}{line[11:]}"
            n += 1
        out.append(line)
    return ''.join(out)


# ═══════════════════════════════════════════════════════════════════════════ #
# RDKit-based operations
# ═══════════════════════════════════════════════════════════════════════════ #

def _require_rdkit(fn_name: str):
    if not _RDKIT:
        raise RuntimeError(f"{fn_name} requires RDKit (not installed).")


def _fix_h_monomer_info(mol_h, n_orig: int):
    """
    Fix MonomerInfo for new H atoms added by RDKit AddHs.
    RDKit places all new H at end with no/wrong MonomerInfo.
    For each new H, find its heavy-atom neighbor and copy its residue info.
    """
    for atom in mol_h.GetAtoms():
        if atom.GetIdx() < n_orig:
            continue  # original atom — skip
        info = atom.GetMonomerInfo()
        resname = info.GetResidueName().strip() if info else ''
        if resname and resname not in ('UNL', 'UNK', ''):
            continue  # already correct
        # Find first neighbor that is a heavy atom with valid residue info
        for nb in atom.GetNeighbors():
            nb_info = nb.GetMonomerInfo()
            if nb_info is None:
                continue
            nb_res = nb_info.GetResidueName().strip()
            if nb_res and nb_res not in ('UNL', 'UNK', ''):
                new_info = Chem.AtomPDBResidueInfo()
                new_info.SetResidueName(nb_res)
                new_info.SetResidueNumber(nb_info.GetResidueNumber())
                new_info.SetInsertionCode(nb_info.GetInsertionCode())
                new_info.SetChainId(nb_info.GetChainId())
                new_info.SetIsHeteroAtom(nb_info.GetIsHeteroAtom())
                new_info.SetName(f' H  ')
                atom.SetMonomerInfo(new_info)
                break


def _pdb_split_scope(pdb_str: str) -> tuple[str, str, list[str]]:
    """Split PDB into (protein_str, ligand_str, other_lines).
    protein = ATOM records, ligand = HETATM records, other = everything else.
    """
    protein, ligand, other = [], [], []
    for line in pdb_str.splitlines(keepends=True):
        rec = line[:6].strip()
        if rec == 'ATOM':
            protein.append(line)
        elif rec == 'HETATM':
            ligand.append(line)
        else:
            other.append(line)
    return ''.join(protein), ''.join(ligand), other


def _pdb_merge_scope(protein_str: str, ligand_str: str,
                     other_lines: list[str]) -> str:
    """Merge back protein + ligand, keeping header/remark non-coord lines."""
    result = []
    end_records = {'END', 'MASTER', 'CONECT'}
    for line in other_lines:
        if line[:6].strip() not in end_records:
            result.append(line)
    for s in (protein_str, ligand_str):
        for line in s.splitlines(keepends=True):
            if line[:6].strip() in ('ATOM', 'HETATM'):
                result.append(line)
    result.append('END\n')
    return ''.join(result)


def _apply_scoped(pdb_str: str, scope: str, fn) -> tuple[str, str]:
    """Apply fn only to 'protein' (ATOM) or 'ligand' (HETATM) portion."""
    protein, ligand, other = _pdb_split_scope(pdb_str)
    if scope == 'protein':
        if not protein.strip():
            return pdb_str, "No protein ATOM records found — skipped."
        processed, msg = fn(protein)
        return _pdb_merge_scope(processed, ligand, other), msg
    else:  # ligand
        if not ligand.strip():
            return pdb_str, "No ligand HETATM records found — skipped."
        processed, msg = fn(ligand)
        return _pdb_merge_scope(protein, processed, other), msg



def rdkit_add_hydrogens(pdb_str: str) -> tuple[str, str]:
    """Add all missing hydrogens to a protein using RDKit."""
    _require_rdkit('add_hydrogens')
    mol = Chem.MolFromPDBBlock(pdb_str, sanitize=True, removeHs=False)
    if mol is None:
        raise ValueError("RDKit could not parse PDB block. Try 'Fix PDB' first.")
    n_orig = mol.GetNumAtoms()
    mol_h = Chem.AddHs(mol, addCoords=True)
    _fix_h_monomer_info(mol_h, n_orig)
    n = mol_h.GetNumAtoms() - n_orig
    return Chem.MolToPDBBlock(mol_h), f"Added {n} hydrogen atoms (all)."


def rdkit_add_hydrogens_polar(pdb_str: str) -> tuple[str, str]:
    """Add only polar hydrogens (N, O, S) to a protein using RDKit."""
    _require_rdkit('add_hydrogens_polar')
    mol = Chem.MolFromPDBBlock(pdb_str, sanitize=True, removeHs=False)
    if mol is None:
        raise ValueError("RDKit could not parse PDB block. Try 'Fix PDB' first.")
    n_orig = mol.GetNumAtoms()
    mol_h = Chem.AddHs(mol, addCoords=True, onlyOnAtoms=[
        a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() in (7, 8, 16)
    ])
    _fix_h_monomer_info(mol_h, n_orig)
    n = mol_h.GetNumAtoms() - n_orig
    return Chem.MolToPDBBlock(mol_h), f"Added {n} polar hydrogen atoms."


def _fmt_bfactor_charge(charge: float) -> float:
    """Sanitize a charge value for the 6-wide B-factor field.

    Filters NaN/inf and clamps magnitude so f'{c:6.3f}' never exceeds 6 chars
    (which would shift the element/columns after it).
    """
    if charge is None or not math.isfinite(charge):
        return 0.0
    # A leading '-' takes one of the 6 columns, so the safe range for f'{c:6.3f}'
    # is [-9.999, 9.999]. Real Gasteiger charges sit far inside this.
    return max(-9.999, min(9.999, charge))


def rdkit_add_gasteiger_charges(pdb_str: str) -> tuple[str, str]:
    """Compute Gasteiger charges; store them in the B-factor column of the PDB.

    Charges are matched to PDB lines by **atom serial number** rather than by
    positional order, so any atom RDKit drops or reorders while parsing cannot
    silently shift charges onto the wrong atoms. Falls back to positional
    mapping only if serial numbers are unavailable. NaN charges are written as
    0.000 and values are clamped to keep the B-factor column aligned.
    """
    _require_rdkit('add_gasteiger_charges')
    mol = Chem.MolFromPDBBlock(pdb_str, sanitize=True, removeHs=False)
    if mol is None:
        raise ValueError("RDKit could not parse PDB block.")
    rdPartialCharges.ComputeGasteigerCharges(mol)

    # Primary mapping: PDB serial number → charge.
    charge_by_serial: dict[int, float] = {}
    for atom in mol.GetAtoms():
        info = atom.GetMonomerInfo()
        if info is None:
            continue
        try:
            charge_by_serial[info.GetSerialNumber()] = \
                _fmt_bfactor_charge(atom.GetDoubleProp('_GasteigerCharge'))
        except KeyError:
            continue

    lines, n = [], 0
    if charge_by_serial:
        for line in pdb_str.splitlines(keepends=True):
            a = _parse_atom_line(line)
            if a and a['serial'] in charge_by_serial:
                c = charge_by_serial[a['serial']]
                line = f"{line[:60]}{c:6.3f}{line[66:]}"
                n += 1
            lines.append(line)
    else:
        # Fallback: positional mapping (serials unavailable).
        idx = 0
        for line in pdb_str.splitlines(keepends=True):
            a = _parse_atom_line(line)
            if a and idx < mol.GetNumAtoms():
                try:
                    c = _fmt_bfactor_charge(
                        mol.GetAtomWithIdx(idx).GetDoubleProp('_GasteigerCharge'))
                except KeyError:
                    c = 0.0
                line = f"{line[:60]}{c:6.3f}{line[66:]}"
                idx += 1
                n += 1
            lines.append(line)
    return ''.join(lines), f"Gasteiger charges computed for {n} atoms (stored in B-factor)."


def rdkit_fix_pdb(pdb_str: str) -> tuple[str, str]:
    """Round-trip through RDKit to sanitize/fix PDB format."""
    _require_rdkit('fix_pdb')
    mol = Chem.MolFromPDBBlock(pdb_str, sanitize=False, removeHs=False)
    if mol is None:
        raise ValueError("RDKit could not parse PDB block.")
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        pass  # Partial sanitization OK for proteins
    out = Chem.MolToPDBBlock(mol)
    return out, "PDB sanitized via RDKit."


# ── Ligand ──────────────────────────────────────────────────────────────────

def ligand_add_h_embed_optimize(mol_input: str, fmt: str = 'sdf') -> tuple[str, str]:
    """
    Full ligand prep pipeline:
      1. Parse molecule
      2. Add hydrogens
      3. Embed 3D conformer (ETKDG)
      4. MMFF94 optimization
      5. Gasteiger charges
    Returns (sdf_str, report).
    """
    _require_rdkit('ligand preparation')
    report = []

    if fmt == 'sdf':
        mol = Chem.MolFromMolBlock(mol_input, removeHs=False)
    elif fmt == 'mol2':
        mol = Chem.MolFromMol2Block(mol_input, removeHs=False)
    elif fmt == 'pdb':
        mol = Chem.MolFromPDBBlock(mol_input, removeHs=False)
    elif fmt == 'smiles':
        mol = Chem.MolFromSmiles(mol_input)
    else:
        mol = Chem.MolFromMolBlock(mol_input, removeHs=False)

    if mol is None:
        raise ValueError(f"Cannot parse ligand as {fmt}.")

    # Add H
    mol = Chem.AddHs(mol, addCoords=True)
    report.append(f"Hydrogens added → {mol.GetNumAtoms()} atoms total.")

    # Embed 3D if needed
    if mol.GetNumConformers() == 0:
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        r = AllChem.EmbedMolecule(mol, params)
        if r == -1:
            AllChem.EmbedMolecule(mol, AllChem.ETKDG())
        report.append("3D conformer generated (ETKDGv3).")

    # MMFF94 optimize
    try:
        res = AllChem.MMFFOptimizeMolecule(mol, mmffVariant='MMFF94', maxIters=2000)
        if res == 0:
            report.append("MMFF94 geometry optimization converged.")
        elif res == 1:
            report.append("MMFF94 optimization did not fully converge (acceptable).")
    except Exception as e:
        report.append(f"MMFF94 skipped: {e}")

    # Gasteiger charges
    try:
        rdPartialCharges.ComputeGasteigerCharges(mol)
        report.append("Gasteiger charges computed.")
    except Exception as e:
        report.append(f"Charge computation skipped: {e}")

    sdf_out = Chem.MolToMolBlock(mol)
    return sdf_out, '\n'.join(report)


# ═══════════════════════════════════════════════════════════════════════════ #
# High-level pipeline wrappers
# ═══════════════════════════════════════════════════════════════════════════ #

class PrepResult:
    """Container for preparation output."""
    def __init__(self, output_str: str, report: list[str], fmt: str):
        self.output_str = output_str   # prepared file content as string
        self.report     = report       # list of step descriptions
        self.fmt        = fmt          # 'pdb' or 'sdf'
        self.ok         = True

    def full_report(self) -> str:
        return '\n'.join(f"  ✔ {r}" for r in self.report)


def prep_receptor(pdb_path: str, steps: list[str],
                  keep_chains: list[str] | None = None,
                  kept_components: list[dict] | None = None) -> PrepResult:
    """
    Prepare a receptor PDB file.

    steps (in order):
      'filter_components', 'keep_chain', 'fix_pdb',
      'add_h_protein', 'add_h_polar_protein', 'add_h_ligand', ...
    """
    pdb_str = Path(pdb_path).read_text(encoding='utf-8', errors='replace')
    report  = []

    _kc = kept_components or []
    step_map = {
        'filter_components': lambda s: pdb_filter_components(s, _kc),
        'keep_chain':        lambda s: pdb_keep_chain(s, keep_chains or ['A']),
        # whole-structure (legacy / fallback)
        'remove_water':      lambda s: pdb_remove_water(s),
        'remove_hetatm':     lambda s: pdb_remove_hetatm(s),
        'remove_metal':      lambda s: pdb_remove_metals(s),
        'add_h':             lambda s: rdkit_add_hydrogens(s),
        'add_h_polar':       lambda s: rdkit_add_hydrogens_polar(s),
        'add_charge':        lambda s: rdkit_add_gasteiger_charges(s),
        'fix_pdb':           lambda s: rdkit_fix_pdb(s),
        # scoped — protein (ATOM) only
        'add_h_protein':      lambda s: _apply_scoped(s, 'protein', rdkit_add_hydrogens),
        'add_h_polar_protein':lambda s: _apply_scoped(s, 'protein', rdkit_add_hydrogens_polar),
        'add_charge_protein': lambda s: _apply_scoped(s, 'protein', rdkit_add_gasteiger_charges),
        # scoped — ligand (HETATM) only
        'add_h_ligand':       lambda s: _apply_scoped(s, 'ligand',  rdkit_add_hydrogens),
        'add_h_polar_ligand': lambda s: _apply_scoped(s, 'ligand',  rdkit_add_hydrogens_polar),
        'add_charge_ligand':  lambda s: _apply_scoped(s, 'ligand',  rdkit_add_gasteiger_charges),
    }

    for step in steps:
        if step not in step_map:
            report.append(f"Unknown step '{step}' — skipped.")
            continue
        try:
            pdb_str, msg = step_map[step](pdb_str)
            report.append(f"[{step}] {msg}")
        except Exception as e:
            report.append(f"[{step}] ERROR: {e}")

    pdb_str = pdb_renumber_atoms(pdb_str)
    return PrepResult(pdb_str, report, 'pdb')


def prep_ligand(mol_path: str, steps: list[str]) -> PrepResult:
    """
    Prepare a ligand file.

    steps: 'add_h', 'embed_3d', 'optimize', 'add_charge'
    (All steps run as a single RDKit pipeline.)
    """
    path = Path(mol_path)
    ext  = path.suffix.lower()
    src  = path.read_text(encoding='utf-8', errors='replace')
    fmt_map = {'.sdf':  'sdf', '.mol':  'sdf', '.mol2': 'mol2',
               '.pdb':  'pdb', '.pdbqt':'pdb'}
    fmt = fmt_map.get(ext, 'sdf')

    out_str, rep = ligand_add_h_embed_optimize(src, fmt=fmt)
    return PrepResult(out_str, rep.splitlines(), 'sdf')
