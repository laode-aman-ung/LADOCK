"""
LADOCK Docking Engine
Refactored from docking.py — GUI-agnostic, uses callback for output.

Instead of writing directly to a tkinter Text widget, all output goes
through an optional `log_callback(message: str)` function.
"""

import os
import glob
import shutil
import itertools

import numpy as np
import pandas as pd
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
from os.path import basename

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

try:
    from ladeep.utility import (
        extract_gz, download_file_with_retry, is_smiles_valid,
        vina_energy, mol_opt, adfr_energy, get_residues_within_distance,
        run_command, extract_molecule, calculate_molecule_center,
        move_molecule_to_target_center, processing_ligand, process_smi,
        process_sdf, developer_note, developer_contact, citation_list,
        print_dev, delete_files_except_pdb, pdb_to_smiles
    )
    LADEEP_AVAILABLE = True
except ImportError:
    LADEEP_AVAILABLE = False


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _log(callback, message: str):
    """Send message to callback if provided, else print."""
    if callback is not None:
        callback(message)
    else:
        print(message)


def adgpu_energy(dlg_path: str):
    """Parse best Free Energy of Binding from AutoDock-GPU .dlg output.

    Returns the FEB (kcal/mol) of cluster rank 1, or None if not found.
    DLG RANKING line format:
      <ClusterRank> <RunRank> <Run> <FEB> <RMSD1> <RMSD2>  RANKING
    """
    if not os.path.isfile(dlg_path):
        return None
    try:
        with open(dlg_path) as f:
            for line in f:
                if 'RANKING' in line:
                    parts = line.split()
                    if len(parts) >= 4 and parts[0] == '1':
                        return float(parts[3])
    except (OSError, ValueError):
        pass
    return None


def write_molecule_to_pdb(startsWith, atom_coordinates, endsWith, output_path):
    with open(output_path, 'w') as file:
        for start, (x, y, z), end in zip(startsWith, atom_coordinates, endsWith):
            line = f"{start}    {x:8.3f}{y:8.3f}{z:8.3f}  {end}\n"
            file.write(line)


def create_vina_config(ligand_pdb, box_size):
    startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)
    center = calculate_molecule_center(atom_coordinates)
    center_x, center_y, center_z = center
    config_file = 'config.txt'
    with open(config_file, 'w') as f:
        f.write(f"size_x = {box_size[0]}\n")
        f.write(f"size_y = {box_size[1]}\n")
        f.write(f"size_z = {box_size[2]}\n")
        f.write(f"center_x = {center_x:.3f}\n")
        f.write(f"center_y = {center_y:.3f}\n")
        f.write(f"center_z = {center_z:.3f}\n")
        f.write("# Script written by LADOCK\n")

    return center_x, center_y, center_z


# ---------------------------------------------------------------------------
# Ligand preparation
# ---------------------------------------------------------------------------

def generate_ligand_pdbqt(prepare_ligand, smiles, reference_center):
    try:
        ligand_name = smiles[0]
        smi = smiles[1]
        activity = smiles[2] if len(smiles) >= 3 and smiles[2] else 'NaN'
        others = smiles[3:] if len(smiles) > 3 else None

        if os.path.exists(os.path.join('.', f'{ligand_name}.pdbqt')):
            return smi, ligand_name, activity, others

        ligand_pdb = f'{ligand_name}.pdb'
        if not os.path.exists(os.path.join('.', ligand_pdb)):
            mol = mol_opt(smi)
            if mol is None:
                return None
            mol_block = Chem.MolToPDBBlock(mol)
            with open(ligand_pdb, 'w') as pdb_file:
                pdb_file.write(mol_block)

        startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)
        moved_atom_coordinates = move_molecule_to_target_center(
            atom_coordinates, reference_center
        )

        moved_ligand_pdb_path = f'{ligand_name}_tmp.pdb'
        write_molecule_to_pdb(startsWith, moved_atom_coordinates, endsWith, moved_ligand_pdb_path)
        ligand_pdbqt = f'{ligand_name}.pdbqt'
        run_command(f'{prepare_ligand} -l {moved_ligand_pdb_path} -o {ligand_pdbqt}')
        os.remove(ligand_pdb)
        os.rename(moved_ligand_pdb_path, f'{ligand_name}.pdb')

        return smi, ligand_name, activity, others

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Receptor preparation
# ---------------------------------------------------------------------------

