# Cross Check of the Hardware Monitoring for the CMS Inner Tracker Upgrade (`Ph2_ACF`)

Real-time extraction of hardware monitoring data (temperatures, voltages, currents)
from `Ph2_ACF`'s run `LOG`, exposed as live Prometheus metrics for Grafana dashboards.

> For the physics/detector background, the full design structure, and validation
> results, see [`CMSIT_MONITORING_REPORT.pdf`](./CMSIT_MONITORING_REPORT.pdf).
> This README only covers what you need to install, configure, and run the code.

## Architecture Overview

`Ph2_ACF` writes monitoring data (CROC and LpGBT registers) as unstructured text into
its run `LOG` file. Prometheus and Grafana need structured, continuously-refreshed
numeric time series instead. This pipeline bridges that gap in three decoupled stages:

```
Ph2_ACF (LOG file)
        │
        ▼
[1] log_to_csv.py    parses the LOG in real time, writes
                      one continuous "Full CSV"
        │
        ▼
[2] csv_chunker.py   tails the Full CSV, splits it into small
        │             chunk files, bounds its size on disk
        ▼
[3] exporter.py      watches the chunk directory, exposes the
        │             latest values to Prometheus
        ▼
Prometheus / Grafana
```

Each stage has exactly one job, and stage 1 is the only one that would need to
change if `Ph2_ACF` (or the DTC software) is ever modified to write CSV natively
beyond the `LOG` file — stages 2 and 3 don't know or care how the Full CSV was
produced.

## Repository structure

```
.
├── log_to_csv.py                    # Stage 1: LOG -> Full CSV (can also launch CMSITminiDAQ itself)
├── csv_chunker.py                   # Stage 2: Full CSV -> small chunk files, with disk-size bounding
├── exporter.py                      # Stage 3: chunk files -> Prometheus /metrics endpoint
├── config_loader.py                 # Shared monitoring.ini loader, used by all three scripts above
├── csv_schema.py                    # Shared CSV column layout + register lookup tables
├── monitoring.ini                   # Default configuration (paths, settings) <-- edit this first
├── auto_vtrx_calib.py               # Example of multiple calibration and parsing with log_to_csv.py
├── report.pdf                       # Full technical report (physics context, code design and pipeline, results)
├── prometheus.yml                   # See "Prometheus setup" below
└── Ph2_ACF_xml/                     # XML configuration file for Ph2_ACF used in the project
    ├── CMSIT_config_2_CROC_1_LpGBT.xml
    ├── CMSIT_config_3_LpGBT.xml
    └── CMSIT_config_2_LpGBT.xml
```

## Requirements

- Python 3.9+ with the following packeges:
  - ```pandas```
  - ```prometheus_client```
- Prometheus and Grafana (in the next section is explained how to install the basic version)
- A running `Ph2_ACF` installation, if you want `log_to_csv.py` to launch
  `CMSITminiDAQ` itself rather than just parsing an existing `LOG` file

## Installation

### Code
Clone the whole directory in your home directory:
```bash
cd /home/<username>
git clone https://github.com/BelottiMarco/CMSIT_monitoring
cd CMSIT_monitoring
```
### Grafana
Download (the appropriate release) and run Grafana:
```bash
cd /home/<username>
mkdir grafana
cd grafana/
sudo apt-get install -y adduser libfontconfig1 musl
wget https://dl.grafana.com/oss/release/grafana-13.2.0.linux-amd64.tar.gz
tar -xzf grafana-13.2.0.linux-amd64.tar.gz
mv grafana-13.2.0/* .
rm grafana-13.2.0.linux-amd64.tar.gz 
rmdir grafana-13.2.0/

nohup ./bin/grafana server --homepath=/home/<username>/grafana cfg:server.http_port=3300 > grafana.log 2>&1 &
echo $! > grafana.pid
curl http://localhost:3300/api/health
```

