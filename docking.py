import rdkit
from rdkit import Chem
from rdkit.Chem import AllChem
import pandas as pd
import subprocess
import os
import glob
import shutil
from tqdm import tqdm
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import itertools
import numpy as np
import argparse
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from tabulate import tabulate
from os.path import basename
from ladeep.utility import extract_gz, download_file_with_retry, is_smiles_valid, vina_energy, mol_opt, adfr_energy, get_residues_within_distance, run_command, extract_molecule, calculate_molecule_center, move_molecule_to_target_center, processing_ligand, process_smi, process_sdf, developer_note, developer_contact, citation_list, print_dev, delete_files_except_pdb, pdb_to_smiles

def read_config_from_file(filename):
    config = {}
    with open(filename, 'r') as file:
        for line in file:
            key, value = map(str.strip, line.split(':'))
            config[key] = value
    return config


def write_molecule_to_pdb(startsWith, atom_coordinates, endsWith, output_path):
    with open(output_path, 'w') as file:
        for start, (x, y, z), end in zip(startsWith, atom_coordinates, endsWith):
            # Format koordinat atom ke dalam format PDB
            line = f"{start}    {x:8.3f}{y:8.3f}{z:8.3f}  {end}\n"
            file.write(line)

def create_vina_config(ligand_pdb, box_size):
    startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)    
    center = calculate_molecule_center(atom_coordinates)
    center_x, center_y, center_z = center
    config_file='config.txt'
    with open(config_file, 'w') as config_file:
        config_file.write(f"size_x = {box_size[0]}\n")
        config_file.write(f"size_y = {box_size[1]}\n")
        config_file.write(f"size_z = {box_size[2]}\n")
        config_file.write(f"center_x = {center_x:.3f}\n")
        config_file.write(f"center_y = {center_y:.3f}\n")
        config_file.write(f"center_z = {center_z:.3f}\n")
        config_file.write("# Script written by:\n")
        config_file.write("# La Ode Aman\n")
        config_file.write("# laodeaman.ai@gmail.com\n")
        config_file.write("# laode_aman@ung.ac.id\n")
        config_file.write("# Universitas Negeri Gorontalo, Indonesia\n")

    print("  Docking parameter:")
    print("\tsize_x =", box_size[0])
    print("\tsize_y =", box_size[1])
    print("\tsize_z =", box_size[2])
    print(f"\tcenter_x = {center_x:.3f}")
    print(f"\tcenter_y = {center_y:.3f}")
    print(f"\tcenter_z = {center_z:.3f}")
    
def generate_ligand_pdbqt(prepare_ligand, smiles, reference_center):
    try:
        ligand_name = smiles[0]
        smi = smiles[1]
        activity = smiles[2] if len(smiles) >= 3 and smiles[2] else 'NaN'
        others = smiles[3:] if len(smiles) > 3 else None

        if os.path.exists(os.path.join('.', f'{ligand_name}.pdbqt')):
            return smi, ligand_name, activity, others
        else:
            ligand_pdb = f'{ligand_name}.pdb'

            if not os.path.exists(os.path.join('.', ligand_pdb)):
                mol = mol_opt(smi)
                if mol is not None:
                    mol_block = Chem.MolToPDBBlock(mol)
                    with open(ligand_pdb, 'w') as pdb_file:
                        pdb_file.write(mol_block)
                else:
                    # Handle the case where mol is None
                    return None

            startsWith, atom_coordinates, endsWith = extract_molecule(ligand_pdb)
            center = calculate_molecule_center(atom_coordinates)

            # Move the ligand to the target box center
            moved_atom_coordinates = move_molecule_to_target_center(atom_coordinates, reference_center)

            # Write the moved ligand to a new PDB file
            moved_ligand_pdb_path = f'{ligand_name}_tmp.pdb'
            write_molecule_to_pdb(startsWith, moved_atom_coordinates, endsWith, moved_ligand_pdb_path)
            ligand_pdbqt = f'{ligand_name}.pdbqt'
            run_command(f'{prepare_ligand} -l {moved_ligand_pdb_path} -o {ligand_pdbqt}')
            os.remove(ligand_pdb)
            os.rename(moved_ligand_pdb_path, f'{ligand_name}.pdb')

            return smi, ligand_name, activity, others

    except Exception as e:
        #print(f"An error occurred: {e}")
        return None

