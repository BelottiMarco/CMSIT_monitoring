import argparse
import csv
import re
import time
import datetime
import os
from pathlib import Path
import pandas as pd

# for mimic the terminal
import shlex
import subprocess
import signal
import sys

from config_loader import load_config, cfg
from csv_schema import FIELDNAMES
from csv_schema import CROC_REGISTERS
from csv_schema import LPGBT_ADC_REGISTERS, LPGBT_VOLTAGE_REGISTERS, PROBLEMATIC_LPGBT_ADC_REGISTER

# --- DEFAULT CONFIGURATIONS ---
PH2_ACF_DIRECTORY = "/home/bootcamp/marco/Ph2_ACF_1/Ph2_ACF/"
CSV_DIRECTORY  = "./MonitoringCSV"

ANSI_ESCAPE_REGEX = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

def adc_raw_to_voltage(calibration_df, lpgbt_efuse, adc_ch, raw_adc, gain=None, tj_user=None):
    chip = calibration_df[calibration_df['CHIPID'] == lpgbt_efuse].iloc[0]
    
    slope = chip[f'ADC_X{gain}_SLOPE']
    offset = chip[f'ADC_X{gain}_OFFSET']
    
    if tj_user is not None:
        slope += tj_user * chip[f'ADC_X{gain}_SLOPE_TEMP']
        offset += tj_user * chip[f'ADC_X{gain}_OFFSET_TEMP']
        
    v_adc = (float(raw_adc) * slope) + offset
    
    if adc_ch == 2:
        return v_adc * 2.0
    elif adc_ch == 3:
        return v_adc * 4.0
    elif adc_ch == 4:
        return v_adc * 16.0
    elif 9 <= adc_ch <= 13:
        vddmon_slope = chip['VDDMON_SLOPE']
        if tj_user is not None:
            vddmon_slope += tj_user * chip['VDDMON_SLOPE_TEMP']
        return v_adc * vddmon_slope
    else:
        return v_adc

def load_lpgbt_calibration(Ph2_ACF_dir):
    """Upload LpGBT calibration table only once in the memory."""
    lpgbt_calib_path = os.path.join(Ph2_ACF_dir, 'settings/lpGBTFiles/lpgbt_calibration.csv')
    
    if not os.path.exists(lpgbt_calib_path):
        print(f"WARNING: LpGBT calibration file not found at {lpgbt_calib_path}.\n")
        return None
    try:
        # Ignor comments '#' and consider complex separators
        df = pd.read_csv(lpgbt_calib_path, comment='#', sep=r'[\s,;\t]+', engine='python')
        print(f"-> LpGBT calibration file loaded successfully from {lpgbt_calib_path}.")
        return df
    except Exception as e:
        print(f"WARNING: Error reading LpGBT calibration file: {e}.\n")
        return None

def convert_lpgbt_adc(calib_df, lpgbt_efuse, register, raw_adc, raw_units, gain=None, tj_user=None):
    if calib_df is None or calib_df.empty:
        return raw_adc, raw_units

    if lpgbt_efuse not in calib_df['CHIPID'].values:
        return raw_adc, raw_units

    # search LPGBT_ADC channel in the problematic ADC registers
    match = re.match(r'^LPGBT_ADC(\d+)$', register)
    if match and lpgbt_efuse in PROBLEMATIC_LPGBT_ADC_REGISTER:
        adc_ch = int(match.group(1))
        if adc_ch in PROBLEMATIC_LPGBT_ADC_REGISTER[lpgbt_efuse]:
            val_converted = adc_raw_to_voltage(calib_df, lpgbt_efuse, adc_ch, raw_adc, gain=gain, tj_user=tj_user)
            return str(val_converted), 'V'

    return raw_adc, raw_units

def croc_register_conversion(reg, val):
    if   reg == 'VINA':        return str(float(val)*4)
    elif reg == 'VDDA':        return str(float(val)*2) 
    elif reg == 'VIND':        return str(float(val)*4)
    elif reg == 'VDDD':        return str(float(val)*2)
    elif reg == 'ANA_IN_CURR': return str(float(val)*21000)
    elif reg == 'DIG_IN_CURR': return str(float(val)*21000)
    else:                      return val

