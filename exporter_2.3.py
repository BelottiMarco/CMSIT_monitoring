import argparse
import time
import pandas as pd
from prometheus_client import start_http_server
from prometheus_client.core import GaugeMetricFamily, REGISTRY
import glob
import os

class ChunkCollector:
    def __init__(self, chunk_dir, stale_after=150):
        self.chunk_dir = chunk_dir
        self.stale_after = stale_after
        self.latest_data = {}   # labels -> value
        self.last_seen = {}     # labels -> time.time() of last fresh update (PER series)

    def _consume_chunks(self):
        chunk_files = sorted(glob.glob(os.path.join(self.chunk_dir, "*.csv")))
        for path in chunk_files:
            try:
                df = pd.read_csv(path, on_bad_lines="skip")
                for _, row in df.iterrows():
                    try:
                        labels = (
                            str(row.get('BOARD', '')),
                            str(row.get('OPTICAL_GROUP', '')),
                            str(row.get('PORTCARD_ID', '')),
                            str(row.get('LpGBT_EFUSE', '')),
                            str(row.get('HYBRID_ID', '')),
                            str(row.get('CHIP', '')),
                            str(row.get('CHIP_EFUSE', '')),
                            str(row.get('MODULE_ID', '')),
                            str(row.get('REGISTER', '')),
                            str(row.get('UNIT', 'none')) if pd.notna(row.get('UNIT')) else 'none'
                        )

                        numeric_val = parse_numeric(row.get('VALUE'))
                        if numeric_val is not None:
                            self.latest_data[labels] = numeric_val
                            self.last_seen[labels] = time.time()
                    except Exception:
                        # one malformed row shouldn't discard the whole chunk
                        continue
            except Exception as e:
                print(f"Skipping unreadable chunk {path}: {e}")
            finally:
                # consumed (or unreadable/corrupt) -> delete either way,
                # no point keeping a chunk we've already merged or can't parse
                os.remove(path)

    def collect(self):
        self._consume_chunks()

        # Prune each series independently: a chip/register that's gone quiet
        # should disappear on its own, without waiting for or depending on
        # every OTHER series to also go quiet.
        now = time.time()
        stale_labels = [labels for labels, t in self.last_seen.items() if now - t > self.stale_after]
        for labels in stale_labels:
            del self.latest_data[labels]
            del self.last_seen[labels]

        gauge = GaugeMetricFamily(
            'hardware_monitoring_live_value',
            'Hardware monitoring register reading',
            labels=['board', 'optical_group', 'portcard_id', 'lpgbt_efuse',
                     'hybrid_id', 'chip', 'chip_efuse', 'module_id', 'register', 'unit']
        )

        for labels, val in self.latest_data.items():
            gauge.add_metric(labels=labels, value=val)

        yield gauge



def parse_numeric(val):
    if pd.isna(val):
        return None
    val_str = str(val).strip()
    if val_str.lower().startswith('0x'):
        try:
            return float(int(val_str, 16))
        except ValueError:
            return None
    try:
        return float(val_str)
    except ValueError:
        return None

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Upload data to Prometheus in real-time.")
    parser.add_argument("--csv_dir", type=str, required=True, help="Path to chunk directory")
    parser.add_argument("--stale_after", type=int, default=150,
                         help="Seconds without a fresh reading before a series is dropped "
                              "(default: 150). Set this comfortably above the slowest "
                              "register's real read interval, or it will be pruned as "
                              "'stale' between legitimate readings, causing gaps in Grafana.")

    args = parser.parse_args()

    REGISTRY.register(ChunkCollector(chunk_dir=args.csv_dir, stale_after=args.stale_after))
    start_http_server(8000)
    print(f"Metrics exposed at http://localhost:8000/metrics (stale_after={args.stale_after}s)")
    
    while True:
        time.sleep(100)