def docking_reference(output_model_dir, result_text, agfr, adfr, prepare_gpf, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, csv_result, receptor_pdb, reference_pdb, flexible_residues, vina_path, ad4_path, ag4_path):
    # Center box target is the center box of reference
       
    startsWithR, atom_coordinatesR, endsWithR = extract_molecule(reference_pdb)
    reference_center = calculate_molecule_center(atom_coordinatesR)

    # Preparation
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
                elif sf_type == "ad4gpu":
                    run_command(f'{prepare_gpf} -l {reference_pdbqt} -r {combine_ref_pdbqt} -p spacing={spacing} -p npts="{npts}" -y -o {combine_ref}.gpf')
                    run_command(f'{ag4_path} -p {combine_ref}.gpf -l {combine_ref}.glg')
                    run_command(f'{autodockgpu} --ffile {combine_ref}.maps.fld --lfile {reference_name}_out.pdbqt --nrun {n_poses}')  
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "vinagpu":
                    run_command(f'{vinagpu} --receptor {combine_ref_pdbqt} --ligand {reference_pdbqt} --config config.txt --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy}) 
                    
        elif mode == "flexible":
            for sf_type in sf_types:
                if sf_type == "vina":
                    run_command(f'{vina_path} --receptor {combine_ref}_rigid.pdbqt --flex {combine_ref}_flex.pdbqt --ligand {reference_pdbqt}  --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "vinardo":
                    run_command(f'{vina_path} --receptor {combine_ref}_rigid.pdbqt --flex {combine_ref}_flex.pdbqt --ligand {reference_pdbqt}  --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {reference_name}_out.pdbqt')
                    energy = vina_energy(f'{reference_name}_out.pdbqt')
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})
                elif sf_type == "ad4":                     
                    run_command(f'{agfr} -r {combine_ref_pdbqt} -l {reference_pdbqt} -o ligPocket')
                    run_command(f'{adfr} -l {reference_pdbqt} -t ligPocket.trg --jobName rigid --nbRuns 8 --maxEvals 200000 -O --seed 1')
                    energy = adfr_energy(f'{reference_name}_rigid_summary.dlg') 
                    ref_energy.append({f'{mode}_{sf_type}_Energy': energy})                 
                elif sf_type == "ad4gpu" or sf_type == "vinagpu":
                    print(f'flexible receptor is not supported in processing with GPU.')
                    continue   
    
    source_file = f"{reference_name}_out.pdbqt"
    destination_file = os.path.join(output_model_dir, f"{reference_name}_out.pdbqt")
    
    try:
        shutil.copy(source_file, destination_file)
    except FileNotFoundError:
        print(f"File {source_file} tidak ditemukan.")
    except PermissionError:
        print(f"Tidak memiliki izin untuk menyalin file.")
    except Exception as e:
        print(f"Terjadi kesalahan saat menyalin file: {e}")    

    # Save results to a DataFrame
    results = {       
        "ligand_id": reference_name,
        "smiles": smiles_reference,            
        "activity": "NaN",
        **{key: value for energy_dict in ref_energy for key, value in energy_dict.items()},        
    }

    # Membuat DataFrame untuk hasil saat ini
    df_result = pd.DataFrame([results], columns=columns)
    
    # Save the DataFrame to a CSV file
    df_result.to_csv(csv_result, mode='a', header=False, index=False)
       
    formatted_ref = f"{reference_name.upper()} {', '.join([f'{key}: {value}' for energy_dict in ref_energy for key, value in energy_dict.items()])} in kcal/mol"
            
    # Update the GUI text widget with the formatted result
    df = pd.read_csv(csv_result)
    df_as_string = df.to_string(header=True, index=False)
    
    if result_text is not None:     
        # Title
        result_text.insert(tk.END, "Docking Results:\n\n")
        result_text.insert(tk.END, formatted_ref + '\n')    
        result_text.yview(tk.END)
    
    results_ref = (ref_energy, reference_name, reference_center, combine_ref, smiles_reference)
        
    return results_ref

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

