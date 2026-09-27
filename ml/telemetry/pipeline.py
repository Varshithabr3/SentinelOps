"""
pipeline.py — SentinelOps Phase 4A Telemetry Pipeline
======================================================
Orchestrates a full collection + labeling run in one command.

Steps:
  1. Run the collector for --duration seconds (default: 120s).
     During collection the user should trigger chaos scenarios to
     inject labelled anomalies into the dataset.
  2. Run the labeler against the raw CSV (fetches chaos history from
     the API Gateway to build ground-truth labels).
  3. Print a brief dataset summary.

Usage:
    python -m ml.telemetry.pipeline                   # 120 s collection
    python -m ml.telemetry.pipeline --duration 300    # 5 min collection
    python -m ml.telemetry.pipeline --interval 5      # sample every 5 s
    python -m ml.telemetry.pipeline --label-only      # skip collection, just label existing raw CSV
    python -m ml.telemetry.pipeline --collect-only    # collect but don't label
    python -m ml.telemetry.pipeline --fresh           # delete existing raw CSV before collecting
"""

import argparse
import sys
from pathlib import Path

from ml.telemetry.collector import collect, DEFAULT_OUTPUT as DEFAULT_RAW
from ml.telemetry.labeler   import label,   DEFAULT_LABELED_CSV


def run_pipeline(
    duration:      float = 120.0,
    interval:      float = 10.0,
    raw_csv:       Path  = DEFAULT_RAW,
    labeled_csv:   Path  = DEFAULT_LABELED_CSV,
    label_only:    bool  = False,
    collect_only:  bool  = False,
    fresh:         bool  = False,
    quiet:         bool  = False,
):
    raw_csv    = Path(raw_csv)
    labeled_csv = Path(labeled_csv)

    # Optionally wipe existing raw CSV for a clean run
    if fresh and raw_csv.exists():
        raw_csv.unlink()
        print(f"[pipeline] Deleted existing raw CSV: {raw_csv}")

    # ── Step 1: Collect ──────────────────────────────────────────────────────
    if not label_only:
        print("=" * 60)
        print("STEP 1/2  Collecting telemetry from Prometheus …")
        print(f"          Duration : {duration}s  |  Interval : {interval}s")
        print("          TIP: trigger chaos scenarios now to generate anomalies!")
        print("          e.g. curl -X POST http://localhost:8001/chaos/cpu \\")
        print("                    -H 'Content-Type: application/json' \\")
        print("                    -d '{\"duration_seconds\":30,\"load_percentage\":80}'")
        print("=" * 60)
        rows = collect(
            interval=interval,
            duration=duration,
            output=raw_csv,
            quiet=quiet,
        )
        if rows == 0:
            print("[pipeline] No rows collected — exiting.", file=sys.stderr)
            sys.exit(1)
    else:
        print("[pipeline] --label-only: skipping collection step.")
        if not raw_csv.exists():
            print(f"[ERROR] Raw CSV not found: {raw_csv}", file=sys.stderr)
            sys.exit(1)

    # ── Step 2: Label ────────────────────────────────────────────────────────
    if not collect_only:
        print()
        print("=" * 60)
        print("STEP 2/2  Applying ground-truth labels from chaos history …")
        print("=" * 60)
        summary = label(
            input_csv  = raw_csv,
            output_csv = labeled_csv,
        )
        print()
        print("=" * 60)
        print("PIPELINE COMPLETE")
        print(f"  Raw CSV     : {raw_csv}")
        print(f"  Labeled CSV : {labeled_csv}")
        print(f"  Total rows  : {summary['total_rows']}")
        print(f"  Anomaly %   : {summary['anomaly_pct']}%")
        print("=" * 60)
    else:
        print(f"[pipeline] --collect-only: raw CSV saved to {raw_csv}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SentinelOps Phase 4A: Full telemetry pipeline")
    p.add_argument("--duration",     type=float, default=120.0,          help="Collection duration in seconds (default: 120)")
    p.add_argument("--interval",     type=float, default=10.0,           help="Sample interval in seconds (default: 10)")
    p.add_argument("--raw-csv",      type=Path,  default=DEFAULT_RAW,    help="Path for raw telemetry CSV")
    p.add_argument("--labeled-csv",  type=Path,  default=DEFAULT_LABELED_CSV, help="Path for labeled output CSV")
    p.add_argument("--label-only",   action="store_true",                help="Skip collection; only label existing raw CSV")
    p.add_argument("--collect-only", action="store_true",                help="Collect only; skip labeling step")
    p.add_argument("--fresh",        action="store_true",                help="Delete existing raw CSV before collecting")
    p.add_argument("--quiet",        action="store_true",                help="Suppress per-row log lines")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_pipeline(
        duration     = args.duration,
        interval     = args.interval,
        raw_csv      = args.raw_csv,
        labeled_csv  = args.labeled_csv,
        label_only   = args.label_only,
        collect_only = args.collect_only,
        fresh        = args.fresh,
        quiet        = args.quiet,
    )
