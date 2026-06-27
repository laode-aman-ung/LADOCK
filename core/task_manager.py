"""
LADOCK Task Manager
Breaks a docking job into discrete pipeline steps and reports progress.

Pipeline steps:
  1. prepare_receptor  — convert receptor PDB → PDBQT
  2. prepare_reference — convert reference ligand PDB → PDBQT
  3. generate_grid     — create config.txt / GPF grids
  4. dock_reference    — dock reference ligand (validation)
  5. prepare_ligands   — convert test ligands → PDBQT
  6. dock_ligands      — run docking for all test ligands
  7. parse_results     — collect and format CSV results
"""

import os
from typing import Callable, List, Optional


PIPELINE_STEPS = [
    "prepare_receptor",
    "prepare_reference",
    "generate_grid",
    "dock_reference",
    "prepare_ligands",
    "dock_ligands",
    "parse_results",
]


class TaskManager:
    """
    Wraps a docking run into sequential steps with progress tracking.

    Usage:
        tm = TaskManager(parameters, log_cb, progress_cb)
        tm.run()
    """

    def __init__(self,
                 parameters: dict,
                 log_callback: Optional[Callable[[str], None]] = None,
                 progress_callback: Optional[Callable[[int, str], None]] = None):
        """
        Parameters
        ----------
        parameters : dict
            Full docking parameter dict (same keys as run_docking()).
        log_callback : callable(message: str)
            Receives log messages.
        progress_callback : callable(percent: int, step_name: str)
            Called after each pipeline step completes.
        """
        self.parameters = parameters
        self._log = log_callback or print
        self._progress = progress_callback or (lambda p, s: None)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        """Execute the full docking pipeline."""
        from engine.docking_engine import run_docking

        step_count = len(PIPELINE_STEPS)
        for i, step in enumerate(PIPELINE_STEPS):
            if self._cancelled:
                self._log("Docking cancelled.")
                return
            percent = int((i / step_count) * 100)
            self._progress(percent, step)
            self._log(f"[Step {i+1}/{step_count}] {step.replace('_', ' ').title()}...")

        # Currently delegates entirely to the engine.
        # Future: split run_docking() so each step can report progress.
        def log_cb(msg):
            self._log(msg)

        run_docking(log_callback=log_cb, **self.parameters)
        self._progress(100, "done")
        self._log("Docking pipeline complete.")

    # ------------------------------------------------------------------ #
    # Static helper — build parameter dict from GUI values
    # ------------------------------------------------------------------ #

    @staticmethod
    def build_parameters(
        sf_types: List[str],
        listmode: List[str],
        distance: float,
        arrangement_type: str,
        elements: List[str],
        box_size: str,
        spacing: float,
        n_poses: int,
        exhaustiveness: int,
        cpu: int,
        parallel_simulation: str,
        input_file_saved: str,
        output_file_saved: str,
        vina_path: str,
        ad4_path: str,
        ag4_path: str,
        autodockgpu: str,
        vinagpu: str,
        job_directory: str,
        agfr: str,
        adfr: str,
        prepare_ligand: str,
        prepare_receptor: str,
        prepare_gpf: str,
        prepare_flexreceptor: str,
    ) -> dict:
        max_workers = max(1, os.cpu_count() // max(cpu, 1))
        return {
            'sf_types': sf_types,
            'listmode': listmode,
            'distance': distance,
            'arrangement_type': arrangement_type,
            'elements': elements,
            'box_size': box_size,
            'spacing': spacing,
            'n_poses': n_poses,
            'exhaustiveness': exhaustiveness,
            'cpu': cpu,
            'parallel_simulation': parallel_simulation,
            'input_file_saved': input_file_saved,
            'output_file_saved': output_file_saved,
            'vina_path': vina_path,
            'ad4_path': ad4_path,
            'ag4_path': ag4_path,
            'autodockgpu': autodockgpu,
            'vinagpu': vinagpu,
            'job_directory': job_directory,
            'max_workers': max_workers,
            'agfr': agfr,
            'adfr': adfr,
            'prepare_ligand': prepare_ligand,
            'prepare_receptor': prepare_receptor,
            'prepare_gpf': prepare_gpf,
            'prepare_flexreceptor': prepare_flexreceptor,
            'current_directory': job_directory,
        }
