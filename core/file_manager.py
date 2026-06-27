"""
LADOCK File Manager
Handles project directories, file copying, and path resolution.
"""

import os
import shutil
from pathlib import Path
from typing import List, Optional


# Supported ligand input formats
LIGAND_EXTENSIONS = {'.smi', '.sdf', '.mol2', '.mol', '.pdb', '.pdbqt', '.uri'}
RECEPTOR_EXTENSIONS = {'.pdb', '.pdbqt'}


class FileManager:

    def __init__(self, project_root: str):
        self.root = str(Path(project_root).resolve())

    # ------------------------------------------------------------------ #
    # Directory helpers
    # ------------------------------------------------------------------ #

    def ensure_dirs(self):
        """Create all standard project subdirectories."""
        for sub in ["receptors", "ligands", "grids", "docking_runs",
                    "poses", "results", "logs"]:
            os.makedirs(self.path(sub), exist_ok=True)

    def path(self, *parts) -> str:
        return os.path.join(self.root, *parts)

    # ------------------------------------------------------------------ #
    # Receptor management
    # ------------------------------------------------------------------ #

    def add_receptor(self, src: str) -> str:
        """Copy a receptor file into the project receptors/ dir."""
        dest = self.path("receptors", os.path.basename(src))
        shutil.copy2(src, dest)
        return dest

    def list_receptors(self) -> List[str]:
        return self._list_files("receptors", RECEPTOR_EXTENSIONS)

    # ------------------------------------------------------------------ #
    # Ligand management
    # ------------------------------------------------------------------ #

    def add_ligand_file(self, src: str) -> str:
        """Copy a ligand file into the project ligands/ dir."""
        dest = self.path("ligands", os.path.basename(src))
        shutil.copy2(src, dest)
        return dest

    def list_ligand_files(self) -> List[str]:
        return self._list_files("ligands", LIGAND_EXTENSIONS)

    # ------------------------------------------------------------------ #
    # Run directory management
    # ------------------------------------------------------------------ #

    def setup_run_dir(self, run_id: str,
                      receptor_src: str,
                      reference_ligand_src: str,
                      ligand_files: Optional[List[str]] = None) -> str:
        """
        Prepare a docking_runs/<run_id> directory with the classic
        model001/ + ligand_input/ layout expected by docking_engine.

        Returns the run directory path.
        """
        run_dir = self.path("docking_runs", run_id)
        model_dir = os.path.join(run_dir, "model001")
        ligand_input_dir = os.path.join(run_dir, "ligand_input")
        os.makedirs(model_dir, exist_ok=True)
        os.makedirs(ligand_input_dir, exist_ok=True)

        # Receptor must be named rec*.pdb
        rec_name = os.path.basename(receptor_src)
        if not rec_name.startswith('rec'):
            rec_name = 'rec_' + rec_name
        shutil.copy2(receptor_src, os.path.join(model_dir, rec_name))

        # Reference ligand must be named lig*.pdb
        ref_name = os.path.basename(reference_ligand_src)
        if not ref_name.startswith('lig'):
            ref_name = 'lig_' + ref_name
        shutil.copy2(reference_ligand_src, os.path.join(model_dir, ref_name))

        # Ligand library files
        if ligand_files:
            for f in ligand_files:
                shutil.copy2(f, os.path.join(ligand_input_dir, os.path.basename(f)))

        return run_dir

    # ------------------------------------------------------------------ #
    # Results
    # ------------------------------------------------------------------ #

    def collect_results(self, run_id: str) -> List[str]:
        """Return all CSV result files for a given run."""
        from data.result_parser import find_result_csvs
        output_dir = self.path("docking_runs", run_id, "output")
        return find_result_csvs(output_dir)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _list_files(self, subdir: str, extensions) -> List[str]:
        d = self.path(subdir)
        if not os.path.exists(d):
            return []
        return [
            os.path.join(d, f) for f in os.listdir(d)
            if Path(f).suffix.lower() in extensions
        ]