def clean_line(line):
    """Remove ANSI color escape sequences from a log line."""
    return ANSI_ESCAPE_REGEX.sub("", line)

def CROC_telemery(line, results):
    # Iterative search for CROC register
    for reg in CROC_REGISTERS:
        match = re.search(rf"{reg}\s*:\s*([-\d\.]+)(?:\s*\+/-\s*([-\d\.]+))?(?:\s*([a-zA-Z]+))?", line, re.IGNORECASE)
        if match:
            reg_name = reg.upper().replace(" ", "_")
            results.append({
                "REGISTER": reg_name,
                "VALUE": croc_register_conversion(reg_name, match.group(1)),
                "ERROR": croc_register_conversion(reg_name, match.group(2)) if match.group(2) else "",
                "UNIT": match.group(3) if match.group(3) else ""
            })
            
    return results

def LpGBT_telemery(line, results):
     # Iterative search for LpGBT ADC (also PROBLEMATIC) register
    for reg in LPGBT_ADC_REGISTERS:
        match_1 = re.search(rf"LpGBT register {reg} has no calibration file. Raw value is \s*([-\d\.]+)",             line, re.IGNORECASE)
        match_2 = re.search(rf"LpGBT temperature measurement from register {reg} is\s*([-\d\.]+)(?:\s*([a-zA-Z]+))?", line, re.IGNORECASE)
        if match_1:
            results.append({"REGISTER": "LPGBT_"+reg, "VALUE": match_1.group(1), "ERROR": "", "UNIT": ""})
        if match_2:
            results.append({"REGISTER": "LPGBT_"+reg, "VALUE": match_2.group(1), "ERROR": "", "UNIT": match_2.group(2) if match_2.group(2) else ""})
    
    # Search for LpGBT generic temperature
    temp_match = re.search(r"LpGBT temperature measurement\s*([-\d\.]+)(?:\s*([a-zA-Z]+))?", line, re.IGNORECASE)
    if temp_match:
        results.append({"REGISTER": "LPGBT_"+"TEMPERATURE", "VALUE": temp_match.group(1), "ERROR": "", "UNIT": temp_match.group(2) if temp_match.group(2) else ""})
    
    # Iterative search for LpGBT Voltage register
    for reg in LPGBT_VOLTAGE_REGISTERS:
        match = re.search(rf"LpGBT voltage measurement from power supply {reg} is\s*([-\d\.]+)(?:\s*([a-zA-Z]+))?", line, re.IGNORECASE)
        if match:
            results.append({"REGISTER": "LPGBT_"+reg, "VALUE": match.group(1), "ERROR": "", "UNIT": match.group(2) if match.group(2) else ""})

    # Search for LpGBT PUSMStatus
    pusm_match = re.search(r"PUSMStatus\s*=\s*\d+\s*\((0x[0-9a-fA-F]+)\)", line, re.IGNORECASE)
    if pusm_match:
        results.append({"REGISTER": "LPGBT_"+"PUSMStatus", "VALUE": pusm_match.group(1), "ERROR": "", "UNIT": ""})

    return results

def parse_telemetry(line, hierarchy_length):
    results = []
    if hierarchy_length == 4:
        results = CROC_telemery(line, results)
    else:
        results = LpGBT_telemery(line, results)

    return results

def heartbeat_print(message, heartbeat_seconds, last_heartbeat_time):
    if heartbeat_seconds and (time.time() - last_heartbeat_time) >= heartbeat_seconds:
        print_status(message)
        return time.time()
    return last_heartbeat_time

def link_type_extractor(line):
    match = re.search(r"Link\s+type\s*[:\s]+\s*(\w+)", line, re.IGNORECASE)
    if match: return match.group(1).strip()
    else:     return None

def lpgbt_efuse_extractor(line):
    match = re.search(r"FuseID from LpGBT OpticalGroup ID (\d+)(?:\s+on Board ID (\d+))?:\s*(0x[0-9a-fA-F]+)", line, re.IGNORECASE)
    if match: return match
    else:     return None

def croc_efuse_extractor(line):
    match = re.search(r"e-?fuse\s*code\s*[:\s]+\s*([0-9a-fA-FxX]+)", line, re.IGNORECASE)
    if match: return match
    else:     return None

