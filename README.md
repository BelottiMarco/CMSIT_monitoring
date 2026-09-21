# CMS Inner Tracker Hardware Monitoring Pipeline

A real-time, three-tier hardware telemetry pipeline for the CMS Inner Tracker (`Ph2_ACF`) upgrade. This pipeline extracts CROC (RD53B) and LpGBT telemetry from DAQ logs, applies physics scaling and temperature-compensated ADC calibrations, and streams Prometheus metrics to Grafana with sub-5-second latency.

---

## 🏗️ Architecture Overview

+------------------+         +--------------------+         +--------------------+         +--------------------+|     Ph2_ACF      |  log    | log_to_csv_2.6.py  |  write  | csv_chunker_0.4.py |  chunk  |   exporter_2.4.py  || (DAQ / Telemetry)| ------> | (Telemetry Parser) | ------> |  (Batch Chunker)   | ------> |(Prometheus Exporter|+------------------+         +--------------------+         +--------------------+         +--------------------+|                              |                              |v                              v                              vmonitoring_FULL.csv              /chunks/*.csv                  HTTP :8000|vPrometheus & Grafana
1. **Parser (`log_to_csv_2.6.py`)**: Tails `Ph2_ACF` logs, decodes hardware hierarchies (Board, Hybrid, Chip, eFuse), converts CROC register values, and calibrates uncalibrated LpGBT ADC channels.
2. **Chunker (`csv_chunker_0.4.py`)**: Groups complete telemetry cycles into dynamic CSV chunks using signature checking, while preventing disk saturation via atomic truncation (`max_mb`).
3. **Exporter (`exporter_2.4.py`)**: Consumes CSV chunks, exposes Prometheus Gauges, and automatically evicts stale metrics (`stale_after`) to avoid persistent "ghost" values when DAQ stops.

---

## 📋 Prerequisites

* **Python**: `3.8+`
* **Dependencies**:
  ```bash
  pip install prometheus_client pandas numpy
⚙️ Configuration (monitoring.ini)All modules share a single configuration file. Create a file named monitoring.ini in your working directory:Ini, TOML[paths]
# Path where Ph2_ACF writes logs or where raw stdout is saved
log_file = /path/to/Ph2_ACF.log
# Directory where intermediate CSV chunks will be stored
output_csv_dir = ./MonitoringCSV
# Path to the LpGBT custom calibration file
calib_file = ./lpgbt_calibration.csv

[chunker]
# Max size (in MB) for monitoring_FULL.csv before triggering atomic truncation
max_mb = 1.0

[exporter]
# Prometheus exporter HTTP port
port = 8000
# Timeout (in seconds) after which inactive metrics are evicted from Prometheus
stale_after = 150
🚀 Quick Start GuideOption A: Running Components Manually (Recommended for Debugging)Open three separate terminal windows (or use tmux/screen) and launch the services in order:1. Start the Prometheus ExporterBashpython3 exporter_2.4.py --config monitoring.ini
2. Start the Batch ChunkerBashpython3 csv_chunker_0.4.py --config monitoring.ini
3. Start the Telemetry ParserBashpython3 log_to_csv_2.6.py --config monitoring.ini
Option B: Piping Ph2_ACF Output DirectlyIf you want to stream Ph2_ACF execution directly in real-time:Bash# Terminal 1: Exporter
python3 exporter_2.4.py --config monitoring.ini

# Terminal 2: Chunker
python3 csv_chunker_0.4.py --config monitoring.ini

# Terminal 3: Run Ph2_ACF DAQ and pipe stdout directly
CMSITminiDAQ -f CMSIT.xml -c status | python3 log_to_csv_2.6.py --config monitoring.ini
Option C: Launching as Background ProcessesTo run the entire pipeline in the background for long monitoring runs:Bashpython3 exporter_2.4.py --config monitoring.ini > exporter.log 2>&1 &
python3 csv_chunker_0.4.py --config monitoring.ini > chunker.log 2>&1 &
python3 log_to_csv_2.6.py --config monitoring.ini > parser.log 2>&1 &
To stop all monitoring background tasks:Bashpkill -f "python3.*(exporter|csv_chunker|log_to_csv)"
📊 Prometheus & Grafana Setup1. Prometheus Configuration (prometheus.yml)Add the exporter target to your Prometheus configuration file:YAMLscrape_configs:
  - job_name: 'cms_it_monitoring'
    scrape_interval: 1s  # Recommended for low latency
    static_configs:
      - targets: ['localhost:8000']
2. Verified Performance MetricsScrape Latency: ~4–5 seconds end-to-end delay (Ph2_ACF log line $\rightarrow$ Grafana plot point).Metric Eviction: Hardware components turned off or disconnected will automatically disappear from Prometheus after stale_after seconds (default: 150s).🛠️ File Structure.
├── config_loader.py        # Centralized INI configuration parser
├── csv_schema.py           # Standardized 14-field CSV header definitions
├── log_to_csv_2.6.py       # Telemetry parser and LpGBT calibration module
├── csv_chunker_0.4.py      # Signature-based batch chunker and dynamic truncator
├── exporter_2.4.py         # Prometheus Gauge exporter with stale metric eviction
├── lpgbt_calibration.csv   # Polynomial calibration constants for LpGBT ADCs
└── monitoring.ini          # Global configuration file
