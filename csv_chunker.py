import argparse
import csv
import time
import datetime
import os
from pathlib import Path

from config_loader import load_config, cfg
from csv_schema import FIELDNAMES

class ChunkWriter:
    def __init__(self, chunk_dir, fieldnames):
        self.chunk_dir = Path(chunk_dir)
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        self.fieldnames = fieldnames
        self.first_row_signature = None
        self.chunks_completed = 0

        # Clean up any orphan temporary files from previous sessions
        stale_tmp_files = list(self.chunk_dir.glob("*.csv.tmp"))
        for stale in stale_tmp_files:
            stale.unlink()
        if stale_tmp_files:
            print(f"-> Cleaned up {len(stale_tmp_files)} orphaned .tmp chunk(s) from a previous session.")
            
        self._open_new_chunk()

    def _get_signature(self, row):
        """
        Extract the unique identifier of the register excluding fields that vary
        on each read (VALUE, ERROR, DATE, TIME).
        """
        ignore_keys = {"VALUE", "ERROR", "DATE", "TIME"}
        return tuple(str(row.get(k, "")) for k in self.fieldnames if k not in ignore_keys)

    def _open_new_chunk(self):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.tmp_path = self.chunk_dir / f"{ts}.csv.tmp"
        self.final_path = self.chunk_dir / f"{ts}.csv"
        self.file = open(self.tmp_path, mode="w", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=self.fieldnames)
        self.writer.writeheader()
        self.file.flush()
        self.rows_in_chunk = 0
        
    def write_row(self, row):
        chunk_closed = False
        current_sig = self._get_signature(row)

        # If the chunk already contains data and the current row matches the first register of the cycle:
        # close the current chunk (making it visible to Prometheus) and open a new chunk.
        if self.rows_in_chunk > 0 and current_sig == self.first_row_signature:
            self._close_and_finalize()
            self._open_new_chunk()
            self.chunks_completed += 1
            chunk_closed = True
            
        # Record the signature of the first element of the new cycle
        if self.rows_in_chunk == 0:
            self.first_row_signature = current_sig

        self.writer.writerow(row)
        self.file.flush()
        self.rows_in_chunk += 1
        return chunk_closed

    def _close_and_finalize(self):
        self.file.close()
        # only publish the chunk if it actually has data beyond the header
        if self.rows_in_chunk > 0:
            os.rename(self.tmp_path, self.final_path)
        else:
            if self.tmp_path.exists():
                os.remove(self.tmp_path)

    def close(self):
        self._close_and_finalize()


def tail_and_reduce(file_path, writer, max_mb=1.0, debug_csv_path=None):
    """Reads the CSV in real time, saves it to a permanent debug file (if requested), and truncates it when file size exceeds max_mb."""
    while not os.path.exists(file_path):
        time.sleep(1)
        
    debug_file = None
    debug_writer = None
    max_bytes = max_mb * 1024 * 1024
    
    # If a debug file is specified, open it in permanent append mode
    if debug_csv_path:
        file_exists = os.path.exists(debug_csv_path) and os.path.getsize(debug_csv_path) > 0
        debug_file = open(debug_csv_path, mode="a", newline="", encoding="utf-8")
        debug_writer = csv.DictWriter(debug_file, fieldnames=FIELDNAMES)
        if not file_exists:
            debug_writer.writeheader()
            debug_file.flush()
        print(f"-> DEBUG mode active: Saving full history appending to {Path(debug_csv_path).name}\n")

    try:
        with open(file_path, 'r+', encoding='utf-8') as f:
            header = f.readline()
            while not header:
                time.sleep(0.1)
                header = f.readline()
                
            while True:
                pos = f.tell()
                line = f.readline()
                
                # End of file reached (no new line written by appender)
                if not line or not line.endswith('\n'):
                    f.seek(pos)
                    
                    # Check if file size exceeds the target limit
                    current_size = os.path.getsize(file_path)
                    if current_size >= max_bytes:
                        # Confirm exporter has processed chunks (.csv files removed)
                        ready_chunks = list(writer.chunk_dir.glob("*.csv"))
                        
                        if len(ready_chunks) == 0:
                            current_size_mb = current_size / (1024 * 1024)
                            print(f"-> File size ({current_size_mb:.2f} MB) exceeded threshold ({max_mb} MB) and scrapings confirmed.\n   Truncating temporary file {Path(file_path).name}...")
                            
                            # 1. Read unread lines written while waiting
                            unread_data = f.read()
                            
                            # 2. Truncate file and reset pointer
                            f.seek(0)
                            f.truncate(0)
                            
                            # 3. Rewrite header and unread buffer
                            f.write(header)
                            if unread_data:
                                f.write(unread_data)
                            f.flush()
                            
                            writer.chunks_completed = 0
                            print("   File reduced successfully. Resuming monitoring.")
                    
                    time.sleep(0.1)
                    continue
                
                # Parse row
                values = line.strip('\n').split(',')
                while len(values) < len(FIELDNAMES):
                    values.append("")
                    
                row_dict = dict(zip(FIELDNAMES, values))
                
                # 1. Standard chunker output for Prometheus
                writer.write_row(row_dict)
                
                # 2. Write to permanent DEBUG file (if enabled)
                if debug_writer:
                    debug_writer.writerow(row_dict)
                    debug_file.flush()

    finally:
        if debug_file:
            debug_file.close()
            
            
