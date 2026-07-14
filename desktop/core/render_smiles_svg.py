from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a SMILES string to SVG with RDKit.")
    parser.add_argument("--smiles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--anchors-output", default="")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=320)
    parser.add_argument("--theme", choices=("light", "dark"), default="dark")
    return parser.parse_args()


def _atom_name(atom) -> str:
    info = atom.GetPDBResidueInfo()
    if info is not None:
        name = (info.GetName() or "").strip()
        if name:
            return name
    for prop in ("_TriposAtomName", "_Name"):
        if atom.HasProp(prop):
            name = atom.GetProp(prop).strip()
            if name:
                return name
    return ""


def _load_source_mol(path: str):
    from rdkit import Chem
    source = Path(path)
    if not source.is_file():
        return None
    suffix = source.suffix.lower()
    mol = None
    if suffix == ".pdb":
        mol = Chem.MolFromPDBFile(str(source), sanitize=False, removeHs=False)
    elif suffix in (".sdf", ".mol"):
        if suffix == ".sdf":
            supplier = Chem.SDMolSupplier(str(source), removeHs=False, sanitize=False)
            mol = next((m for m in supplier if m is not None), None)
        else:
            mol = Chem.MolFromMolFile(str(source), sanitize=False, removeHs=False)
    elif suffix == ".mol2":
        mol = Chem.MolFromMol2File(str(source), sanitize=False, removeHs=False)
    if mol is None:
        return None
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        pass
    return mol


def _copy_with_orig_idx(mol):
    from rdkit import Chem
    copied = Chem.Mol(mol)
    for atom in copied.GetAtoms():
        atom.SetIntProp("_orig_idx", atom.GetIdx())
    return copied


def _prepare_depiction_mol(smiles: str, source_path: str = ""):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    template_mol = Chem.MolFromSmiles(smiles)
    if template_mol is None:
        raise ValueError("RDKit could not parse the SMILES string.")

    if not source_path:
        return template_mol

    source_mol = _load_source_mol(source_path)
    if source_mol is None:
        return template_mol

    try:
        source_named = Chem.Mol(source_mol)
        for atom in source_named.GetAtoms():
            atom_name = _atom_name(atom)
            if atom_name:
                atom.SetProp("_source_atom_name", atom_name)
        source_match = Chem.RemoveHs(source_named)
        template_match = Chem.RemoveHs(Chem.Mol(template_mol))
        assigned = AllChem.AssignBondOrdersFromTemplate(template_match, source_match)
        if assigned is not None:
            return assigned
    except Exception:
        pass
    return template_mol


def _build_anchor_map(depiction_mol, drawer) -> dict[str, list[float]]:
    anchors: dict[str, list[float]] = {}
    for atom in depiction_mol.GetAtoms():
        atom_name = (
            atom.GetProp("_source_atom_name").strip()
            if atom.HasProp("_source_atom_name")
            else _atom_name(atom)
        )
        if not atom_name:
            continue
        pt = drawer.GetDrawCoords(atom.GetIdx())
        anchors[atom_name] = [float(pt.x), float(pt.y)]
    return anchors


def render_smiles_depiction(
    smiles: str,
    width: int,
    height: int,
    theme: str,
    source_path: str = "",
) -> tuple[str, dict[str, list[float]]]:
    from rdkit.Chem import rdDepictor
    from rdkit.Chem.Draw import MolDraw2DSVG

    mol = _prepare_depiction_mol(smiles, source_path=source_path)

    rdDepictor.SetPreferCoordGen(True)
    rdDepictor.Compute2DCoords(mol)

    drawer = MolDraw2DSVG(max(width, 120), max(height, 120))
    opts = drawer.drawOptions()
    opts.padding = 0.05
    opts.baseFontSize = 1.0
    opts.minFontSize = 10
    opts.maxFontSize = 40
    opts.annotationFontScale = 0.6

    if theme == "light":
        opts.backgroundColour = (1.0, 1.0, 1.0, 1.0)
        opts.updateAtomPalette(
            {
                6: (0.20, 0.20, 0.20),
                7: (0.13, 0.47, 0.71),
                8: (1.00, 0.05, 0.05),
                16: (1.00, 0.78, 0.00),
                9: (0.56, 0.88, 0.31),
                15: (1.00, 0.50, 0.00),
                17: (0.12, 0.94, 0.12),
                35: (0.65, 0.16, 0.16),
                53: (0.58, 0.00, 0.58),
            }
        )
    else:
        from rdkit.Chem.Draw.rdMolDraw2D import SetDarkMode

        SetDarkMode(opts)
        opts.backgroundColour = (0.059, 0.067, 0.090, 1.0)

    drawer.DrawMolecule(mol)
    try:
        anchors = _build_anchor_map(mol, drawer)
    except Exception:
        anchors = {}
    drawer.FinishDrawing()
    return drawer.GetDrawingText(), anchors


def main() -> int:
    args = _parse_args()
    svg, anchors = render_smiles_depiction(
        args.smiles,
        args.width,
        args.height,
        args.theme,
        source_path=args.source,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    if args.anchors_output:
        anchor_path = Path(args.anchors_output)
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.write_text(json.dumps(anchors), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
