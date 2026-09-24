"""Shared CSV schema for the monitoring pipeline
(log_to_csv.py -> csv_chunker.py -> exporter.py).
"""

# ==== CSV ===========================================================================================================================
# List of fieldsnae for the csv file
FIELDNAMES = [
    "BOARD", "OPTICAL_GROUP", "PORTCARD_ID", "LpGBT_EFUSE",
    "HYBRID_ID", "CHIP", "CHIP_EFUSE", "MODULE_ID",
    "DATE", "TIME", "REGISTER", "VALUE", "ERROR", "UNIT"
]

# ====================================================================================================================================


"""
CROC and LpGBT registers to rearch in the LOG file (only used
by log_to_csv.py)
"""
# ==== CROC ==========================================================================================================================
# List of CROC register to search
CROC_REGISTERS = [
    "VINA", "VDDA", "ANA_IN_CURR", "VIND", "VDDD", "DIG_IN_CURR", "Iref",
    "POLY_REL_TEMPSENS_TOP", "POLY_REL_TEMPSENS_BOTTOM", "POLY_ABS_TEMPSENS_TOP",
    "POLY_ABS_TEMPSENS_BOTTOM", "TEMPSENS_ANA_SLDO", "TEMPSENS_DIG_SLDO",
    "TEMPSENS_CENTER", "INTERNAL_NTC_REL", "INTERNAL_NTC_ABS",
    "Hybrid voltage", "Hybrid temperature"
]

# ====================================================================================================================================


# ==== LpGBT =========================================================================================================================
# List of LpGBT ADC register to search
LPGBT_ADC_REGISTERS = ["ADC0", "ADC1", "ADC2", "ADC3", "ADC4", "ADC5", "ADC6", "ADC7"]
# List of LpGBT Voltage register to search
LPGBT_VOLTAGE_REGISTERS = ["VDDIO", "VDDTX", "VDDRX", "VDD", "VDDA"]

# Mapping of non-calibrated ADC channels with the LpGBT eFuse to convert into voltage values
PROBLEMATIC_LPGBT_ADC_REGISTER = {
    '922CE0AE': [0, 1, 2, 3, 4, 5, 7],
    '52AE68FD': [   1, 2, 3, 4, 5, 7],
    '52AE2086': [0, 1, 2, 3, 4, 5, 7],
}

# Mapping of the ADC channels to the physical measured quantity --> To be implemented
LPGBT_ADC_CONNECTION = {
    '922CE0AE': {0: "TEMP2",        1: "TEMP1",       2: "VDD1V2", 3: "VDD2V5", 4: "V_IN", 5: "1K", 6: "VTRX_TEMP", 7: "VTRX_RSSI"},
    '52AE68FD': {0: "PORTCARD_NTC", 1: "TEMP1_DCDC",  2: "VDD1V2", 3: "VDD2V5", 4: "V_IN", 5: "1K", 6: "VTRX_TEMP", 7: "VTRX_RSSI"},
    '52AE2086': {0: "ADC0",         1: "ADC1",        2: "VDD1V2", 3: "VDD2V5", 4: "V_IN", 5: "1K", 6: "OPTO_TEMP", 7: "OPTO_RSSI"},
}
# ====================================================================================================================================