def clean_efuse(efuse_str):
    """Remove prefix '0x' o '0X' from eFuse."""
    if not efuse_str:
        return ""
    return re.sub(r"^0[xX]", "", efuse_str.strip())
    
def print_status(message):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"   at [{now_str}] -> {message}", flush=True)

def open_full_csv_file(full_csv_path, fieldnames):
    os.makedirs(os.path.dirname(full_csv_path), exist_ok=True)
    full_csv_file = open(full_csv_path, mode="a", newline="")
    writer = csv.DictWriter(full_csv_file, fieldnames=fieldnames)
    
    # write header only if the file is completly empty
    if os.path.getsize(full_csv_path) == 0:
        writer.writeheader()
        full_csv_file.flush()
    return full_csv_file, writer

def process_live_log(Ph2_ACF_dir, log_path, full_csv_path, date=None, use_log_time=False, timeout=300, heartbeat_seconds=120, lpgbt_calib_config={'gain': 2, 'tj_user': 20}):
    print(f"\n-> Start real time monitoring of: {log_path}")
    print(f"-> Output FULL CSV (Append Mode): {full_csv_path}")
    print(f"-> Press Ctrl+C for interrupting the reading in every moment.")
    
    # State variables for extraction at single look
    link_type = None
    is_optical = False
    efuse_map = {}
    temp_chip_efuses = {}
    current_wb_chip = None
    active_hierarchy = None
    all_croc_chips = set()
    
    # Variables for duplicates in the same second
    seen_this_second = set()
    last_seen_time = ""
    
    # Open LpGBT calibration file
    calib_df = load_lpgbt_calibration(Ph2_ACF_dir)
        
    # Open single permanent CSV writer
    full_csv_file, writer = open_full_csv_file(full_csv_path, FIELDNAMES)
    
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as log_file:
            # timer for inactivity
            last_activity_time = time.time()
            # timer for the periodic heartbeat print
            last_heartbeat_time = time.time()

            while True:
                # Print a status line every heartbeat_seconds, regardless of log activity
                last_heartbeat_time = heartbeat_print(f"still monitoring '{Path(log_path).name}' (last log line: {int(time.time() - last_activity_time)}s ago)", heartbeat_seconds, last_heartbeat_time)

                # read line from log file
                line = log_file.readline()
                # Dischard useless lines
                if "|D|" in line: 
                    continue
                
                # Condition to stop the execution (timeout)
                if not line:
                    # If line added at the end of the file check how many time has passed from last line
                    if time.time() - last_activity_time >= timeout:
                        print_status(f"No new lines in the log file for {timeout} seconds. Execution ended for timeout.")
                        break
                    # wait 0.1s and try again
                    time.sleep(0.1)
                    continue
                # reset time
                last_activity_time = time.time()

                # Condition to stop the execution (end of .log file)
                if "@@@ End of CMSIT miniDAQ @@@" in line:
                    print_status("Reached '@@@ End of CMSIT miniDAQ @@@'. Finish execution!")
                    break
                
                
                # Real time vs log line time (for old data)
                if use_log_time and date is not None:
                    current_date = date
                    time_match = re.search(r"\|(\d{2}:\d{2}:\d{2})\|", line)
                    current_time = time_match.group(1) if time_match else ""
                else:
                    now = datetime.datetime.now()
                    current_date = now.strftime("%Y-%m-%d")
                    current_time = now.strftime("%H:%M:%S.%f")
                    
                # If becomes another second, clean the duplicates memory
                if current_time and current_time != last_seen_time:
                    seen_this_second.clear()
                    last_seen_time = current_time


                # Clean the line from ANSI escape sequences (color codes)
                cleaned_line = clean_line(line)

                # 1. LINK TYPE EXTRACTOR
                if not link_type:
                    link_type = link_type_extractor(cleaned_line)
                    if link_type:
                        is_optical = "optical" in link_type.lower()
                        print_status(f"Detected Link Type: '{link_type}'")

                # 2. LpGBT EFUSE EXTRACTOR
                if is_optical:
                    lpgbt_match = lpgbt_efuse_extractor(cleaned_line)
                    if lpgbt_match:
                        og, b = int(lpgbt_match.group(1)), int(lpgbt_match.group(2)) if lpgbt_match.group(2) else 0
                        efuse_val = clean_efuse(lpgbt_match.group(3))
                        efuse_map[(b, og)] = efuse_val
                        print_status(f"Detected LpGBT eFuse: '{lpgbt_match.group(3)}'")

                # 3. CROC EFUSE EXTRACTOR
                wb_match = re.search(r"Wire bonded chip ID\s*=\s*(\d+)", cleaned_line, re.IGNORECASE)
                if wb_match:
                    current_wb_chip = int(wb_match.group(1))
                    
                efuse_code_match = croc_efuse_extractor(cleaned_line)
                if efuse_code_match and current_wb_chip is not None:
                    efuse_val = clean_efuse(efuse_code_match.group(1))
                    temp_chip_efuses[current_wb_chip] = efuse_val
                    current_wb_chip = None
                    print_status(f"Detected CROC  eFuse: '{efuse_code_match.group(1)}'")

                # 4. UPDATING CONTEXT AND EFUSE MAP
                croc_ctx = re.search(r"(?:Reading\s+)?monitor(?:ed|ing)?\s+data\s+for\s*\[\s*board/opticalGroup/hybrid/chip\s*=\s*(\d+)/(\d+)/(\d+)/(\d+)\s*\]", cleaned_line, re.IGNORECASE)
                if croc_ctx:
                    b, og, h, c = int(croc_ctx.group(1)), int(croc_ctx.group(2)), int(croc_ctx.group(3)), int(croc_ctx.group(4))
                    active_hierarchy = (b, og, h, c)
                    all_croc_chips.add(active_hierarchy)
                    
                    # Assign e-fuse remaining from temporary memory
                    if c in temp_chip_efuses:
                        efuse_map[active_hierarchy] = temp_chip_efuses[c]
                        del temp_chip_efuses[c]
                    continue 

                lpgbt_ctx = re.search(r"Reading monitored data for \[\s*board/opticalGroup\s*=\s*(\d+)/(\d+)\s*\]", cleaned_line, re.IGNORECASE)
                if lpgbt_ctx:
                    active_hierarchy = (int(lpgbt_ctx.group(1)), int(lpgbt_ctx.group(2)))
                    continue

                # 5. EXTRACTING TELEMERY AND LIVE WRITING
                if active_hierarchy:
                    parsed_records = parse_telemetry(cleaned_line, len(active_hierarchy))
                    
                    rows_to_write = []
                    for record in parsed_records:
                        b, og = active_hierarchy[0], active_hierarchy[1]
                        lpgbt_efuse = efuse_map.get((b, og), "")

                        # Opt.A: if is a CROC (4 levels hierarchy)
                        if len(active_hierarchy) == 4:
                            h, c = active_hierarchy[2], active_hierarchy[3]
                            row = {field: "" for field in FIELDNAMES}
                            row["BOARD"], row["OPTICAL_GROUP"] = b, og
                            row["LpGBT_EFUSE"] = lpgbt_efuse
                            row["HYBRID_ID"], row["CHIP"] = h, c
                            row["CHIP_EFUSE"] = efuse_map.get((b, og, h, c), "")
                            row["REGISTER"], row["VALUE"] = record["REGISTER"], record["VALUE"]
                            row["ERROR"], row["UNIT"] = record["ERROR"], record["UNIT"]
                            row["TIME"], row["DATE"] = current_time, current_date
                            row["PORTCARD_ID"], row["MODULE_ID"] = "56044-PAC046", "F1p-2030"
                            rows_to_write.append(row)

                        # Opt.B: if is a LpGBT (2 levels hierarchy)
                        else:
                            # Find all chips associated to this (BOARD, OPTICAL_GROUP) from the eFuse_map
                            matching_chips = sorted([chip for chip in all_croc_chips if chip[0] == b and chip[1] == og])

                            if matching_chips:
                                # Duplicate the LpGBT line for each associated chip
                                for _, _, h, c in matching_chips:
                                    row = {field: "" for field in FIELDNAMES}
                                    row["BOARD"], row["OPTICAL_GROUP"] = b, og
                                    row["LpGBT_EFUSE"] = lpgbt_efuse
                                    row["HYBRID_ID"], row["CHIP"] = h, c
                                    row["CHIP_EFUSE"] = efuse_map.get((b, og, h, c), "")
                                    row["REGISTER"] = record["REGISTER"]
                                    row["VALUE"], row['UNIT'] = convert_lpgbt_adc(calib_df, lpgbt_efuse, record["REGISTER"], record["VALUE"], record["UNIT"], gain=lpgbt_calib_config['gain'], tj_user=lpgbt_calib_config['tj_user'])
                                    row["ERROR"] = record["ERROR"]
                                    row["TIME"], row["DATE"] = current_time, current_date
                                    row["PORTCARD_ID"], row["MODULE_ID"] = "56044-PAC046", "F1p-2030"
                                    rows_to_write.append(row)
                            else:
                                # Fallback if not known chips for this OG
                                row = {field: "" for field in FIELDNAMES}
                                row["BOARD"], row["OPTICAL_GROUP"] = b, og
                                row["LpGBT_EFUSE"] = lpgbt_efuse
                                row["REGISTER"] = record["REGISTER"]
                                row["VALUE"], row['UNIT'] = convert_lpgbt_adc(calib_df, lpgbt_efuse, record["REGISTER"], record["VALUE"], record["UNIT"], gain=lpgbt_calib_config['gain'], tj_user=lpgbt_calib_config['tj_user'])
                                row["ERROR"] = record["ERROR"]
                                row["TIME"], row["DATE"] = current_time, current_date
                                row["PORTCARD_ID"], row["MODULE_ID"] = "56044-PAC046", "F1p-2030"
                                rows_to_write.append(row)

                    # Duplicates filter in real time and writing immediatly on disk
                    if rows_to_write:
                        unique_rows = []
                        for row in rows_to_write:
                            row_tuple = tuple(row.items())
                            if row_tuple not in seen_this_second:
                                seen_this_second.add(row_tuple)
                                unique_rows.append(row)
                        
                        if unique_rows:
                            writer.writerows(unique_rows)
                            full_csv_file.flush()

    except KeyboardInterrupt:
        print("\n\n-> Execution manually interrupted (Ctrl+C). CSV file was closed and saved correctly.\n")
    finally:
        full_csv_file.close()

