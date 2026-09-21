import os
import time
import datetime
import csv
import subprocess
import re

# --- CONFIGURATIONS ---
XML_FILE = "CMSIT_gtx0.xml"
TIMES_CSV = "/home/bootcamp/marco/code/MonitoringCSV/vtrx_calib_times.csv"
PH2_ACF_DIR = "/home/bootcamp/marco/Ph2_ACF_1/Ph2_ACF/"
TEST_DIR = os.path.join(PH2_ACF_DIR, "test/")

# Define the combinations to iterate over here. 
# Format: (VTRxBiasStart, VTRxBiasStop, VTRxModulationStart, VTRxModulationStop)
CALIBRATION_PARAMS = [
    (40, 41, 24, 25),
    (48, 49, 24, 25),
    (55, 56, 24, 25),
    (40, 41, 32, 33),
    (48, 49, 32, 33),
    (55, 56, 32, 33),
    (40, 41, 39, 40),
    (48, 49, 39, 40),
    (55, 56, 39, 40),
    # Add all the required combinations
]

def update_xml_settings(bias_start, bias_stop, mod_start, mod_stop):
    """Overwrites target parameters in CMSIT_gtx0.xml using regex to preserve formatting"""
    with open(os.path.join(TEST_DIR, XML_FILE), 'r') as f:
        content = f.read()
    
    content = re.sub(r'(<Setting name="VTRxBiasStart">\s*)\d+(\s*</Setting>)', rf'\g<1>{bias_start}\g<2>', content)
    content = re.sub(r'(<Setting name="VTRxBiasStop">\s*)\d+(\s*</Setting>)', rf'\g<1>{bias_stop}\g<2>', content)
    content = re.sub(r'(<Setting name="VTRxModulationStart">\s*)\d+(\s*</Setting>)', rf'\g<1>{mod_start}\g<2>', content)
    content = re.sub(r'(<Setting name="VTRxModulationStop">\s*)\d+(\s*</Setting>)', rf'\g<1>{mod_stop}\g<2>', content)
    
    with open(os.path.join(TEST_DIR, XML_FILE), 'w') as f:
        f.write(content)

def get_current_run_number():
    """Reads the run number that will be assigned to the next calibration"""
    try:
        with open(os.path.join(TEST_DIR, 'RunNumber.txt'), 'r') as f:
            return int(f.read().strip())
    except Exception:
        return -1

if __name__ == "__main__":
    # Initialize the timing CSV file
    with open(TIMES_CSV, 'w', newline='') as f:
        writer = csv.writer(f)
        # Extended header to track the parameters used in the run
        writer.writerow(["RUN_NUM", "START_TIME", "STOP_TIME", "VTRxBiasStart", "VTRxBiasStop", "VTRxModulationStart", "VTRxModulationStop"])

    # Calibration loop
    for (b_start, b_stop, m_start, m_stop) in CALIBRATION_PARAMS:
        print(f"\n" + "="*50)
        print(f"   Starting calibration: Bias [{b_start}-{b_stop}], Mod [{m_start}-{m_stop}]")
        print("="*50)
        
        # 1. Modify XML parameters
        update_xml_settings(b_start, b_stop, m_start, m_stop)
        
        # 2. Get the current run_number to pass to the parser
        run_num = get_current_run_number()
        
        # 3. Start the parser in the background (listening mode only)
        # Assuming log_to_csv_2.5.py is in the same directory as this script
        parser_cmd = f"python3 log_to_csv_2.5.py --no_launch --run {run_num} --calibration vtrx --output_csv_name monitoring_auto_vtrx_calib.csv --heartbeat_seconds 0"
        parser_proc = subprocess.Popen(parser_cmd, shell=True)
        
        # Safety pause to allow the listener to initialize
        time.sleep(1)
        
        # 4. Record the start time and launch Ph2_ACF
        start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        
        # Use the absolute path of the current XML for safety
        xml_abspath = os.path.abspath(os.path.join(TEST_DIR, XML_FILE))
        daq_cmd = f"cd {PH2_ACF_DIR} && source setup.sh && cd {TEST_DIR} && CMSITminiDAQ -f {xml_abspath} -c vtrx"
        
        subprocess.run(
            daq_cmd,
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
            )
        
        # 5. Ph2_ACF has finished: record the end time
        stop_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        
        # Save the row in the timing file
        with open(TIMES_CSV, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([run_num, start_time, stop_time, b_start, b_stop, m_start, m_stop])
            
        # Wait for the parser to shut down naturally after processing the CMSITminiDAQ closing line
        parser_proc.wait()
        
    print("\n[+] All VTRx calibration iterations completed successfully.")