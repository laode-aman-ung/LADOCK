"""
LADOCK Project Model
Manages the project directory structure and metadata.

A LADOCK project *is* a docking job directory: creating a project and
"generating a job directory" produce the exact same layout, the one every
panel and the docking engine actually read from and write to.

Project / job layout:
  <project_root>/
  ├── target_input/       Raw receptor/target files (PDB) to prepare
  ├── ligand_input/       Raw ligand files (test ligands, any format)
  ├── receptor_ready/     Prepared receptors (docking-ready)
  ├── results/            CSV result files
  ├── logs/               Log files
  └── project.json        Project metadata

Per-run working output is created on demand under docking_runs/<run>/output/
by the docking panels; there is no shared top-level output/ directory.
"""

import os
import json
import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional


# Single source of truth for the job/project directory layout.
# Note: no ligand_ready/ (ligand prep was removed) and no top-level output/
# (per-run output lives under docking_runs/<run>/output, created on demand).
SUBDIRS = ["target_input", "ligand_input", "receptor_ready",
           "results", "logs"]

# Backwards-compatible alias used by older UI code.
LEGACY_SUBDIRS = tuple(SUBDIRS)


@dataclass
class LADOCKProject:
    name: str
    root: str
    created_at: str = ""

    # ------------------------------------------------------------------ #
    # Class methods
    # ------------------------------------------------------------------ #

    @classmethod
    def create(cls, root: str, name: str) -> "LADOCKProject":
        """Create a new project at `root`."""
        root = str(Path(root).resolve())
        os.makedirs(root, exist_ok=True)
        for sub in SUBDIRS:
            os.makedirs(os.path.join(root, sub), exist_ok=True)

        project = cls(
            name=name,
            root=root,
            created_at=datetime.datetime.now().isoformat()
        )
        project.save()
        return project

    @classmethod
    def load(cls, root: str) -> "LADOCKProject":
        """Load an existing project from its root directory (or project.json path)."""
        root = str(Path(root).resolve())
        # Accept either the directory or the project.json file itself
        if root.endswith("project.json") and os.path.isfile(root):
            root = str(Path(root).parent)
        meta_file = os.path.join(root, "project.json")
        if not os.path.exists(meta_file):
            raise FileNotFoundError(f"No LADOCK project found at: {root}")
        with open(meta_file, 'r') as f:
            data = json.load(f)
        data.pop("runs", None)   # legacy field, no longer used
        project = cls(**data)
        return project

    # ------------------------------------------------------------------ #
    # Instance methods
    # ------------------------------------------------------------------ #

    def save(self):
        """Persist project metadata to project.json."""
        meta_file = os.path.join(self.root, "project.json")
        data = asdict(self)
        with open(meta_file, 'w') as f:
            json.dump(data, f, indent=2)

    def path(self, *parts) -> str:
        """Return absolute path under project root."""
        return os.path.join(self.root, *parts)


def create_legacy_job_directory(base_dir: Optional[str] = None,
                                unique: bool = False) -> str:
    """
    Quick-create a docking job directory. This is the "Generate Job Directory"
    shortcut: it builds a full LADOCK project (same layout as New Project,
    including project.json) with an auto-generated name and no name prompt.

    Parameters
    ----------
    base_dir : str, optional
        Parent directory to create the job folder in. Defaults to a stable
        per-user location (~/LADOCK_jobs) instead of the current working
        directory, so the folder does not land wherever the app happened to
        be launched from.
    unique : bool
        When True the job folder is timestamped (dock_YYYYmmdd_HHMMSS) so
        regenerating never mixes inputs/outputs from a previous run.

    Returns the absolute path of the created job directory (== project root).
    """
    if base_dir is None:
        base_dir = str((Path.home() / "LADOCK_jobs").resolve())

    name = "dock"
    if unique:
        name = "dock_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    project = LADOCKProject.create(os.path.join(base_dir, name), name)
    return project.root
