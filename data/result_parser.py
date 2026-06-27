"""
LADOCK Result Parser
Parse docking output files (PDBQT, CSV) into structured DataFrames.
"""

import os
import re
import pandas as pd
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# PDBQT energy parser
# ---------------------------------------------------------------------------

def parse_pdbqt_energies(pdbqt_file: str) -> List[dict]:
    """
    Parse all pose energies from a Vina/Vinardo/AD4 output PDBQT file.

    Returns list of dicts:
      [{'pose': 1, 'affinity': -8.2, 'rmsd_lb': 0.0, 'rmsd_ub': 0.0}, ...]
    """
    poses = []
    pattern = re.compile(
        r'REMARK VINA RESULT:\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)'
    )
    if not os.path.exists(pdbqt_file):
        return poses
    with open(pdbqt_file, 'r') as f:
        for line in f:
            m = pattern.match(line)
            if m:
                poses.append({
                    'pose': len(poses) + 1,
                    'affinity': float(m.group(1)),
                    'rmsd_lb': float(m.group(2)),
                    'rmsd_ub': float(m.group(3)),
                })
    return poses


def get_best_energy(pdbqt_file: str) -> Optional[float]:
    """Return best (lowest) binding energy from a PDBQT output file."""
    poses = parse_pdbqt_energies(pdbqt_file)
    if not poses:
        return None
    return min(p['affinity'] for p in poses)


# ---------------------------------------------------------------------------
# CSV result loader
# ---------------------------------------------------------------------------

def load_results_csv(csv_file: str) -> pd.DataFrame:
    """Load a LADOCK results CSV and return a sorted DataFrame."""
    if not os.path.exists(csv_file):
        return pd.DataFrame()
    df = pd.read_csv(csv_file)
    # Sort by first energy column if present
    energy_cols = [c for c in df.columns if c.endswith('_Energy')]
    if energy_cols:
        df = df.sort_values(by=energy_cols[0], ascending=True).reset_index(drop=True)
        df.insert(0, 'rank', range(1, len(df) + 1))
    return df


def find_result_csvs(output_dir: str) -> List[str]:
    """Recursively find all results CSV files under output_dir."""
    result_files = []
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            if f.startswith('results_') and f.endswith('.csv'):
                result_files.append(os.path.join(root, f))
    return sorted(result_files)


def merge_results(csv_files: List[str]) -> pd.DataFrame:
    """Merge multiple results CSVs into one DataFrame."""
    frames = []
    for f in csv_files:
        df = load_results_csv(f)
        if not df.empty:
            df['source_file'] = os.path.basename(f)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# PDBQT pose extractor
# ---------------------------------------------------------------------------

def extract_poses(pdbqt_file: str) -> List[str]:
    """
    Split a multi-pose PDBQT output into individual pose strings.

    Returns list of pose blocks (each is a full PDBQT string for one pose).
    """
    if not os.path.exists(pdbqt_file):
        return []

    poses = []
    current = []
    with open(pdbqt_file, 'r') as f:
        for line in f:
            current.append(line)
            if line.startswith('ENDMDL') or line.startswith('END'):
                if current:
                    poses.append(''.join(current))
                    current = []
    if current:
        poses.append(''.join(current))
    return poses


def get_pose_coordinates(pose_block: str) -> List[Tuple[float, float, float]]:
    """Extract (x, y, z) atom coordinates from a PDBQT pose block."""
    coords = []
    for line in pose_block.splitlines():
        if line.startswith(('ATOM', 'HETATM')):
            try:
                x = float(line[30:38])
                y = float(line[38:46])
                z = float(line[46:54])
                coords.append((x, y, z))
            except ValueError:
                continue
    return coords