def generate_receptor_pdbqt(prepare_receptor, receptor_pdb):
    try:
        receptor_name = os.path.basename(receptor_pdb).split('.')[0]
        receptor_pdbqt = f'{receptor_name}.pdbqt'
        counter = 1
        while os.path.exists(receptor_pdbqt):
            receptor_pdbqt = f'{receptor_name}_{counter}.pdbqt'
            counter += 1
        run_command(f'{prepare_receptor} -r {receptor_pdb} -A hydrogens -o {receptor_pdbqt}')
        return receptor_pdbqt
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


# ---------------------------------------------------------------------------
# Reference docking
# ---------------------------------------------------------------------------

def docking_reference(output_model_dir, log_callback,
                      agfr, adfr, prepare_gpf, spacing, n_poses, exhaustiveness,
                      cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand,
                      box_size, listmode, sf_types, columns, csv_result,
                      receptor_pdb, reference_pdb, flexible_residues,
                      vina_path, ad4_path, ag4_path, autodockgpu=None, vinagpu=None):

    startsWithR, atom_coordinatesR, endsWithR = extract_molecule(reference_pdb)
    reference_center = calculate_molecule_center(atom_coordinatesR)

    npts = ",".join(map(str, box_size))
    receptor_name = os.path.basename(receptor_pdb).split('.')[0]
    reference_name = os.path.basename(reference_pdb).split('.')[0]
    reference_pdbqt = f'{reference_name}.pdbqt'
    combine_ref = receptor_name
    combine_ref_pdbqt = f'{combine_ref}.pdbqt'

    run_command(f'{prepare_ligand} -l {reference_pdb} -o {reference_pdbqt}')
    run_command(f'{prepare_receptor} -r {receptor_pdb} -A hydrogens -o {combine_ref_pdbqt}')
    run_command(f'{prepare_flexreceptor} -r {combine_ref_pdbqt} -s {flexible_residues}')
    smiles_reference = pdb_to_smiles(reference_pdb)

    ref_energy = []
    for mode in listmode:
        if mode == "rigid":
            for sf_type in sf_types:
                if sf_type == "vina":
                    run_command(f'{vina_path} --ligand {reference_pdbqt} --receptor {combine_ref_pdbqt} --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "vinardo":
                    run_command(f'{vina_path} --ligand {reference_pdbqt} --receptor {combine_ref_pdbqt} --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "ad4":
                    run_command(f'{prepare_gpf} -l {reference_pdbqt} -r {combine_ref_pdbqt} -p spacing={spacing} -p npts="{npts}" -y -o {combine_ref}.gpf')
                    run_command(f'{ag4_path} -p {combine_ref}.gpf -l {combine_ref}.glg')
                    run_command(f'{vina_path} --ligand {reference_pdbqt} --maps {combine_ref} --scoring ad4 --exhaustiveness {exhaustiveness} --cpu {cpu} --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "ad4gpu" and autodockgpu:
                    run_command(f'{prepare_gpf} -l {reference_pdbqt} -r {combine_ref_pdbqt} -p spacing={spacing} -p npts="{npts}" -y -o {combine_ref}.gpf')
                    run_command(f'{ag4_path} -p {combine_ref}.gpf -l {combine_ref}.glg')
                    run_command(f'{autodockgpu} --ffile {combine_ref}.maps.fld --lfile {reference_pdbqt} --nrun {n_poses}')
                    energy = adgpu_energy(f'{reference_name}.dlg')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "vinagpu" and vinagpu:
                    run_command(f'{vinagpu} --receptor {combine_ref_pdbqt} --ligand {reference_pdbqt} --config config.txt --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})

        elif mode == "flexible":
            for sf_type in sf_types:
                if sf_type == "vina":
                    run_command(f'{vina_path} --receptor {combine_ref}_rigid.pdbqt --flex {combine_ref}_flex.pdbqt --ligand {reference_pdbqt} --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "vinardo":
                    run_command(f'{vina_path} --receptor {combine_ref}_rigid.pdbqt --flex {combine_ref}_flex.pdbqt --ligand {reference_pdbqt} --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "ad4":
                    cx, cy, cz = reference_center
                    sx, sy, sz = box_size
                    run_command(f'{agfr} -r {combine_ref_pdbqt} -l {reference_pdbqt} -b user {cx:.3f} {cy:.3f} {cz:.3f} {sx} {sy} {sz} -o ligPocket')
                    run_command(f'{adfr} -l {reference_pdbqt} -t ligPocket.trg --jobName rigid --nbRuns 8 --maxEvals 200000 -O --seed 1')
                    energy = adfr_energy(f'{reference_name}_rigid_summary.dlg')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type in ("ad4gpu", "vinagpu"):
                    _log(log_callback, 'flexible receptor is not supported with GPU.')
                    continue

    # Copy reference output to output dir
    source_file = f"{reference_name}_out.pdbqt"
    destination_file = os.path.join(output_model_dir, f"{reference_name}_out.pdbqt")
    try:
        shutil.copy(source_file, destination_file)
    except Exception as e:
        _log(log_callback, f"Warning: could not copy reference output — {e}")

    results = {
        "ligand_id": reference_name,
        "smiles": smiles_reference,
        "activity": "NaN",
        **{key: value for energy_dict in ref_energy for key, value in energy_dict.items()},
    }
    df_result = pd.DataFrame([results], columns=columns)
    df_result.to_csv(csv_result, mode='a', header=False, index=False)

    formatted_ref = (
        f"{reference_name.upper()} "
        + ", ".join([f'{k}: {v}' for ed in ref_energy for k, v in ed.items()])
        + " in kcal/mol"
    )
    _log(log_callback, formatted_ref)

    return (ref_energy, reference_name, reference_center, combine_ref, smiles_reference)


# ---------------------------------------------------------------------------
# Multiple ligand docking (sequential)
# ---------------------------------------------------------------------------

def multiple_docking(log_callback, agfr, adfr, prepare_gpf, input_file_saved,
                     output_file_saved, arrangement_type, elements, spacing,
                     n_poses, exhaustiveness, cpu, prepare_flexreceptor,
                     prepare_receptor, prepare_ligand, box_size, listmode,
                     sf_types, columns, receptor_pdb, smiles_list,
                     flexible_residues, results_ref, output_model_dir, csv_result,
                     vina_path, ad4_path, ag4_path, autodockgpu=None, vinagpu=None):
    try:
        npts = ",".join(map(str, box_size))
        combine_pdbqt = generate_receptor_pdbqt(prepare_receptor, receptor_pdb)
        combine_name = os.path.basename(combine_pdbqt).split('.')[0]
        run_command(f'{prepare_flexreceptor} -r {combine_pdbqt} -s {flexible_residues}')

        reference_center = results_ref[2]
        combine_ref = results_ref[3]

        for n in elements:
            n = int(n)
            satuan = " ligands" if n == 1 else " ligand pairs"
            if arrangement_type == 'combination':
                arrangements = itertools.combinations(smiles_list.values, n)
            elif arrangement_type == 'permutation':
                arrangements = itertools.permutations(smiles_list.values, n)
            else:
                raise ValueError("Invalid arrangement_type: use 'combination' or 'permutation'.")

            for arrangement in tqdm(arrangements, desc="Processing", unit=satuan):
                try:
                    smile_list, ligand_name_list, activities_list, ligand_pdbqt_list = [], [], [], []
                    all_energy = []

                    for smiles in arrangement:
                        result = generate_ligand_pdbqt(prepare_ligand, smiles, reference_center)
                        if result is None:
                            continue
                        smi, ligand_name, activity, _ = result
                        ligand_pdbqt = f"{ligand_name}.pdbqt"
                        ligand_pdb = f"{ligand_name}.pdb"
                        smile_list.append(smi)
                        activities_list.append(str(activity))
                        ligand_name_list.append(ligand_name)
                        ligand_pdbqt_list.append(ligand_pdbqt)

                    if n > 1:
                        smi = "/".join(smile_list)
                        ligand_name = "_".join(ligand_name_list)
                        activities = "/".join(activities_list)
                        ligand_pdbqt = " ".join(ligand_pdbqt_list)
                    else:
                        smi = smile_list[0]
                        ligand_name = ligand_name_list[0]
                        activities = activities_list[0]
                        ligand_pdbqt = ligand_pdbqt_list[0]

                    for mode in listmode:
                        if mode == "rigid":
                            for sf_type in sf_types:
                                if sf_type == "vina":
                                    run_command(f'{vina_path} --ligand {ligand_pdbqt} --receptor {combine_pdbqt} --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                    energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                elif sf_type == "vinardo":
                                    run_command(f'{vina_path} --ligand {ligand_pdbqt} --receptor {combine_pdbqt} --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                    energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                elif sf_type == "ad4":
                                    run_command(f'{prepare_gpf} -l {ligand_pdbqt} -r {combine_pdbqt} -p spacing={spacing} -p npts="{npts}" -i {combine_ref}.gpf -o {combine_name}.gpf')
                                    run_command(f'{ag4_path} -p {combine_name}.gpf -l {combine_name}.glg')
                                    run_command(f'{vina_path} --ligand {ligand_pdbqt} --maps {combine_name} --scoring ad4 --exhaustiveness {exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                    energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    for tmp in [f'{combine_name}.gpf', f'{combine_name}.glg']:
                                        if os.path.exists(tmp):
                                            os.remove(tmp)
                                elif sf_type == "ad4gpu" and autodockgpu:
                                    run_command(f'{prepare_gpf} -l {ligand_pdbqt} -r {combine_pdbqt} -p spacing={spacing} -p npts="{npts}" -i {combine_ref}.gpf -o {combine_name}.gpf')
                                    run_command(f'{ag4_path} -p {combine_name}.gpf -l {combine_name}.glg')
                                    run_command(f'{autodockgpu} --ffile {combine_name}.maps.fld --lfile {ligand_pdbqt} --nrun {n_poses}')
                                    energy = adgpu_energy(f'{ligand_name}.dlg')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                elif sf_type == "vinagpu" and vinagpu:
                                    run_command(f'{vinagpu} --receptor {combine_pdbqt} --ligand {ligand_pdbqt} --config config.txt --out {ligand_name}_out.pdbqt')
                                    energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})

                        elif mode == "flexible":
                            for sf_type in sf_types:
                                if sf_type == "vina":
                                    run_command(f'{vina_path} --receptor {combine_name}_rigid.pdbqt --flex {combine_name}_flex.pdbqt --ligand {ligand_pdbqt} --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                    energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                elif sf_type == "vinardo":
                                    run_command(f'{vina_path} --receptor {combine_name}_rigid.pdbqt --flex {combine_name}_flex.pdbqt --ligand {ligand_pdbqt} --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                    energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                elif sf_type == "ad4":
                                    run_command(f'{adfr} -l {ligand_pdbqt} -t ligPocket.trg --jobName rigid --nbRuns 8 --maxEvals 200000 -O --seed 1')
                                    energy = adfr_energy(f'{ligand_name}_rigid_summary.dlg')
                                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                elif sf_type in ("ad4gpu", "vinagpu"):
                                    _log(log_callback, 'flexible receptor is not supported with GPU.')
                                    continue

                except Exception:
                    continue

                # Save/move input & output files
                if input_file_saved.lower() == "true":
                    if not os.path.exists(os.path.join(output_model_dir, ligand_pdb)):
                        shutil.move(ligand_pdb, output_model_dir)
                if output_file_saved.lower() == "true":
                    shutil.copy(f'{ligand_name}_out.pdbqt', output_model_dir)

                # Clean temp files
                for f in glob.glob(f"{ligand_name}*"):
                    os.remove(f)

                all_results = {
                    "ligand_id": ligand_name.upper(),
                    "smiles": smi,
                    "activity": activities,
                    **{key: value for ed in all_energy for key, value in ed.items()}
                }
                df_all_result = pd.DataFrame([all_results], columns=columns)
                df_all_result.to_csv(csv_result, mode='a', header=False, index=False)

                formatted_result = (
                    f"{ligand_name.upper()} "
                    + ", ".join([f'{k}: {v}' for ed in all_energy for k, v in ed.items()])
                    + " in kcal/mol"
                )
                _log(log_callback, formatted_result)

    except Exception as e:
        _log(log_callback, f"Error in multiple_docking: {e}")


# ---------------------------------------------------------------------------
# Multiple ligand docking (parallel)
# ---------------------------------------------------------------------------

def multiple_docking_parallel(log_callback, agfr, adfr, prepare_gpf, input_file_saved,
                               output_file_saved, arrangement_type, elements, spacing,
                               n_poses, exhaustiveness, cpu, prepare_flexreceptor,
                               prepare_receptor, prepare_ligand, box_size, listmode,
                               sf_types, columns, receptor_pdb, smiles_list,
                               flexible_residues, results_ref, output_model_dir, csv_result,
                               vina_path, ad4_path, ag4_path, autodockgpu=None, vinagpu=None):

    npts = ",".join(map(str, box_size))
    combine_pdbqt = generate_receptor_pdbqt(prepare_receptor, receptor_pdb)
    combine_name = os.path.basename(combine_pdbqt).split('.')[0]
    run_command(f'{prepare_flexreceptor} -r {combine_pdbqt} -s {flexible_residues}')
    reference_center = results_ref[2]
    combine_ref = results_ref[3]

    def process_arrangement(arrangement):
        smile_list, ligand_name_list, activities_list, ligand_pdbqt_list = [], [], [], []
        all_energy = []

        for smiles in arrangement:
            result = generate_ligand_pdbqt(prepare_ligand, smiles, reference_center)
            if result is None:
                return
            smi, ligand_name, activity, _ = result
            ligand_pdbqt = f"{ligand_name}.pdbqt"
            ligand_pdb = f"{ligand_name}.pdb"
            smile_list.append(smi)
            activities_list.append(str(activity))
            ligand_name_list.append(ligand_name)
            ligand_pdbqt_list.append(ligand_pdbqt)

        if n > 1:
            smi = "/".join(smile_list)
            ligand_name = "_".join(ligand_name_list)
            activities = "/".join(activities_list)
            ligand_pdbqt = " ".join(ligand_pdbqt_list)
        else:
            smi = smile_list[0]
            ligand_name = ligand_name_list[0]
            activities = activities_list[0]
            ligand_pdbqt = ligand_pdbqt_list[0]

        for mode in listmode:
            if mode == "rigid":
                for sf_type in sf_types:
                    if sf_type == "vina":
                        run_command(f'{vina_path} --ligand {ligand_pdbqt} --receptor {combine_pdbqt} --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                    elif sf_type == "vinardo":
                        run_command(f'{vina_path} --ligand {ligand_pdbqt} --receptor {combine_pdbqt} --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                    elif sf_type == "ad4":
                        run_command(f'{prepare_gpf} -l {ligand_pdbqt} -r {combine_pdbqt} -p spacing={spacing} -p npts="{npts}" -i {combine_ref}.gpf -o {combine_name}.gpf')
                        run_command(f'{ag4_path} -p {combine_name}.gpf -l {combine_name}.glg')
                        run_command(f'{vina_path} --ligand {ligand_pdbqt} --maps {combine_name} --scoring ad4 --exhaustiveness {exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
            elif mode == "flexible":
                for sf_type in sf_types:
                    if sf_type == "vina":
                        run_command(f'{vina_path} --receptor {combine_name}_rigid.pdbqt --flex {combine_name}_flex.pdbqt --ligand {ligand_pdbqt} --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                    elif sf_type == "ad4":
                        run_command(f'{adfr} -l {ligand_pdbqt} -t ligPocket.trg --jobName rigid --nbRuns 8 --maxEvals 200000 -O --seed 1')
                        energy = adfr_energy(f'{ligand_name}_rigid_summary.dlg')
                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                    elif sf_type in ("ad4gpu", "vinagpu"):
                        continue

        if input_file_saved.lower() == "true":
            if not os.path.exists(os.path.join(output_model_dir, ligand_pdb)):
                shutil.move(ligand_pdb, output_model_dir)
        if output_file_saved.lower() == "true":
            shutil.copy(f'{ligand_name}_out.pdbqt', output_model_dir)

        for f in glob.glob(f"{ligand_name}*"):
            os.remove(f)

        all_results = {
            "ligand_id": ligand_name.upper(),
            "smiles": smi,
            "activity": activities,
            **{key: value for ed in all_energy for key, value in ed.items()}
        }
        df_all_results = pd.DataFrame([all_results], columns=columns)
        df_all_results.to_csv(csv_result, mode='a', header=False, index=False)

        formatted_result = (
            f"{ligand_name.upper()} "
            + ", ".join([f'{k}: {v}' for ed in all_energy for k, v in ed.items()])
            + " in kcal/mol"
        )
        _log(log_callback, formatted_result)

    for n in elements:
        n = int(n)
        if arrangement_type == 'combination':
            arrangements = itertools.combinations(smiles_list.values, n)
        elif arrangement_type == 'permutation':
            arrangements = itertools.permutations(smiles_list.values, n)
        else:
            raise ValueError("Invalid arrangement_type.")

        total_cpus = os.cpu_count() - 2
        max_workers = max(1, total_cpus // (cpu + 1))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for arrangement in arrangements:
                executor.submit(process_arrangement, arrangement)


# ---------------------------------------------------------------------------
# Main docking orchestrator
# ---------------------------------------------------------------------------

def run_docking(log_callback, sf_types, listmode, distance, arrangement_type, elements,
                box_size, spacing, n_poses, exhaustiveness, cpu, parallel_simulation,
                input_file_saved, output_file_saved, vina_path, ad4_path, ag4_path,
                autodockgpu, vinagpu, job_directory, max_workers, agfr, adfr,
                prepare_ligand, prepare_receptor, prepare_gpf, prepare_flexreceptor,
                current_directory):
    """
    Main entry point for docking.
    
    Parameters
    ----------
    log_callback : callable or None
        Function called with (message: str) for all output.
        Pass None to use stdout.
    All other params: same as the original docking() function.
    """
    box_size = [int(value) for value in box_size.split(',')]
    os.chdir(current_directory)

    if LADEEP_AVAILABLE:
        print_dev(developer_note, developer_contact, citation_list)

    model_dirs = [
        d for d in os.listdir(current_directory)
        if os.path.isdir(os.path.join(current_directory, d)) and d.startswith('model')
    ]
    ligand_dir = os.path.join(current_directory, 'ligand_input')
    output_dir = os.path.join(current_directory, 'output')
    os.makedirs(output_dir, exist_ok=True)

    for model_dir in model_dirs:
        model_path = os.path.join(current_directory, model_dir)
        os.chdir(model_path)

        receptor_pdb = next(
            (f for f in os.listdir('.') if f.startswith("rec") and f.endswith(".pdb")), None
        )
        reference_pdb = next(
            (f for f in os.listdir('.') if f.startswith("lig") and f.endswith(".pdb")), None
        )

        if not (receptor_pdb and reference_pdb
                and os.path.exists(receptor_pdb) and os.path.exists(reference_pdb)):
            _log(log_callback, f"Skipping {model_dir.upper()} — receptor or reference ligand not found")
            os.chdir(current_directory)
            continue

        delete_files_except_pdb('.')

        _log(log_callback, f"Processing {model_dir.upper()} — Reference ligand")
        _log(log_callback, f"  Receptor: {receptor_pdb}")
        _log(log_callback, f"  Reference Ligand: {reference_pdb}")

        create_vina_config(reference_pdb, box_size)

        output_model_dir = os.path.join(output_dir, model_dir)
        os.makedirs(output_model_dir, exist_ok=True)

        flexible_residues = get_residues_within_distance(receptor_pdb, reference_pdb, distance)
        flexible_residues = '_'.join(sorted(flexible_residues))

        columns = (
            ["ligand_id", "smiles", "activity"]
            + [f'{mode}_{sf_type}_Energy' for mode in listmode for sf_type in sf_types]
        )
        csv_result = os.path.join(
            output_model_dir,
            f"results_{'_'.join(sf_types)}_{'_'.join(listmode)}.csv"
        )
        pd.DataFrame(columns=columns).to_csv(csv_result, mode='w', header=True, index=False)

        shutil.copy(reference_pdb, output_model_dir)
        results_ref = docking_reference(
            output_model_dir, log_callback, agfr, adfr, prepare_gpf,
            spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor,
            prepare_receptor, prepare_ligand, box_size, listmode, sf_types,
            columns, csv_result, receptor_pdb, reference_pdb, flexible_residues,
            vina_path, ad4_path, ag4_path, autodockgpu, vinagpu
        )

        _log(log_callback, f"Processing {model_dir.upper()} — Test ligands")
        result_list = []

        for n in elements:
            n = int(n)
            for f in os.listdir(ligand_dir):
                if f.endswith('.uri'):
                    with open(os.path.join(ligand_dir, f), 'r') as file:
                        ligand_links = [
                            line.strip() for line in file.readlines()
                            if 'http' in line and not line.strip().startswith('#')
                        ]
                    for ligand_link in ligand_links:
                        ligand_file_base = basename(ligand_link)
                        download_file_with_retry(ligand_link, ligand_file_base)
                        if ligand_file_base.endswith('.gz'):
                            try:
                                ligand_file = extract_gz(ligand_file_base)
                                os.remove(ligand_file_base)
                            except Exception:
                                continue
                        else:
                            ligand_file = ligand_file_base

                        smiles_list = _load_ligand_file(ligand_file)
                        if smiles_list is not None:
                            _run_docking_mode(
                                parallel_simulation, log_callback, agfr, adfr,
                                prepare_gpf, input_file_saved, output_file_saved,
                                arrangement_type, elements, spacing, n_poses,
                                exhaustiveness, cpu, prepare_flexreceptor,
                                prepare_receptor, prepare_ligand, box_size, listmode,
                                sf_types, columns, receptor_pdb, smiles_list,
                                flexible_residues, results_ref, output_model_dir,
                                csv_result, vina_path, ad4_path, ag4_path,
                                autodockgpu, vinagpu
                            )
                else:
                    smiles_list = processing_ligand(os.path.join(ligand_dir, f))
                    if smiles_list is not None:
                        _run_docking_mode(
                            parallel_simulation, log_callback, agfr, adfr,
                            prepare_gpf, input_file_saved, output_file_saved,
                            arrangement_type, elements, spacing, n_poses,
                            exhaustiveness, cpu, prepare_flexreceptor,
                            prepare_receptor, prepare_ligand, box_size, listmode,
                            sf_types, columns, receptor_pdb, smiles_list,
                            flexible_residues, results_ref, output_model_dir,
                            csv_result, vina_path, ad4_path, ag4_path,
                            autodockgpu, vinagpu
                        )

        _log(log_callback, f"Success: {model_dir.upper()}")
        os.chdir(current_directory)

    if LADEEP_AVAILABLE:
        print_dev(developer_note, developer_contact, citation_list)


def _load_ligand_file(ligand_file):
    if ligand_file.endswith(".smi"):
        return process_smi(ligand_file)
    elif ligand_file.endswith(".sdf"):
        return process_sdf(ligand_file)
    return None


def _run_docking_mode(parallel_simulation, *args, **kwargs):
    if parallel_simulation.lower() == 'true':
        multiple_docking_parallel(*args, **kwargs)
    else:
        multiple_docking(*args, **kwargs)


# ---------------------------------------------------------------------------
# Backward-compatibility shim — keeps old docking() signature working
# ---------------------------------------------------------------------------

def docking(result_text, sf_types, listmode, distance, arrangement_type, elements,
            box_size, spacing, n_poses, exhaustiveness, cpu, parallel_simulation,
            input_file_saved, output_file_saved, vina_path, ad4_path, ag4_path,
            autodockgpu, vinagpu, job_directory, max_workers, agfr, adfr,
            prepare_ligand, prepare_receptor, prepare_gpf, prepare_flexreceptor,
            current_directory):
    """
    Backward-compatible wrapper.  `result_text` can be:
      - None  → output goes to stdout
      - a callable(str)  → output goes to that function
      - a tkinter Text widget  → output appended via insert(END, ...)
    """
    if result_text is None or callable(result_text):
        callback = result_text
    else:
        # Legacy tkinter Text widget
        import tkinter as tk
        def callback(msg):
            result_text.insert(tk.END, msg + '\n')
            result_text.yview(tk.END)

    run_docking(
        log_callback=callback,
        sf_types=sf_types, listmode=listmode, distance=distance,
        arrangement_type=arrangement_type, elements=elements,
        box_size=box_size, spacing=spacing, n_poses=n_poses,
        exhaustiveness=exhaustiveness, cpu=cpu,
        parallel_simulation=parallel_simulation,
        input_file_saved=input_file_saved, output_file_saved=output_file_saved,
        vina_path=vina_path, ad4_path=ad4_path, ag4_path=ag4_path,
        autodockgpu=autodockgpu, vinagpu=vinagpu,
        job_directory=job_directory, max_workers=max_workers,
        agfr=agfr, adfr=adfr,
        prepare_ligand=prepare_ligand, prepare_receptor=prepare_receptor,
        prepare_gpf=prepare_gpf, prepare_flexreceptor=prepare_flexreceptor,
        current_directory=current_directory
    )