def wait_for_log_file(logs_dir, run_number, timeout_for_log=60):
    """
        Wait up to 'timeout_for_log' seconds for the log file to be generated.
        Find log file corresponding to 6-digit run number in specified directory.
    """
    print(f"-> In waiting for log file for run {run_number} in directory '{logs_dir}'...\n")
    directory = Path(logs_dir)
    if not directory.exists():
        print(f"ERROR: directory '{logs_dir}' does not exist.\n")
        return None

    start_time = time.time()
    pattern = f"CMSITminiDAQ{run_number:06d}_*.log"
    
    while True:
        matching_files = list(directory.glob(pattern))
        if matching_files:
            return matching_files[0]
        
        if time.time() - start_time > timeout_for_log:
            print(f"ERROR: No file found after waiting {timeout_for_log} seconds.\n")
            return None
        time.sleep(1)

def read_last_run_number(ph2_acf_test_dir):
    """Read the run number from RunNumber.txt as an int"""
    with open(ph2_acf_test_dir + '/RunNumber.txt', 'r', encoding='utf-8') as f:
        last_run_number = int(float(f.read().strip()))
        print(f"-> Auto-detected last run number from RunNumber.txt: {last_run_number}")

    return last_run_number

def write_run_number(ph2_acf_test_dir, new_run_number):
    """Overwrite RunNumber.txt with a new run number"""
    with open(ph2_acf_test_dir + '/RunNumber.txt', 'w', encoding='utf-8') as f:
        f.write(f"{new_run_number:06d}")