def multiple_docking(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path):
    try:
        npts = ",".join(map(str, box_size))
        combine_pdbqt = generate_receptor_pdbqt(prepare_receptor, receptor_pdb)
        combine_name = os.path.basename(combine_pdbqt).split('.')[0]
        run_command(f'{prepare_flexreceptor} -r {combine_pdbqt} -s {flexible_residues}')
       
        reference_center = results_ref[2]
        combine_ref = results_ref[3]

        for n in elements:
                n = int(n)
                if n == 1:
                    satuan = " ligands"
                elif n > 1:
                    satuan = " ligand pairs"
                    
                if arrangement_type == 'combination':
                    arrangements = itertools.combinations(smiles_list.values, n)
                elif arrangement_type == 'permutation':
                    arrangements = itertools.permutations(smiles_list.values, n)
                else:
                    raise ValueError("Invalid arrangement type. Use 'combination' or 'permutation', set in the config file.")           
                
                for arrangement in tqdm(arrangements, desc="Processing", unit=satuan):
                    try:
                        smile_list = []
                        ligand_name_list = []
                        activities_list = []
                        ligand_pdbqt_list = []
                        all_energy = []    
                        
                        for smiles in arrangement:
                            result = generate_ligand_pdbqt(prepare_ligand, smiles, reference_center)
                            smi, ligand_name, activity, others = result         

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
                        
                        elif n == 1:
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
                                        os.remove(f'{combine_name}.gpf')
                                        os.remove(f'{combine_name}.glg')
                                    elif sf_type == "ad4gpu":
                                        run_command(f'{prepare_gpf} -l {ligand_pdbqt} -r {combine_pdbqt} -p spacing={spacing} -p npts="{npts}" -i {combine_ref}.gpf -o {combine_name}.gpf')
                                        run_command(f'{ag4_path} -p {combine_name}.gpf -l {combine_name}.glg')
                                        run_command(f'{autodockgpu} --ffile {combine_name}.maps.fld --lfile {ligand_name}_out.pdbqt --nrun {n_poses}')  
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "vinagpu":
                                        run_command(f'{vinagpu} --receptor {combine_pdbqt} --ligand {ligand_pdbqt} --config config.txt --out {ligand_name}_out.pdbqt')
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        ref_energy.append({f'{mode}_{sf_type}_Energy': energy})  
                                        
                            elif mode == "flexible":
                                for sf_type in sf_types:
                                    if sf_type == "vina":
                                        run_command(f'{vina_path} --receptor {combine_name}_rigid.pdbqt --flex {combine_name}_flex.pdbqt --ligand {ligand_pdbqt}  --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "vinardo":
                                        run_command(f'{vina_path} --receptor {combine_name}_rigid.pdbqt --flex {combine_name}_flex.pdbqt --ligand {ligand_pdbqt}  --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "ad4":
                                        run_command(f'{adfr} -l {ligand_pdbqt} -t ligPocket.trg --jobName rigid --nbRuns 8 --maxEvals 200000 -O --seed 1')
                                        energy = adfr_energy(f'{ligand_name}_rigid_summary.dlg')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "ad4gpu" or sf_type == "vinagpu":
                                        print(f'flexible receptor is not supported in processing with GPU.')
                                        continue 
                                        
                    except Exception as arrangement_exception:
                        #print(f"Error in arrangement: {str(arrangement_exception)}")
                        continue  
                    #except Exception as e:
                    #    print(  f"An error occurred: {e}")
                    # Proses penyimpanan input output
                    if input_file_saved.lower() == "true":
                        if not os.path.exists(os.path.join(output_model_dir, ligand_pdb)):
                            shutil.move(ligand_pdb, output_model_dir)
                    if output_file_saved.lower() == "true":
                        shutil.copy(f'{ligand_name}_out.pdbqt', output_model_dir)              
                    
                    # Proses hapus temporary_files                    
                    ligand_temp_files = glob.glob(f"{ligand_name}*")
                    temporary_files = ligand_temp_files 
                    for file_to_remove in temporary_files:
                        os.remove(file_to_remove)
                    
 
                    # Save results to a DataFrame
                    all_results = {
                        "ligand_id": ligand_name.upper(),
                        "smiles": smi,                    
                        "activity": activities,
                        **{key: value for energy_dict in all_energy for key, value in energy_dict.items()}
                    }    
                    
                     # Membuat DataFrame untuk hasil saat ini
                    df_all_result = pd.DataFrame([all_results], columns=columns)
                
                    # Save the DataFrame to a CSV file
                    df_all_result.to_csv(csv_result, mode='a', header=False, index=False)
                                      
                    formatted_result = f"{ligand_name.upper()} {', '.join([f'{key}: {value}' for energy_dict in all_energy for key, value in energy_dict.items()])} in kcal/mol"
                   
                    if result_text is not None:  
                        result_text.insert(tk.END, formatted_result + '\n')
                        result_text.yview(tk.END)  
                    
    except Exception as e:
        print(f"An error occurred: {e}")    
                
def multiple_docking_parallel(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path):

    def process_arrangement(arrangement):

        smile_list = []
        ligand_name_list = []
        activities_list = []
        ligand_pdbqt_list = []
        all_energy = []

        for smiles in arrangement:
            result = generate_ligand_pdbqt(prepare_ligand, smiles, reference_center)
            smi, ligand_name, activity, others = result

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

        elif n == 1:
            smi = smile_list[0]
            ligand_name = ligand_name_list[0]
            activities = activities_list[0]
            ligand_pdbqt = ligand_pdbqt_list[0]
           
        for mode in listmode:
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
                                        os.remove(f'{combine_name}.gpf')
                                        os.remove(f'{combine_name}.glg')
                                    elif sf_type == "ad4gpu":
                                        run_command(f'{prepare_gpf} -l {ligand_pdbqt} -r {combine_pdbqt} -p spacing={spacing} -p npts="{npts}" -i {combine_ref}.gpf -o {combine_name}.gpf')
                                        run_command(f'{ag4_path} -p {combine_name}.gpf -l {combine_name}.glg')
                                        run_command(f'{autodockgpu} --ffile {combine_name}.maps.fld --lfile {ligand_name}_out.pdbqt --nrun {n_poses}')  
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "vinagpu":
                                        run_command(f'{vinagpu} --receptor {combine_pdbqt} --ligand {ligand_pdbqt} --config config.txt --out {ligand_name}_out.pdbqt')
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        ref_energy.append({f'{mode}_{sf_type}_Energy': energy})  
                                        
                            elif mode == "flexible":
                                for sf_type in sf_types:
                                    if sf_type == "vina":
                                        run_command(f'{vina_path} --receptor {combine_name}_rigid.pdbqt --flex {combine_name}_flex.pdbqt --ligand {ligand_pdbqt}  --config "config.txt" --scoring vina --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "vinardo":
                                        run_command(f'{vina_path} --receptor {combine_name}_rigid.pdbqt --flex {combine_name}_flex.pdbqt --ligand {ligand_pdbqt}  --config "config.txt" --scoring vinardo --exhaustiveness={exhaustiveness} --cpu {cpu} --out {ligand_name}_out.pdbqt')
                                        energy = vina_energy(f'{ligand_name}_out.pdbqt')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "ad4":
                                        run_command(f'{adfr} -l {ligand_pdbqt} -t ligPocket.trg --jobName rigid --nbRuns 8 --maxEvals 200000 -O --seed 1')
                                        energy = adfr_energy(f'{ligand_name}_rigid_summary.dlg')
                                        all_energy.append({f'{mode}_{sf_type}_Energy': energy})
                                    elif sf_type == "ad4gpu" or sf_type == "vinagpu":
                                        print(f'flexible receptor is not supported in processing with GPU.')
                                        continue 

        # Proses penyimpanan input output
        if input_file_saved.lower() == "true":
            if not os.path.exists(os.path.join(output_model_dir, ligand_pdb)):
                shutil.move(ligand_pdb, output_model_dir)        
                
        if output_file_saved.lower() == "true":
            shutil.copy(f'{ligand_name}_out.pdbqt', output_model_dir)
        
        # Proses hapus temporary_files
        ligand_temp_files = glob.glob(f"{ligand_name}*")
        temporary_files = ligand_temp_files
        for file_to_remove in temporary_files:
            os.remove(file_to_remove)

        # Save results to a DataFrame
        all_results = {
                        "ligand_id": ligand_name.upper(),
                        "smiles": smi,                    
                        "activity": activities,
                        **{key: value for energy_dict in all_energy for key, value in energy_dict.items()}
        }
       
        df_all_results = pd.DataFrame([all_results], columns=columns)
        print(df_all_results)
        df_all_results.to_csv(csv_result, mode='a', header=False, index=False)

        formatted_result = f"{ligand_name.upper()} {', '.join([f'{key}: {value}' for energy_dict in all_energy for key, value in energy_dict.items()])} in kcal/mol"
       
        if result_text is not None:
            result_text.insert(tk.END, formatted_result + '\n')
            result_text.yview(tk.END)

    npts = ",".join(map(str, box_size))
    combine_pdbqt = generate_receptor_pdbqt(prepare_receptor, receptor_pdb)
    combine_name = os.path.basename(combine_pdbqt).split('.')[0]
    run_command(f'{prepare_flexreceptor} -r {combine_pdbqt} -s {flexible_residues}')

    reference_center = results_ref[2]
    combine_ref = results_ref[3]
    
    for n in elements:
        n = int(n)
        if arrangement_type == 'combination':
            arrangements = itertools.combinations(smiles_list.values, n)
        elif arrangement_type == 'permutation':
             arrangements = itertools.permutations(smiles_list.values, n)
        else:
            raise ValueError("Invalid arrangement type. Use 'combination' or 'permutation', set in the config file.")
    
        total_cpus = os.cpu_count() - 2
        max_workers = total_cpus // (cpu + 1)
        
        with ThreadPoolExecutor(max_workers=3) as executor:           
            for arrangement in arrangements:
                executor.submit(process_arrangement, arrangement)

def docking(result_text, sf_types, listmode, distance, arrangement_type, elements,
         box_size, spacing, n_poses, exhaustiveness, cpu, parallel_simulation, input_file_saved,
         output_file_saved, vina_path, ad4_path, ag4_path, autodockgpu, vinagpu,
         job_directory, max_workers, agfr, adfr, prepare_ligand,
         prepare_receptor, prepare_gpf, prepare_flexreceptor, current_directory):
    
    box_size = [int(value) for value in box_size.split(',')]
    os.chdir(current_directory)
    print_dev(developer_note, developer_contact, citation_list)
    
    model_dirs = [d for d in os.listdir(current_directory) if os.path.isdir(os.path.join(current_directory, d)) and d.startswith('model')]
    ligand_dir = os.path.join(current_directory, 'ligand_input')
    output_dir = os.path.join(current_directory, 'output')    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)    
    
    for model_dir in model_dirs:
        model_path = os.path.join(current_directory, model_dir)

        os.chdir(model_path)       
        
        receptor_pdb = next((f for f in os.listdir('.') if f.startswith("rec") and f.endswith(".pdb")), None)
        reference_pdb = next((f for f in os.listdir('.') if f.startswith("lig") and f.endswith(".pdb")), None)

        if receptor_pdb and reference_pdb and os.path.exists(receptor_pdb) and os.path.exists(reference_pdb):
            delete_files_except_pdb('.') 
            
            # Processing reference ligand
            print(f"Processing {model_dir.upper()} - Reference ligand")
            print(f"  Receptor: {receptor_pdb}")
            print(f"  Reference Ligand: {reference_pdb}")
            
            create_vina_config(reference_pdb, box_size)

            output_model_dir = os.path.join(output_dir, model_dir)
            if not os.path.exists(output_model_dir):
                os.makedirs(output_model_dir)
                
            # Get flexible residue            
            flexible_residues = get_residues_within_distance(receptor_pdb, reference_pdb, distance) 
            flexible_residues = '_'.join(sorted(flexible_residues))
            
            # Membuat DataFrame kosong dengan nama kolom yang diperoleh dari smiles_list
            columns = ["ligand_id","smiles","activity"] + [f'{mode}_{sf_type}_Energy' for mode in listmode for sf_type in sf_types]
            df_empty = pd.DataFrame(columns=columns)
            csv_result = os.path.join(output_model_dir, f"results_{'_'.join(sf_types)}_{'_'.join(listmode)}.csv")

            df_empty.to_csv(csv_result, mode='w', header=True, index=False)
                        

            shutil.copy(reference_pdb, output_model_dir)
            results_ref = docking_reference(output_model_dir, result_text, agfr, adfr, prepare_gpf, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, csv_result, receptor_pdb, reference_pdb, flexible_residues, vina_path, ad4_path, ag4_path)
            # Processing ligand test files
            print(f"Processing {model_dir.upper()} - Test ligands")                        
            result_list = []  
        
            for n in elements:
                n = int(n)
                if n > 1:                    
                    for f in os.listdir(ligand_dir):
                    
                        if f.endswith('.uri'): 
                            ligand_file = processing_uri(f)
                            if ligand_file.endswith(".smi"):
                                smiles_list = process_smi(lig_file) 
                                result_list.append(smiles_list) 
                                
                            elif ligand_file.endswith(".sdf"):
                                smiles_list = process_sdf(ligand_file)
                                result_list.append(smiles_list) 

                        else:
                            smiles_list = processing_ligand(os.path.join(ligand_dir, f))
                            result_list.append(smiles_list)                            
                        
                        result_list = pd.concat(result_list, ignore_index=True) 
                        
                        if parallel_simulation.lower() == 'true':
                            multiple_docking_parallel(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, result_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)   
                        else:
                            multiple_docking(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, result_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)

                elif n == 1:                
                     for f in os.listdir(ligand_dir):                    
                        if f.endswith('.uri'):
                            with open(os.path.join(ligand_dir, f), 'r') as file:
                                ligand_links = [line.strip() for line in file.readlines() if 'http' in line and not line.strip().startswith('#')]
                                
                            for ligand_link in ligand_links:
                                ligand_file_base = basename(ligand_link)
                                download_file_with_retry(ligand_link, ligand_file_base) 
                                
                                if ligand_file_base.endswith('.gz'):
                                    try:
                                        ligand_file = extract_gz(ligand_file_base)
                                        os.remove(ligand_file_base)
                                    except Exception as e:
                                        continue                                
                                        
                                else:
                                    ligand_file = ligand_file_base
                                    
                                if ligand_file.endswith(".smi"):
                                            smiles_list = process_smi(ligand_file) 
                                            if parallel_simulation.lower() == 'true':
                                                multiple_docking_parallel(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)   
                                            else:
                                                multiple_docking(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)
                                                        
                                elif ligand_file.endswith(".sdf"):
                                            smiles_list = process_sdf(ligand_file)
                                            if parallel_simulation.lower() == 'true':
                                                multiple_docking_parallel(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)   
                                            else:
                                                multiple_docking(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)
                                    
                        else:
                            smiles_list = processing_ligand(os.path.join(ligand_dir, f))
                            if parallel_simulation.lower() == 'true':
                                multiple_docking_parallel(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)   
                            else:
                                multiple_docking(result_text, agfr, adfr, prepare_gpf, input_file_saved, output_file_saved, arrangement_type, elements, spacing, n_poses, exhaustiveness, cpu, prepare_flexreceptor, prepare_receptor, prepare_ligand, box_size, listmode, sf_types, columns, receptor_pdb, smiles_list, flexible_residues, results_ref, output_model_dir, csv_result, vina_path, ad4_path, ag4_path)
                                
                print(f"Success: {model_dir.upper()}")
                os.chdir(current_directory)

        else:
            print(f"Skipping {model_dir.upper()} - receptor_pdb or reference_pdb or both not found")
            os.chdir(current_directory)

    # Print developer's note, contact, and citation list
    print_dev(developer_note, developer_contact, citation_list)

