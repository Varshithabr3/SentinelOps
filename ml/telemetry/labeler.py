"""
labeler.py — SentinelOps Phase 4A Ground-Truth Labeler
=======================================================
Reads raw telemetry CSV produced by collector.py and overlays
chaos-engineering ground-truth labels from the API Gateway's
/api/chaos/history endpoint.

Label logic
-----------
For every row in the raw CSV:
  - If the row's (timestamp, service) falls inside a chaos event window
    for that service: is_anomaly = 1, failure_type = <chaos type>
  - Otherwise:        is_anomaly = 0, failure_type = "normal"

Output columns (all columns from raw CSV PLUS):
  failure_type    "normal" | "cpu_spike" | "memory_growth" | "latency" |
                  "error_rate" | "service_unavailability" |
                  "dependency_failure" | "traffic_spike"
  is_anomaly      0 | 1

Usage:
    # Label using live chaos history from gateway
    python -m ml.telemetry.labeler

    # Label against a cached history JSON file
    python -m ml.telemetry.labeler --history-file data/chaos_history.json

    # Custom paths
    python -m ml.telemetry.labeler --input data/telemetry_raw.csv --output data/telemetry_labeled.csv
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests

# ── defaults ──────────────────────────────────────────────────────────────────
GATEWAY_URL        = os.getenv("GATEWAY_URL", "http://localhost:8000")
DEFAULT_RAW_CSV    = Path(__file__).parent.parent / "data" / "telemetry_raw.csv"
DEFAULT_LABELED_CSV= Path(__file__).parent.parent / "data" / "telemetry_labeled.csv"
DEFAULT_HIST_JSON  = Path(__file__).parent.parent / "data" / "chaos_history.json"

OUTPUT_COLUMNS = [
    "timestamp",
    "service",
    "cpu_usage",
    "memory_mb",
    "memory_pct",
    "request_rate",
    "latency_p95",
    "error_rate",
    "availability",
    "failure_type",
    "is_anomaly",
]


# ── Chaos history helpers ─────────────────────────────────────────────────────

def _fetch_chaos_history(gateway_url: str = GATEWAY_URL) -> list:
    """Fetch the consolidated chaos history from the API Gateway."""
    url = f"{gateway_url.rstrip('/')}/api/chaos/history"
    try:
        resp = requests.get(url, timeout=10.0)
        resp.raise_for_status()
        history = resp.json()
        print(f"[labeler] Fetched {len(history)} chaos records from {url}")
        return history
    except Exception as exc:
        print(f"[WARN] Could not reach Gateway chaos history ({exc}). "
              "Returning empty — all rows will be labelled 'normal'.", file=sys.stderr)
        return []


def _load_chaos_history(path: Path) -> list:
    """Load chaos history from a local JSON file."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    print(f"[labeler] Loaded {len(data)} chaos records from {path}")
    return data