if __name__ == "__main__":
    config, config_path = load_config()

    # input_csv and chunk_dir are both DERIVED from the same [paths]
    # output_csv_dir that log_to_csv.py uses - so this stays in sync with
    # log_to_csv.py automatically instead of needing the same base path
    # retyped identically on both command lines.
    output_csv_dir = cfg(config, "paths", "output_csv_dir", fallback="./MonitoringCSV")
    output_csv_name = cfg(config, "log_to_csv", "output_csv_name", fallback="monitoring_FULL.csv")
    default_input_csv = f"{output_csv_dir}/{output_csv_name}"
    default_chunk_dir = f"{output_csv_dir}/chunks"

    default_debug_csv = cfg(config, "csv_chunker", "debug_csv", fallback=None)
    if default_debug_csv:
        default_debug_csv = f"{output_csv_dir}/{default_debug_csv}"

    parser = argparse.ArgumentParser(description="Tail and reduce a full CSV and produce chunked CSVs for Prometheus based on register reading cycles.")
    parser.add_argument("--config", type=str, default=config_path,
                         help="Path to the shared monitoring.ini (default: ./monitoring.ini or $MONITORING_CONFIG)")
    parser.add_argument("--input_csv", type=str, default=default_input_csv,
                         help="Path to the full CSV file to monitor "
                              "(default: derived from [paths] output_csv_dir + [log_to_csv] output_csv_name)")
    parser.add_argument("--chunk_dir", type=str, default=default_chunk_dir,
                         help="Directory to save partial chunked CSVs "
                              "(default: {output_csv_dir}/chunks - same directory exporter.py should point at)")
    parser.add_argument("--max_mb", type=float,
                         default=cfg(config, "csv_chunker", "max_mb", fallback=1.0, kind=float),
                         help="Maximum size in MB before truncating the input CSV file")
    parser.add_argument("--debug_csv", type=str, default=default_debug_csv,
                         help="Optional path to save the FULL debug history without ever truncating it")
    
    args = parser.parse_args()
    
    
    print("\n" + "="*40 + " START CSV CHUNKER " + "="*41 + "\n")

    # Check and remove existing file on startup if present
    if os.path.exists(args.input_csv):
        os.remove(args.input_csv)
        print(f"-> Removed {args.input_csv} from previous run.\n")
    
    print(f"-> Tailing {args.input_csv}\n"
          f"-> Outputting chunks to {args.chunk_dir}\n"
          f"-> Cleaning when size exceeds {args.max_mb} MB.\n")
    
    writer = ChunkWriter(
        chunk_dir=args.chunk_dir,
        fieldnames=FIELDNAMES
    )
    
    try:
        tail_and_reduce(args.input_csv, writer, max_mb=args.max_mb, debug_csv_path=args.debug_csv)
    except KeyboardInterrupt:
        print("\n-> Chunker manually interrupted.\n")
    finally:
        writer.close()
        
    print("="*100 + "\n")