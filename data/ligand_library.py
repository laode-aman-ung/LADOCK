"""
LADOCK — Ligand Library Manager (data/ligand_library.py)

Manages collections of ligands from multiple sources:
  - SMILES CSV  (name, smiles, [activity, ...])
  - SDF / SDFgz file
  - Folder of PDBQT files
  - Single SMILES string

Provides:
  - LigandEntry  — lightweight record per ligand
  - LigandLibrary — collection + filter/sort + persistence (JSON index)
  - load_smiles_csv(), load_sdf(), load_pdbqt_folder() — loaders
"""

from __future__ import annotations

import os
import re
import csv
import json
import gzip
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Iterator


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LigandEntry:
    name:     str
    smiles:   str         = ""
    source:   str         = ""    # file path or "manual"
    activity: str         = ""    # experimental activity (optional)
    pdbqt:    str         = ""    # path to prepared PDBQT, if available
    tags:     List[str]   = field(default_factory=list)
    mw:       float       = 0.0   # molecular weight (filled lazily)
    hba:      int         = 0     # H-bond acceptors
    hbd:      int         = 0     # H-bond donors
    selected: bool        = True  # include in next batch run

    @property
    def uid(self) -> str:
        """Stable short hash based on name + smiles."""
        raw = f"{self.name}|{self.smiles}"
        return hashlib.md5(raw.encode()).hexdigest()[:8]

    def to_smiles_row(self) -> list:
        """Format expected by docking_engine._load_ligand_file / process_smi."""
        return [self.name, self.smiles, self.activity]

    def passes_lipinski(self) -> bool:
        """Very rough Lipinski check (mw, hba, hbd)."""
        return (
            (self.mw == 0 or self.mw <= 500)
            and self.hba <= 10
            and self.hbd <= 5
        )


@dataclass
class LigandLibrary:
    name:    str            = "Untitled Library"
    path:    str            = ""    # path to saved JSON index
    entries: List[LigandEntry] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def add(self, entry: LigandEntry):
        self.entries.append(entry)

    def add_many(self, entries: List[LigandEntry]):
        self.entries.extend(entries)

    def remove(self, name: str):
        self.entries = [e for e in self.entries if e.name != name]

    def clear(self):
        self.entries.clear()

    def __len__(self) -> int:
        return len(self.entries)

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #

    def selected(self) -> List[LigandEntry]:
        return [e for e in self.entries if e.selected]

    def filter(self, query: str) -> List[LigandEntry]:
        q = query.lower()
        return [e for e in self.entries
                if q in e.name.lower() or q in e.smiles.lower()
                or q in e.activity.lower() or any(q in t for t in e.tags)]

    def sort_by(self, key: str = "name", ascending: bool = True) -> List[LigandEntry]:
        def _key(e: LigandEntry):
            v = getattr(e, key, e.name)
            return v if v is not None else ""
        return sorted(self.entries, key=_key, reverse=not ascending)

    def lipinski_pass(self) -> List[LigandEntry]:
        return [e for e in self.entries if e.passes_lipinski()]

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def save(self, path: str = ""):
        target = path or self.path
        if not target:
            raise ValueError("No path specified for library save.")
        data = {
            "name":    self.name,
            "entries": [asdict(e) for e in self.entries],
        }
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.path = target

    @classmethod
    def load(cls, path: str) -> "LigandLibrary":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        lib = cls(name=data.get("name", "Library"), path=path)
        for d in data.get("entries", []):
            lib.add(LigandEntry(**d))
        return lib

    # ------------------------------------------------------------------ #
    # Export helpers
    # ------------------------------------------------------------------ #

    def to_smiles_csv(self, path: str):
        """Write name,smiles,activity CSV for docking engine."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for e in self.selected():
                writer.writerow(e.to_smiles_row())

    def to_smiles_list(self) -> List[list]:
        """Return list of [name, smiles, activity] for all selected entries."""
        return [e.to_smiles_row() for e in self.selected()]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_smiles_csv(path: str, name_col: int = 0, smiles_col: int = 1,
                    activity_col: Optional[int] = 2,
                    skip_header: bool = True) -> LigandLibrary:
    """
    Load a SMILES CSV file.
    Expected columns: [name, smiles, activity?, ...]
    Returns a LigandLibrary.
    """
    lib = LigandLibrary(name=Path(path).stem, path=path + ".lib.json")
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if skip_header and rows and not _looks_like_smiles(rows[0][smiles_col] if len(rows[0]) > smiles_col else ""):
        rows = rows[1:]

    for row in rows:
        if not row:
            continue
        try:
            name   = row[name_col].strip()   if len(row) > name_col   else f"LIG{len(lib)+1:04d}"
            smiles = row[smiles_col].strip() if len(row) > smiles_col else ""
            act    = row[activity_col].strip() if (activity_col is not None
                                                   and len(row) > activity_col) else ""
            if not smiles:
                continue
            lib.add(LigandEntry(name=name, smiles=smiles, activity=act, source=path))
        except IndexError:
            continue
    return lib


def load_sdf(path: str) -> LigandLibrary:
    """
    Parse an SDF file (plain or .gz) into a LigandLibrary.
    Extracts _Name field and, if present, SMILES field or generates from ATOM block.
    Does NOT require RDKit — uses minimal text parsing.
    """
    lib = LigandLibrary(name=Path(path).stem)
    text = _read_sdf_text(path)
    molecules = text.strip().split("$$$$")
    idx = 0
    for mol_block in molecules:
        mol_block = mol_block.strip()
        if not mol_block:
            continue
        lines = mol_block.splitlines()
        name = lines[0].strip() if lines else f"MOL{idx:04d}"
        if not name:
            name = f"MOL{idx:04d}"
        smiles = ""
        activity = ""
        # Try to extract a SMILES property
        for i, line in enumerate(lines):
            if line.strip().upper() in ("> <SMILES>", "> <smiles>"):
                smiles = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if line.strip().upper() in ("> <ACTIVITY>", "> <IC50>", "> <KI>"):
                activity = lines[i + 1].strip() if i + 1 < len(lines) else ""
        lib.add(LigandEntry(
            name=name, smiles=smiles, activity=activity, source=path
        ))
        idx += 1
    return lib


def load_pdbqt_folder(folder: str) -> LigandLibrary:
    """
    Load all *.pdbqt files from a folder as ligand entries.
    """
    lib = LigandLibrary(name=Path(folder).name)
    for fname in sorted(os.listdir(folder)):
        if fname.endswith(".pdbqt"):
            stem = fname[:-6]
            fpath = os.path.join(folder, fname)
            lib.add(LigandEntry(name=stem, pdbqt=fpath, source=folder))
    return lib


def load_smiles_string(smiles: str, name: str = "") -> LigandEntry:
    """Create a single LigandEntry from a SMILES string."""
    if not name:
        name = f"LIG_{hashlib.md5(smiles.encode()).hexdigest()[:6]}"
    return LigandEntry(name=name, smiles=smiles, source="manual")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _looks_like_smiles(s: str) -> bool:
    """Very rough check: SMILES contains C, N, O, brackets, rings."""
    return bool(re.search(r'[CNOSFPcnos\[\]()=#@]', s))


def _read_sdf_text(path: str) -> str:
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return f.read()
    else:
        return Path(path).read_text(encoding="utf-8", errors="replace")