if __name__ == "__main__":
    config, config_path = load_config()

    parser = argparse.ArgumentParser(description="Parse Ph2_ACF log files into a continuous Full CSV.")
    parser.add_argument("--config", type=str, default=config_path,
                        help="Path to the shared monitoring.ini (default: ./monitoring.ini or $MONITORING_CONFIG)")

    # for Ph2_ACF configuration
    parser.add_argument("--calibration",        type=str,
                        default=cfg(config, "log_to_csv", "calibration", fallback="physics"),
                        help="Choose the calibration to run in Ph2_ACF (default: physics)")
    parser.add_argument("--xml_file",           type=str,
                        default=cfg(config, "log_to_csv", "calibration", fallback="CMSIT_gtx0.xml"),
                        help="Choose the configuration file for running Ph2_ACF (default: CMSIT_gtx0.xml)")
    parser.add_argument("--Ph2_ACF_dir",        type=str,
                        default=cfg(config, "paths", "ph2_acf_dir", fallback=PH2_ACF_DIRECTORY),
                        help="Path to the Ph2_ACF directory (containing settings/ and test/)")
    # for csv_output file
    parser.add_argument("--output_csv_dir",     type=str,
                        default=cfg(config, "paths", "output_csv_dir", fallback=CSV_DIRECTORY),
                        help="Path to output CSV directory (default: ./MonitoringCSV)")
    parser.add_argument("--output_csv_name",    type=str,
                        default=cfg(config, "log_to_csv", "output_csv_name", fallback="monitoring_FULL.csv"),
                        help="Name of the output CSV file. -1 for using the run number name (default: monitoring_FULL.csv)")
    # for old runs
    parser.add_argument("--log_time",           action=argparse.BooleanOptionalAction,
                        default=cfg(config, "log_to_csv", "log_time", fallback=False, kind=bool),
                        help="Use old-time date/time of the log lines.")
    parser.add_argument("--run",                type=int, default=-1, help="Run number. If omitted (or -1), auto-detected from RunNumber.txt in --Ph2_ACF_dir.")
    parser.add_argument("--date",               type=str, default=datetime.datetime.now().strftime("%Y-%m-%d"), help="Date of the log (to be added in the CSV)")  
    # for debugging and execution control
    parser.add_argument("--terminal_Ph2_ACF",   action=argparse.BooleanOptionalAction,
                        default=cfg(config, "log_to_csv", "terminal_ph2_acf", fallback=False, kind=bool),
                        help="Save what printed out at terminal by Ph2_ACF in a .log file (for debugging)")
    parser.add_argument("--timeout",            type=int,
                        default=cfg(config, "log_to_csv", "timeout", fallback=300, kind=int),
                        help="Inactivity timeout in seconds, from log file lines (default: 300, set 0 to disable)")
    parser.add_argument("--heartbeat_seconds",  type=int,
                        default=cfg(config, "log_to_csv", "heartbeat_seconds", fallback=120, kind=int),
                        help="Print a status line every N seconds even when nothing changes (default: 120, set 0 to disable)")
    parser.add_argument("--no_launch",          action=argparse.BooleanOptionalAction,
                        default=cfg(config, "log_to_csv", "no_launch", fallback=False, kind=bool),
                        help="Run parsing only, without launching Ph2_ACF")
    
    args = parser.parse_args()
    
    print("\n" + "="*40 + " START LOG --> CSV " + "="*41 + "\n")
    
    # Directory of execution
    Ph2_ACF_test_dir = args.Ph2_ACF_dir + 'test/'
    xml_path = Ph2_ACF_test_dir + args.xml_file

    # Call Ph2_ACF from terminal
    command = (
        f"cd {args.Ph2_ACF_dir} && "
        "source setup.sh && "
        f"cd {Ph2_ACF_test_dir} && "
        f"exec CMSITminiDAQ -f {xml_path} -c {args.calibration}"
    )
    
    
    if args.terminal_Ph2_ACF:
        current_working_dir = os.getcwd()
        terminal_output_path = os.path.join(current_working_dir, "terminal_CMSITminiDAQ.log")
        command = command + f" > {terminal_output_path} 2>&1"
    else:
        command = command + " > /dev/null 2>&1"
    
    process = None
    
    try:
        if not args.no_launch:
            # Launch CMSITminiDAQ in background
            process = subprocess.Popen(
                command,
                shell=True,
                executable="/bin/bash",
                preexec_fn=os.setsid,
                # stdout=subprocess.DEVNULL,
                # stderr=subprocess.DEVNULL
            )
            print(f"=== CMSITminiDAQ ({args.calibration}) is running in background. ===" + 
                "\n=== -> Press Ctrl+C to stop the monitoring           ===\n")
        else:
            process = None
            print("=== Execution in only parsing mode (--no_launch) ===")
        
        # Baseline snapshot of RunNumber.txt taken BEFORE monitoring starts. This is
        # read regardless of whether --run was passed explicitly, because it's
        # needed later purely to detect whether the FILE ITSELF changed while we
        # were monitoring (e.g. Ph2_ACF advancing it for the next run on its own).
        try:
            initial_file_run_number = read_last_run_number(Ph2_ACF_test_dir)
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"WARNING: could not read RunNumber.txt at start ({e}). "
                f"The end-of-run auto-increment check will be skipped.\n")
            initial_file_run_number = None

        if args.run != -1:
            run_number = args.run
        elif initial_file_run_number is not None:
            run_number = initial_file_run_number
        else:
            print("ERROR: --run not specified and RunNumber.txt is unavailable; cannot determine run number.\n")
            run_number = None

        # Wait for CMSITminiDAQ to actually create the .log file before starting reading it in real time
        log_dir = Ph2_ACF_test_dir + "logs/"
        log_file = wait_for_log_file(log_dir, run_number) if run_number is not None else None
        
        if log_file:
            output_csv_dir = args.output_csv_dir if args.output_csv_dir else CSV_DIRECTORY
            Path(output_csv_dir).parent.mkdir(parents=True, exist_ok=True)
            if args.output_csv_name == "-1":
                full_csv_path = f"{output_csv_dir}/run_{run_number:06d}_monitoring_FULL.csv"
            else:
                full_csv_path = f"{output_csv_dir}/{args.output_csv_name}"

            process_live_log(
                args.Ph2_ACF_dir, log_file, full_csv_path, args.date,
                use_log_time=args.log_time,
                timeout=args.timeout,
                heartbeat_seconds=args.heartbeat_seconds,
            )

            # Monitoring is finished (for Ctrl+C, Timeout or Run Finish) --> kill CMSITminiDAQ
            if process and process.poll() is None:
                print(f"\nMonitoring finished. Terminating CMSITminiDAQ ({args.calibration}) process group...\n")
                try:
                    # Usiamo SIGINT (pressione di Ctrl+C virtuale) o SIGTERM per una chiusura pulita
                    os.killpg(os.getpgid(process.pid), signal.SIGINT)
                    process.wait(timeout=5)  # Aspetta fino a 5 secondi che si chiuda pulitamente
                except subprocess.TimeoutExpired:
                    # Se non si chiude con le buone, lo forziamo con SIGKILL
                    print("Process did not stop smoothly, forcing kill...\n")
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception as e:
                    print(f"Could not terminate process group: {e}\n")

            # Check if RunNumber.txt changed on not (eventually advance it)
            if initial_file_run_number is not None:
                try:
                    final_file_run_number = read_last_run_number(Ph2_ACF_test_dir)
                except (FileNotFoundError, ValueError, OSError) as e:
                    print(f"WARNING: could not re-read RunNumber.txt after monitoring finished ({e}). "
                        f"Leaving it as-is.\n")
                    final_file_run_number = None

                if final_file_run_number == initial_file_run_number:
                    next_run_number = initial_file_run_number + 1
                    write_run_number(Ph2_ACF_test_dir, next_run_number)
                    print(f"-> RunNumber.txt unchanged after run {run_number} finished "
                        f"(still {initial_file_run_number}); advanced it to {next_run_number}.\n")
                elif final_file_run_number is not None:
                    print(f"-> RunNumber.txt already advanced to {final_file_run_number} "
                        f"while monitoring; leaving it unchanged.\n")

    except KeyboardInterrupt:
        print(f"\n==> [Ctrl+C detected early] Terminating CMSITminiDAQ ({args.calibration})... ===\n")
        if process and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                print("Process group terminated successfully.\n")
            except Exception as e:
                print(f"Could not terminate process group: {e}\n")
    
    # Security Check (other unexpected interrumptions)
    if process and process.poll() is None:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except:
            pass
    
    print("="*100 + "\n")