def _save_chaos_history(history: list, path: Path):
    """Persist fetched chaos history to JSON for reproducibility."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2)
    print(f"[labeler] Saved chaos history → {path}")


def _parse_iso(ts: str) -> float:
    """Parse ISO-8601 timestamp (with or without tz) to UTC Unix epoch float."""
    ts = ts.strip()
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        # Fallback: strip trailing Z
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ── Labeling logic ────────────────────────────────────────────────────────────

def _build_windows(history: list) -> list:
    """
    Convert chaos history records into a list of dicts:
      { service, failure_type, start_epoch, end_epoch }

    Records without end_timestamp are skipped (still active / never stopped).
    """
    windows = []
    for rec in history:
        svc   = rec.get("service", "")
        ftype = rec.get("failure_type", "unknown")
        start = rec.get("start_timestamp")
        end   = rec.get("end_timestamp")
        if not start or not end or not svc:
            continue
        windows.append({
            "service":      svc,
            "failure_type": ftype,
            "start_epoch":  _parse_iso(start),
            "end_epoch":    _parse_iso(end),
        })
    print(f"[labeler] Built {len(windows)} chaos windows for label overlay")
    return windows


def _label_row(row_epoch: float, service: str, windows: list):
    """
    Return (failure_type, is_anomaly) for a single CSV row.
    If multiple chaos windows overlap, the first match wins.
    """
    for w in windows:
        if w["service"] == service and w["start_epoch"] <= row_epoch <= w["end_epoch"]:
            return w["failure_type"], 1
    return "normal", 0


# ── Main labeling pipeline ────────────────────────────────────────────────────

def label(
    input_csv:    Path = DEFAULT_RAW_CSV,
    output_csv:   Path = DEFAULT_LABELED_CSV,
    history_file: Optional[Path] = None,
    gateway_url:  str  = GATEWAY_URL,
    save_history: bool = True,
) -> dict:
    """
    Read *input_csv*, apply chaos-based ground-truth labels, write *output_csv*.
    Returns a summary dict with row counts and class distribution.
    """
    input_csv  = Path(input_csv)
    output_csv = Path(output_csv)

    if not input_csv.exists():
        print(f"[ERROR] Raw CSV not found: {input_csv}", file=sys.stderr)
        sys.exit(1)

    # Load chaos history
    if history_file and Path(history_file).exists():
        history = _load_chaos_history(Path(history_file))
    else:
        history = _fetch_chaos_history(gateway_url)
        if save_history:
            _save_chaos_history(history, DEFAULT_HIST_JSON)

    windows = _build_windows(history)

    # Process CSV
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    anomaly_count = 0
    failure_type_counts: dict = {}

    with open(input_csv, "r", newline="", encoding="utf-8") as fin, \
         open(output_csv, "w", newline="", encoding="utf-8") as fout:

        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()

        for raw in reader:
            ts_str  = raw["timestamp"]
            service = raw["service"]
            try:
                row_epoch = _parse_iso(ts_str)
            except Exception:
                row_epoch = 0.0

            failure_type, is_anomaly = _label_row(row_epoch, service, windows)

            out_row = dict(raw)
            out_row["failure_type"] = failure_type
            out_row["is_anomaly"]   = is_anomaly

            # Keep only output columns (drop any extra)
            writer.writerow({k: out_row[k] for k in OUTPUT_COLUMNS if k in out_row})

            total += 1
            if is_anomaly:
                anomaly_count += 1
            failure_type_counts[failure_type] = failure_type_counts.get(failure_type, 0) + 1

    normal_count = total - anomaly_count
    summary = {
        "input":          str(input_csv),
        "output":         str(output_csv),
        "total_rows":     total,
        "normal_rows":    normal_count,
        "anomaly_rows":   anomaly_count,
        "anomaly_pct":    round(anomaly_count / total * 100, 2) if total else 0.0,
        "by_failure_type": failure_type_counts,
    }

    print(f"\n[labeler] Labeling complete.")
    print(f"  Output CSV       : {output_csv}")
    print(f"  Total rows       : {total}")
    print(f"  Normal  (0)      : {normal_count}")
    print(f"  Anomaly (1)      : {anomaly_count}  ({summary['anomaly_pct']}%)")
    print(f"  By failure type  :")
    for ft, cnt in sorted(failure_type_counts.items()):
        print(f"    {ft:<30} {cnt}")

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SentinelOps Phase 4A: Ground-truth labeler")
    p.add_argument("--input",        type=Path, default=DEFAULT_RAW_CSV,    help="Raw telemetry CSV")
    p.add_argument("--output",       type=Path, default=DEFAULT_LABELED_CSV,help="Labeled output CSV")
    p.add_argument("--history-file", type=Path, default=None,               help="Local chaos history JSON (skips gateway fetch)")
    p.add_argument("--gateway",      type=str,  default=GATEWAY_URL,        help="API Gateway base URL")
    p.add_argument("--no-save",      action="store_true",                   help="Do not save fetched chaos history to JSON")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    label(
        input_csv    = args.input,
        output_csv   = args.output,
        history_file = args.history_file,
        gateway_url  = args.gateway,
        save_history = not args.no_save,
    )
