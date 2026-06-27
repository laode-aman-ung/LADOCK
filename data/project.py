"""
LADOCK Project Model
Manages the project directory structure and metadata.

Project layout:
  <project_root>/
  ├── receptors/          PDB receptor files
  ├── ligands/            Ligand input files (SMI, SDF, URI)
  ├── grids/              Grid/config files
  ├── docking_runs/       Per-run working directories
  │   └── <run_id>/
  │       ├── model*/     Model subdirectories (receptor+ref ligand)
  │       ├── ligand_input/
  │       └── output/
  ├── poses/              PDBQT pose files
  ├── results/            CSV result files
  └── logs/               Log files
"""

import os
import json
import uuid
import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional


SUBDIRS = ["receptors", "ligands", "grids", "docking_runs", "poses", "results", "logs"]


@dataclass
class DockingRun:
    run_id: str
    name: str
    created_at: str
    receptor: str = ""
    ligand_file: str = ""
    sf_types: List[str] = field(default_factory=list)
    listmode: List[str] = field(default_factory=list)
    status: str = "pending"   # pending | running | finished | failed
    result_csv: str = ""


@dataclass
class LADOCKProject:
    name: str
    root: str
    created_at: str = ""
    runs: List[DockingRun] = field(default_factory=list)

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
        runs = [DockingRun(**r) for r in data.pop("runs", [])]
        project = cls(**data, runs=runs)
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

    def new_run(self, name: str = "") -> DockingRun:
        """Create a new docking run directory and register it."""
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        run_name = name or f"Run_{len(self.runs) + 1}"
        run_dir = self.path("docking_runs", run_id)
        os.makedirs(run_dir, exist_ok=True)
        os.makedirs(os.path.join(run_dir, "ligand_input"), exist_ok=True)
        os.makedirs(os.path.join(run_dir, "output"), exist_ok=True)

        run = DockingRun(
            run_id=run_id,
            name=run_name,
            created_at=datetime.datetime.now().isoformat()
        )
        self.runs.append(run)
        self.save()
        return run

    def run_dir(self, run: DockingRun) -> str:
        """Return absolute path of a run's working directory."""
        return self.path("docking_runs", run.run_id)

    def update_run(self, run: DockingRun):
        """Update a run record and persist."""
        for i, r in enumerate(self.runs):
            if r.run_id == run.run_id:
                self.runs[i] = run
                break
        self.save()


def create_legacy_job_directory(base_dir: Optional[str] = None) -> str:
    """
    Create the classic LADOCK 'dock/' directory layout.
    Compatible with ladocknogui.py workflow.
    """
    if base_dir is None:
        base_dir = os.getcwd()
    job_dir = os.path.join(base_dir, "dock")
    os.makedirs(job_dir, exist_ok=True)
    os.makedirs(os.path.join(job_dir, "target_input"),   exist_ok=True)
    os.makedirs(os.path.join(job_dir, "ligand_input"),   exist_ok=True)
    os.makedirs(os.path.join(job_dir, "receptor_ready"), exist_ok=True)
    os.makedirs(os.path.join(job_dir, "ligand_ready"),   exist_ok=True)
    os.makedirs(os.path.join(job_dir, "output"),         exist_ok=True)
    return job_dir
