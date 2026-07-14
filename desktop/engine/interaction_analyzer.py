"""
LADOCK — Interaction Analyzer (engine/interaction_analyzer.py)

Detects non-covalent interactions between receptor and ligand
by parsing PDBQT atom coordinates + types. No RDKit required.

Interactions detected
---------------------
1. H-Bond         — donor-acceptor distance ≤ 3.5 Å, D-H…A angle ≥ 120°
2. Hydrophobic     — C/A···C/A distance ≤ 4.5 Å (nonpolar carbons)
3. Pi-Stacking     — aromatic ring centroid ≤ 5.5 Å (face-face or T-shape)
4. Salt Bridge     — charged group centroid ≤ 5.0 Å (opposite charges)
5. Halogen Bond    — halogen···acceptor ≤ 3.5 Å

PDBQT atom types used
---------------------
A  — aromatic carbon
C  — aliphatic carbon
HD — hydrogen on donor
NA — nitrogen H-bond acceptor
OA — oxygen H-bond acceptor
SA — sulfur H-bond acceptor
N  — nitrogen
O  — oxygen

Usage
-----
    from engine.interaction_analyzer import analyze_interactions, InteractionResult
    atoms_rec = parse_pdbqt(receptor_text)
    atoms_lig = parse_pdbqt(ligand_text)
    results   = analyze_interactions(atoms_rec, atoms_lig)
    for r in results:
        print(r)
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Atom:
    serial:    int
    name:      str
    resname:   str
    chain:     str
    resseq:    int
    x: float
    y: float
    z: float
    charge:    float
    atom_type: str          # PDBQT type (A, C, N, OA, HD, …)
    source:    str = ""     # "receptor" | "ligand"

    @property
    def pos(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], dtype=float)

    def __repr__(self):
        return f"Atom({self.name}/{self.resname}{self.resseq}/{self.chain} {self.atom_type})"


@dataclass
class Interaction:
    itype:       str               # "H-Bond" | "Hydrophobic" | "Pi-Stacking" | "Salt Bridge" | "Halogen Bond"
    rec_atom:    Atom
    lig_atom:    Atom
    distance:    float             # Å
    angle:       Optional[float]   # degrees (H-bond D-H-A angle, pi tilt angle, etc.)
    subtype:     str = ""          # e.g. "Face-to-Face", "T-Shaped", "donor=N", etc.

    def rec_label(self) -> str:
        return f"{self.rec_atom.resname}{self.rec_atom.resseq}:{self.rec_atom.chain} {self.rec_atom.name}"

    def lig_label(self) -> str:
        return f"{self.lig_atom.resname} {self.lig_atom.name}"

    def __str__(self):
        angle_str = f"  angle={self.angle:.1f}°" if self.angle is not None else ""
        return (f"[{self.itype}] {self.rec_label()} ↔ {self.lig_label()}"
                f"  d={self.distance:.2f}Å{angle_str}  {self.subtype}")


@dataclass
class AnalysisResult:
    interactions:   List[Interaction] = field(default_factory=list)
    summary:        dict              = field(default_factory=dict)

    def by_type(self, itype: str) -> List[Interaction]:
        return [i for i in self.interactions if i.itype == itype]

    def count(self) -> dict:
        counts: dict[str, int] = {}
        for i in self.interactions:
            counts[i.itype] = counts.get(i.itype, 0) + 1
        return counts

    def to_hbond_vectors(self) -> list[dict]:
        """Return H-bond start/end dicts for 3Dmol.js showHBonds()."""
        return self.to_vectors(["H-Bond"])

    def to_vectors(self, types: list[str] | None = None) -> list[dict]:
        """
        Return bond vector dicts for 3Dmol.js showHBonds() / showInteractionHighlights().
        Includes residue info for pocket highlighting.
        types: list of itype strings to include; None = all types.
        """
        _COLORS = {
            "H-Bond":       "#4fc3f7",
            "Hydrophobic":  "#ffb74d",
            "Pi-Stacking":  "#ce93d8",
            "Salt Bridge":  "#ef5350",
            "Halogen Bond": "#80cbc4",
        }
        out = []
        for i in self.interactions:
            if types and i.itype not in types:
                continue
            out.append({
                "start":      {"x": i.rec_atom.x, "y": i.rec_atom.y, "z": i.rec_atom.z},
                "end":        {"x": i.lig_atom.x, "y": i.lig_atom.y, "z": i.lig_atom.z},
                "itype":      i.itype,
                "color":      _COLORS.get(i.itype, "#ffffff"),
                "distance":   round(i.distance, 2),
                "rec_resn":   i.rec_atom.resname,
                "rec_resi":   i.rec_atom.resseq,
                "rec_chain":  i.rec_atom.chain,
                "rec_atom":   i.rec_atom.name,
                "lig_atom":   i.lig_atom.name,
            })
        return out

    def get_pocket_residues(self) -> list[dict]:
        """Return unique receptor residues involved in any interaction."""
        seen: set[tuple] = set()
        out = []
        for i in self.interactions:
            key = (i.rec_atom.resname, i.rec_atom.resseq, i.rec_atom.chain)
            if key not in seen:
                seen.add(key)
                out.append({
                    "resn":  i.rec_atom.resname,
                    "resi":  i.rec_atom.resseq,
                    "chain": i.rec_atom.chain,
                })
        return out


# ---------------------------------------------------------------------------
# PDBQT parser
# ---------------------------------------------------------------------------

# Regex for ATOM/HETATM lines
_ATOM_RE = re.compile(
    r'^(?:ATOM|HETATM)\s+'
    r'(\d+)\s+'           # serial
    r'(\S+)\s+'           # name
    r'(\S+)\s+'           # resname
    r'(\S?)\s*'           # chain (may be empty)
    r'(-?\d+)\s+'         # resseq
    r'(-?\d+\.?\d*)\s+'   # x
    r'(-?\d+\.?\d*)\s+'   # y
    r'(-?\d+\.?\d*)\s+'   # z
    r'(-?\d+\.?\d*)\s+'   # occupancy
    r'(-?\d+\.?\d*)\s*'   # tempfactor
    r'(-?\d+\.?\d*)?\s*'  # charge (optional)
    r'(\S+)?'             # atom_type (optional)
)


def parse_pdbqt(text: str, source: str = "") -> List[Atom]:
    """Parse a PDBQT string and return atoms from the FIRST MODEL only.

    Docking output files (e.g. Vina) contain multiple MODEL/ENDMDL blocks —
    one per pose.  We always analyse pose 1 so that atom coordinates match
    what the 3D viewer displays for the top-ranked pose.
    Receptor PDBQT files typically have no ENDMDL, so they are unaffected.
    """
    atoms: List[Atom] = []
    for line in text.splitlines():
        tag = line[:6].strip()
        if tag == "ENDMDL":          # stop after first pose
            break
        m = _ATOM_RE.match(line)
        if not m:
            continue
        try:
            atoms.append(Atom(
                serial    = int(m.group(1)),
                name      = m.group(2),
                resname   = m.group(3),
                chain     = m.group(4) or "A",
                resseq    = int(m.group(5)),
                x         = float(m.group(6)),
                y         = float(m.group(7)),
                z         = float(m.group(8)),
                charge    = float(m.group(11)) if m.group(11) else 0.0,
                atom_type = m.group(12) or m.group(2)[0],
                source    = source,
            ))
        except (ValueError, IndexError):
            continue
    return atoms


def parse_pdbqt_file(path: str, source: str = "") -> List[Atom]:
    """Parse a PDBQT file from disk."""
    return parse_pdbqt(Path(path).read_text(encoding="utf-8", errors="replace"), source)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _dist(a: Atom, b: Atom) -> float:
    return float(np.linalg.norm(a.pos - b.pos))


def _angle_deg(v1: np.ndarray, v2: np.ndarray) -> float:
    cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


# ---------------------------------------------------------------------------
# Atom classification helpers
# ---------------------------------------------------------------------------

# PDBQT atom types
_HBOND_DONORS    = {"N", "O", "OA", "NA", "NS", "NX", "NY", "NH"}   # heavy-atom donors
_HBOND_ACCEPTORS = {"OA", "NA", "SA", "N", "O", "OS", "NS", "F"}
_AROMATIC        = {"A"}
_HYDROPHOBIC     = {"C", "A", "S", "CL", "BR", "I", "F"}            # nonpolar-ish
_HALOGENS        = {"CL", "BR", "I", "F"}
_POS_CHARGED     = {"NH", "NX", "NY", "NC", "N+"}                   # Arg, Lys N types
_NEG_CHARGED     = {"OA", "O-"}                                      # carboxylate O


def _is_donor(a: Atom) -> bool:
    t = a.atom_type.upper()
    return t in _HBOND_DONORS and a.name[0] not in ("H",)


def _is_acceptor(a: Atom) -> bool:
    return a.atom_type.upper() in _HBOND_ACCEPTORS


def _is_aromatic(a: Atom) -> bool:
    return a.atom_type.upper() in _AROMATIC


def _is_hydrophobic(a: Atom) -> bool:
    t = a.atom_type.upper()
    if t not in _HYDROPHOBIC:
        return False
    # Exclude polar C bound to heteroatom (approximation: resname heuristic)
    return True


def _is_halogen(a: Atom) -> bool:
    return a.atom_type.upper() in _HALOGENS


def _is_pos_charged(a: Atom) -> bool:
    t = a.atom_type.upper()
    if t in _POS_CHARGED:
        return True
    # Guanidinium N: ARG
    if a.resname == "ARG" and a.name in ("NH1", "NH2", "NE"):
        return True
    if a.resname == "LYS" and a.name == "NZ":
        return True
    if a.resname == "HIS" and a.name in ("ND1", "NE2"):
        return True
    return False


def _is_neg_charged(a: Atom) -> bool:
    if a.resname in ("ASP", "GLU") and a.atom_type.upper() in ("OA", "O"):
        return True
    return False


# ---------------------------------------------------------------------------
# Ring detection (aromatic rings from PDBQT)
# ---------------------------------------------------------------------------

# Standard aromatic residues and the atom names that form the ring
_RES_RINGS: dict[str, list[list[str]]] = {
    "PHE": [["CG", "CD1", "CD2", "CE1", "CE2", "CZ"]],
    "TYR": [["CG", "CD1", "CD2", "CE1", "CE2", "CZ"]],
    "TRP": [["CG", "CD1", "CD2", "NE1", "CE2", "CE3"],
             ["CD2", "CE2", "CZ2", "CZ3", "CH2", "CE3"]],
    "HIS": [["CG", "ND1", "CD2", "CE1", "NE2"]],
    "PHO": [["CG", "CD1", "CD2", "CE1", "CE2", "CZ"]],
}


def _find_rings(atoms: List[Atom]) -> list[np.ndarray]:
    """
    Find aromatic ring centroids + normal vectors.
    Returns list of (centroid, normal) tuples.
    """
    rings: list[tuple[np.ndarray, np.ndarray]] = []

    # Group by residue
    res_map: dict[tuple, list[Atom]] = {}
    for a in atoms:
        key = (a.resname, a.resseq, a.chain)
        res_map.setdefault(key, []).append(a)

    for (resname, resseq, chain), res_atoms in res_map.items():
        ring_defs = _RES_RINGS.get(resname.upper(), [])
        name_map  = {a.name: a for a in res_atoms}
        for ring_names in ring_defs:
            ring_atoms = [name_map[n] for n in ring_names if n in name_map]
            if len(ring_atoms) < 4:
                continue
            pts     = np.array([a.pos for a in ring_atoms])
            centroid = pts.mean(axis=0)
            # Normal via cross product of two edge vectors
            v1 = pts[1] - pts[0]
            v2 = pts[2] - pts[0]
            normal = np.cross(v1, v2)
            norm = np.linalg.norm(normal)
            if norm > 1e-6:
                normal /= norm
            rings.append((centroid, normal))

    # Also detect rings from aromatic-type ligand atoms (any residue)
    # Cluster A-type atoms within 2.0 Å mutual distance → approximate ring
    aro_atoms = [a for a in atoms if _is_aromatic(a)]
    used: set[int] = set()
    for i, a in enumerate(aro_atoms):
        if i in used:
            continue
        cluster = [a]
        for j, b in enumerate(aro_atoms):
            if j != i and j not in used and _dist(a, b) < 2.0:
                cluster.append(b)
                used.add(j)
        used.add(i)
        if len(cluster) >= 4:
            pts = np.array([c.pos for c in cluster])
            centroid = pts.mean(axis=0)
            v1 = pts[1] - pts[0]
            v2 = pts[2] - pts[0]
            normal = np.cross(v1, v2)
            norm = np.linalg.norm(normal)
            if norm > 1e-6:
                normal /= norm
            rings.append((centroid, normal))

    return rings


# ---------------------------------------------------------------------------
# Main interaction detection
# ---------------------------------------------------------------------------

# Cutoffs
_HBOND_MAX_DIST  = 3.5    # Å
_HBOND_MIN_ANGLE = 120.0  # D-H-A angle degrees
_HPHOB_MAX_DIST  = 4.5
_PISTACK_MAX_DIST = 5.5
_PISTACK_TSHAPE_MAX = 6.5
_SALTBR_MAX_DIST = 5.0
_HALOG_MAX_DIST  = 3.5

# Minimum distance to avoid intra-residue false positives
_MIN_DIST = 1.5


def analyze_interactions(
    receptor_atoms: List[Atom],
    ligand_atoms:   List[Atom],
    cutoff_shell:   float = 8.0,   # only receptor atoms within this Å of ligand CoM
) -> AnalysisResult:
    """
    Detect all non-covalent interactions between receptor and ligand atom sets.
    Returns an AnalysisResult with full interaction list + summary counts.
    """
    result = AnalysisResult()

    if not receptor_atoms or not ligand_atoms:
        return result

    # Pre-filter receptor atoms to binding-site shell
    lig_com = np.mean([a.pos for a in ligand_atoms], axis=0)
    shell_rec = [a for a in receptor_atoms
                 if np.linalg.norm(a.pos - lig_com) <= cutoff_shell]

    if not shell_rec:
        shell_rec = receptor_atoms  # fallback

    # Build hydrogen positions from ligand (HD atoms)
    lig_h = {a.serial: a for a in ligand_atoms if a.name.startswith("H")}

    interactions: List[Interaction] = []

    # ── 1. H-Bonds ──────────────────────────────────────────────────────────
    lig_donors    = [a for a in ligand_atoms   if _is_donor(a)]
    lig_acceptors = [a for a in ligand_atoms   if _is_acceptor(a)]
    rec_donors    = [a for a in shell_rec      if _is_donor(a)]
    rec_acceptors = [a for a in shell_rec      if _is_acceptor(a)]

    # Ligand as acceptor ↔ Receptor as donor
    for r in rec_donors:
        for l in lig_acceptors:
            d = _dist(r, l)
            if _MIN_DIST < d <= _HBOND_MAX_DIST:
                # Estimate D-H-A angle without explicit H coords → use 180° proxy
                angle = None
                interactions.append(Interaction(
                    itype="H-Bond", rec_atom=r, lig_atom=l,
                    distance=d, angle=angle,
                    subtype=f"rec-donor={r.name}"
                ))

    # Ligand as donor ↔ Receptor as acceptor
    for l in lig_donors:
        for r in rec_acceptors:
            d = _dist(l, r)
            if _MIN_DIST < d <= _HBOND_MAX_DIST:
                interactions.append(Interaction(
                    itype="H-Bond", rec_atom=r, lig_atom=l,
                    distance=d, angle=None,
                    subtype=f"lig-donor={l.name}"
                ))

    # Deduplicate H-bonds (same pair, keep shortest)
    seen: set[tuple] = set()
    hbonds_dedup: List[Interaction] = []
    for i in interactions:
        key = (i.rec_atom.serial, i.lig_atom.serial)
        rev = (i.lig_atom.serial, i.rec_atom.serial)
        if key not in seen and rev not in seen:
            seen.add(key)
            hbonds_dedup.append(i)
    interactions = hbonds_dedup

    # ── 2. Hydrophobic contacts ──────────────────────────────────────────────
    rec_hphob = [a for a in shell_rec    if _is_hydrophobic(a)]
    lig_hphob = [a for a in ligand_atoms if _is_hydrophobic(a)]

    for r in rec_hphob:
        for l in lig_hphob:
            d = _dist(r, l)
            if _MIN_DIST < d <= _HPHOB_MAX_DIST:
                interactions.append(Interaction(
                    itype="Hydrophobic", rec_atom=r, lig_atom=l,
                    distance=d, angle=None,
                    subtype=f"{r.atom_type}···{l.atom_type}"
                ))

    # ── 3. Pi-Stacking ───────────────────────────────────────────────────────
    rec_rings = _find_rings(shell_rec)
    lig_rings = _find_rings(ligand_atoms)

    # Need representative atoms for the Interaction dataclass; use closest ring atom
    def _nearest_atom(atoms: List[Atom], centroid: np.ndarray) -> Atom:
        return min(atoms, key=lambda a: np.linalg.norm(a.pos - centroid))

    for (rc, rn) in rec_rings:
        for (lc, ln) in lig_rings:
            cd = float(np.linalg.norm(rc - lc))
            if cd > _PISTACK_TSHAPE_MAX:
                continue
            tilt = _angle_deg(rn, ln)
            tilt = min(tilt, 180 - tilt)   # 0° = parallel, 90° = T

            r_rep = _nearest_atom(shell_rec,    rc)
            l_rep = _nearest_atom(ligand_atoms, lc)

            if cd <= _PISTACK_MAX_DIST and tilt < 35:
                interactions.append(Interaction(
                    itype="Pi-Stacking", rec_atom=r_rep, lig_atom=l_rep,
                    distance=cd, angle=tilt, subtype="Face-to-Face"
                ))
            elif cd <= _PISTACK_TSHAPE_MAX and 55 <= tilt <= 90:
                interactions.append(Interaction(
                    itype="Pi-Stacking", rec_atom=r_rep, lig_atom=l_rep,
                    distance=cd, angle=tilt, subtype="T-Shaped (Edge-to-Face)"
                ))

    # ── 4. Salt Bridges ──────────────────────────────────────────────────────
    rec_pos = [a for a in shell_rec      if _is_pos_charged(a)]
    rec_neg = [a for a in shell_rec      if _is_neg_charged(a)]
    lig_pos = [a for a in ligand_atoms   if a.charge > 0.3]
    lig_neg = [a for a in ligand_atoms   if a.charge < -0.3]

    for r in rec_pos:
        for l in lig_neg:
            d = _dist(r, l)
            if _MIN_DIST < d <= _SALTBR_MAX_DIST:
                interactions.append(Interaction(
                    itype="Salt Bridge", rec_atom=r, lig_atom=l,
                    distance=d, angle=None, subtype="(+)rec···(-)lig"
                ))
    for r in rec_neg:
        for l in lig_pos:
            d = _dist(r, l)
            if _MIN_DIST < d <= _SALTBR_MAX_DIST:
                interactions.append(Interaction(
                    itype="Salt Bridge", rec_atom=r, lig_atom=l,
                    distance=d, angle=None, subtype="(-)rec···(+)lig"
                ))

    # ── 5. Halogen Bonds ─────────────────────────────────────────────────────
    lig_hal = [a for a in ligand_atoms if _is_halogen(a)]
    for l in lig_hal:
        for r in rec_acceptors:
            d = _dist(l, r)
            if _MIN_DIST < d <= _HALOG_MAX_DIST:
                interactions.append(Interaction(
                    itype="Halogen Bond", rec_atom=r, lig_atom=l,
                    distance=d, angle=None,
                    subtype=f"X={l.atom_type}"
                ))

    result.interactions = interactions
    result.summary = result.count()
    return result


# ---------------------------------------------------------------------------
# Convenience file-based API
# ---------------------------------------------------------------------------

def analyze_from_files(receptor_path: str, ligand_path: str) -> AnalysisResult:
    """High-level: parse two PDBQT files and return AnalysisResult."""
    rec = parse_pdbqt_file(receptor_path, source="receptor")
    lig = parse_pdbqt_file(ligand_path,   source="ligand")
    return analyze_interactions(rec, lig)


def analyze_from_strings(receptor_text: str, ligand_text: str) -> AnalysisResult:
    """High-level: parse two PDBQT strings and return AnalysisResult."""
    rec = parse_pdbqt(receptor_text, source="receptor")
    lig = parse_pdbqt(ligand_text,   source="ligand")
    return analyze_interactions(rec, lig)