### Prometheus
Download (the appropriate release) and run Prometheus:
```bash
cd /home/<username>
mkdir prometheus
cd prometheus/
wget https://github.com/prometheus/prometheus/releases/download/v3.14.0/prometheus-3.14.0.linux-amd64.tar.gz
tar -xzf prometheus-3.14.0.linux-amd64.tar.gz
mv prometheus-3.14.0.linux-amd64/* .
rm prometheus-3.14.0.linux-amd64.tar.gz 
rmdir prometheus-3.14.0.linux-amd64/
cp /home/<username>/CMSIT_monitoring/prometheus.yml .
./promtool check config prometheus.yml

nohup ./prometheus --config.file=prometheus.yml --storage.tsdb.path=data --web.enable-lifecycle > prometheus.log 2>&1 &
echo $! > prometheus.pid
curl http://localhost:9090/-/ready
```

In this way both Grafana and Prometheus are lunched in background. To terminate them use the command:
```bash
ps aux | grep <program>
kill <program_pid>
```

## Configuration

All three scripts read their defaults from **`monitoring.ini`**, so you only need
to set paths and thresholds in one place instead of retyping them on every command
line. Open it and set at least these two values for your setup:

```ini
[paths]
ph2_acf_dir    = /path/to/Ph2_ACF/
output_csv_dir = /path/to/MonitoringCSV
```

