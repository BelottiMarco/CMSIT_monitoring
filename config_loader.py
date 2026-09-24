"""Shared config-file loading for the monitoring pipeline
(log_to_csv.py, csv_chunker.py, exporter.py).

All three scripts read the SAME monitoring.ini, so paths and thresholds
only need to be set in one place instead of being retyped identically on
three separate command lines. Every value is still overridable with a
plain CLI flag for one-off runs - the config file only supplies defaults.
"""

import argparse
import configparser
import os

DEFAULT_CONFIG_PATH = "./monitoring.ini"


def load_config(argv=None):
    """Two-phase parse: first pull out just --config (if given) and/or the
    $MONITORING_CONFIG environment variable, so we know WHICH ini file to
    load before building each script's real argument parser with defaults
    populated from it.

    Returns (config, config_path). config is always a valid (possibly
    empty) configparser.ConfigParser - callers can safely call cfg(...) on
    it whether or not a file was actually found.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=str, default=None)
    pre_args, _ = pre.parse_known_args(argv)

    config_path = pre_args.config or os.environ.get("MONITORING_CONFIG", DEFAULT_CONFIG_PATH)

    config = configparser.ConfigParser()
    if os.path.exists(config_path):
        config.read(config_path)
        print(f"-> Loaded configuration from: {config_path}")
    else:
        print(f"-> No config file found at '{config_path}'; using command-line arguments and built-in defaults.")

    return config, config_path


def cfg(config, section, key, fallback=None, kind=str):
    """One consistent way to pull a value out of the config, regardless of
    type, with a graceful fallback if the section/key/file isn't there:

        cfg(config, "paths", "output_csv_dir", fallback="./MonitoringCSV")
        cfg(config, "exporter", "stale_after", fallback=150, kind=int)
        cfg(config, "log_to_csv", "log_time", fallback=False, kind=bool)
    """
    if not config.has_section(section):
        return fallback
    getters = {
        str: config.get,
        int: config.getint,
        float: config.getfloat,
        bool: config.getboolean,
    }
    return getters[kind](section, key, fallback=fallback)