Everything else (chunk directory, Full CSV path, exporter's target directory) is
*derived* from `output_csv_dir` automatically, so those two consumer scripts can
never drift out of agreement about where files actually live.

Any value in `monitoring.ini` can still be overridden per-run with the matching
`--flag` — the config file only supplies the default when a flag is omitted. Run
any script with `--help` to see its full flag list, or point at a different config
entirely with `--config /path/to/other.ini`.

**Pay Attention:**
1. To avoid mismatch between the different files,
it's always suggested to use a `monitoring.ini` configuration file.
2. `log_to_csv.py`'s per-chip LpGBT ADC calibration expects a file at
`<ph2_acf_dir>/settings/lpGBTFiles/lpgbt_calibration.csv`. If it's missing,
calibration is skipped with a warning rather than failing — uncalibrated LpGBT ADC
channels will simply pass through unconverted.
3. Modify, in `csv_schema.py`, the list of problematic ADC corresponding to each LpGBT eFuse for the actual setup.

## Quick start

Launch all three stages, each in its own terminal (it's better to launch the `exporter.py` with `nohup ... &`), in this
order:

```bash
# Terminal 1 - exporter (start first, runs continuously in background)
nohup python3 exporter.py > exporter.log 2>&1 &

# Terminal 2 - chunker (better run one per Ph2_ACF run)
python3 csv_chunker.py

# Terminal 3 - parser (one per Ph2_ACF run; also launches CMSITminiDAQ by default)
python3 log_to_csv.py
```

With no flags, everything comes from `monitoring.ini`.
Metrics are then available at `http://localhost:8000/metrics`, ready for Prometheus to scrape.

 **Note:** `physics` is the default calibration,
because in the laboratory-setup (not exposed to particles) nothing but the monitoring happenes. 

### Common variations

```bash
# Re-process an OLD (already-finished) LOG file offline instead of launching Ph2_ACF
python3 log_to_csv.py --no_launch --run 42 --date 2026-09-01 --log_time
# --> (see "Offline LOG parsing" in the report for the prometheus.yml/exporter.py changes needed)

# Write the Full CSV somewhere else for one run, without touching monitoring.ini
python3 log_to_csv.py --output_csv_name monitoring_special_run.csv

# Use a completely separate config (e.g. a second, independent monitoring setup)
python3 exporter.py --config ./monitoring_setup2.ini
```

## Command-line reference

Every flag below has a matching key in `monitoring.ini` (grouped by section); 
pass `--help` to any script for the list with full descriptions.

**`log_to_csv.py`**

| Flag | Purpose |
|---|---|
| `--calibration` | Which `Ph2_ACF` calibration to run (`physics`, `vtrx`, ...) |
| `--xml_file` | Configuration XML passed to `CMSITminiDAQ` |
| `--run` | Run number (omit or `-1` to auto-detect from `RunNumber.txt`) |
| `--no_launch` | Parse an existing/external `LOG` only, don't launch `CMSITminiDAQ` |
| `--timeout` | Seconds of inactivity before considering the run finished |
| `--heartbeat_seconds` | Print a status line periodically even when idle (keeps a quiet SSH session alive) |
| `--log_time` | Use the timestamps printed in the LOG file rather than wall-clock time (for offline parsing) |
| `--output_csv_name` | Name of the Full CSV this run writes |

**`csv_chunker.py`**

| Flag | Purpose |
|---|---|
| `--input_csv` | Full CSV to tail (default: derived from `monitoring.ini`, matches `log_to_csv.py`'s default) |
| `--chunk_dir` | Where to write small chunk files (default: derived, matches `exporter.py`'s default) |
| `--max_mb` | Truncate the Full CSV once it exceeds this size and all chunks have been drained |
| `--debug_csv` | Optional permanent, never-truncated mirror of every row, for offline inspection |

**`exporter.py`**

| Flag | Purpose |
|---|---|
| `--csv_dir` | Chunk directory to watch (default: derived, matches `csv_chunker.py`'s default) |
| `--stale_after` | Seconds without a fresh reading before a series is dropped from Prometheus |

## Runtime file layout

Given `output_csv_dir = /path/to/MonitoringCSV`, a default running pipeline produces:

```
MonitoringCSV/
├── monitoring_FULL.csv          # Stage 1 output, continuously tailed by stage 2
├── monitoring_DEBUG.csv         # Optional, only if --debug_csv is set
└── chunks/
    ├── <timestamp>.csv.tmp      # Chunk currently being written by stage 2
    └── <timestamp>.csv          # Finalized chunk, ready for stage 3 to consume + delete
```

Chunk files are ephemeral by design — stage 3 deletes each one immediately after
reading it, so `chunks/` should never accumulate.

## Tuning tips

- **`--stale_after` (exporter)** must be set comfortably above the *slowest*
  monitored register's real read interval and MonitoringSpleepTime in the XML configuration file,
  or it will be pruned as "stale" between legitimate readings —
  visible in Grafana as periodic gaps in an otherwise healthy series.
- **`--max_mb` (chunker)** trades off disk usage for the Full CSV against how long a burst of chunks
  can sit undrained before truncation is skipped; the pipeline never discards data
  to enforce this limit, it just waits until it's safe.
- **`--timeout` (log_to_csv)** controls how long to wait for new `LOG` lines before
  declaring a run finished — this is what lets the pipeline recover automatically if
  `Ph2_ACF` is interrupted without reaching its normal end-of-run marker.


## Prometheus setup

Point Prometheus at the exporter's `/metrics` endpoint. A minimal `prometheus.yml`:

```yaml
global:
  scrape_interval: 1s
  evaluation_interval: 1s
scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
        labels:
          app: "prometheus"

  - job_name: "csv_exporter"
    scrape_interval: 1s
    static_configs:
      - targets: ["127.0.0.1:8000"]

# storage:
#  tsdb:
#   out_of_order_time_window: <period_to_go_back_in_time>
```

A short `scrape_interval` (1–5s) is recommended so fast-changing readings aren't
missed between scrapes.
Enable the last code lines for off-line `LOG` scraping, and launch again Prometheus.

## Grafana

Query the metric `hardware_monitoring_live_value` in Grafana, filtering/grouping by
any of its labels: `board`, `optical_group`, `portcard_id`, `lpgbt_efuse`,
`hybrid_id`, `chip`, `chip_efuse`, `module_id`, `register`, `unit`. For example,
to plot all the register except `LPGBT_PUSMStatus` for one of the LpGBT:

```promql
hardware_monitoring_live_value{lpgbt_efuse="52AE68FD", register!="LPGBT_PUSMStatus"}
```

## Other applications

The pipeline doesn't require `log_to_csv.py` to be the one launching `Ph2_ACF` —
`--no_launch` lets an external script drive `Ph2_ACF`'s lifecycle instead (for
example, an iterative calibration that changes an XML parameter between runs) while
`log_to_csv.py` just parses whatever `LOG` appears. See the report for a worked
example and the code `auto_vtrx_calib.py` in this directory.
