"""
inspect.py — SentinelOps Phase 4A Dataset Inspector
====================================================
Loads the labeled telemetry CSV and prints:
  - Basic dataset info (rows, columns, file size)
  - Feature statistics (mean, std, min, max) per metric
  - Class distribution (normal vs anomaly count and %)
  - Class distribution per service
  - Failure-type breakdown
  - Missing value report

Usage:
    python -m ml.telemetry.inspect                          # inspect default labeled CSV
    python -m ml.telemetry.inspect --csv data/my.csv        # custom path
    python -m ml.telemetry.inspect --raw                    # inspect raw (unlabeled) CSV
    python -m ml.telemetry.inspect --sample 10             # print 10 random sample rows
"""

import argparse
import csv
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_LABELED_CSV = Path(__file__).parent.parent / "data" / "telemetry_labeled.csv"
DEFAULT_RAW_CSV     = Path(__file__).parent.parent / "data" / "telemetry_raw.csv"

NUMERIC_FEATURES = [
    "cpu_usage",
    "memory_mb",
    "memory_pct",
    "request_rate",
    "latency_p95",
    "error_rate",
    "availability",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_csv(path: Path) -> List[dict]:
    with open(path, "r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _safe_float(val: str) -> Optional[float]:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _stats(values: List[float]) -> dict:
    if not values:
        return {"count": 0, "mean": "N/A", "std": "N/A", "min": "N/A", "max": "N/A"}
    mean = statistics.mean(values)
    std  = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "count": len(values),
        "mean":  round(mean, 6),
        "std":   round(std,  6),
        "min":   round(min(values), 6),
        "max":   round(max(values), 6),
    }


def _bar(count: int, total: int, width: int = 30) -> str:
    filled = int(round(count / total * width)) if total else 0
    return "#" * filled + "-" * (width - filled)


# ── report sections ───────────────────────────────────────────────────────────

def _section(title: str):
    print("\n" + "=" * 60)
    print("  " + title)
    print("=" * 60)


def report_basic(rows: List[dict], path: Path):
    _section("DATASET OVERVIEW")
    size_kb = os.path.getsize(path) / 1024
    cols = list(rows[0].keys()) if rows else []
    print(f"  File             : {path}")
    print(f"  Size             : {size_kb:.1f} KB")
    print(f"  Total rows       : {len(rows)}")
    print(f"  Total columns    : {len(cols)}")
    print(f"  Columns          : {', '.join(cols)}")
    services = Counter(r.get("service", "") for r in rows)
    print(f"  Services         : {dict(services)}")
    ts_vals = [r.get("timestamp", "") for r in rows if r.get("timestamp")]
    if ts_vals:
        print(f"  Earliest record  : {min(ts_vals)}")
        print(f"  Latest record    : {max(ts_vals)}")


def report_feature_stats(rows: List[dict]):
    _section("FEATURE STATISTICS  (across all services & rows)")
    header = f"  {'Feature':<18} {'Count':>7} {'Mean':>12} {'Std':>12} {'Min':>12} {'Max':>12}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for feat in NUMERIC_FEATURES:
        vals = [_safe_float(r[feat]) for r in rows if feat in r and _safe_float(r[feat]) is not None]
        s = _stats(vals)
        print(
            f"  {feat:<18} {s['count']:>7} {str(s['mean']):>12} "
            f"{str(s['std']):>12} {str(s['min']):>12} {str(s['max']):>12}"
        )


def report_class_distribution(rows: List[dict]):
    _section("CLASS DISTRIBUTION")
    total = len(rows)
    has_labels = any("is_anomaly" in r for r in rows)
    if not has_labels:
        print("  [INFO] No 'is_anomaly' column found — this is an unlabeled raw CSV.")
        return

    anomaly_rows = [r for r in rows if r.get("is_anomaly") == "1"]
    normal_rows  = [r for r in rows if r.get("is_anomaly") == "0"]
    n_anomaly = len(anomaly_rows)
    n_normal  = len(normal_rows)

    print(f"  {'Class':<12} {'Count':>7}  {'%':>6}  Bar")
    print(f"  {'-'*55}")
    print(f"  {'Normal (0)':<12} {n_normal:>7}  {n_normal/total*100:>5.1f}%  {_bar(n_normal, total)}")
    print(f"  {'Anomaly (1)':<12} {n_anomaly:>7}  {n_anomaly/total*100:>5.1f}%  {_bar(n_anomaly, total)}")
    print(f"  {'TOTAL':<12} {total:>7}")


def report_per_service(rows: List[dict]):
    _section("CLASS DISTRIBUTION BY SERVICE")
    has_labels = any("is_anomaly" in r for r in rows)
    if not has_labels:
        print("  [INFO] No labels — showing row counts per service only.")
        counts = Counter(r.get("service", "") for r in rows)
        for svc, cnt in sorted(counts.items()):
            print(f"  {svc:<25} {cnt} rows")
        return

    by_svc: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_svc[r.get("service", "unknown")].append(r)

    print(f"  {'Service':<25} {'Total':>6} {'Normal':>7} {'Anomaly':>8} {'Anomaly%':>9}")
    print(f"  {'-'*60}")
    for svc in sorted(by_svc.keys()):
        svc_rows = by_svc[svc]
        n_a = sum(1 for r in svc_rows if r.get("is_anomaly") == "1")
        n_n = len(svc_rows) - n_a
        pct = n_a / len(svc_rows) * 100 if svc_rows else 0
        print(f"  {svc:<25} {len(svc_rows):>6} {n_n:>7} {n_a:>8} {pct:>8.1f}%")


def report_failure_types(rows: List[dict]):
    _section("FAILURE TYPE BREAKDOWN")
    has_labels = any("failure_type" in r for r in rows)
    if not has_labels:
        print("  [INFO] No 'failure_type' column — this is an unlabeled raw CSV.")
        return

    total = len(rows)
    counts = Counter(r.get("failure_type", "unknown") for r in rows)
    print(f"  {'Failure Type':<35} {'Count':>7}  {'%':>6}  Bar")
    print(f"  {'-'*70}")
    for ft, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        pct = cnt / total * 100
        print(f"  {ft:<35} {cnt:>7}  {pct:>5.1f}%  {_bar(cnt, total, 20)}")


def report_missing(rows: List[dict]):
    _section("MISSING VALUE REPORT")
    if not rows:
        print("  No rows to inspect.")
        return
    cols = list(rows[0].keys())
    any_missing = False
    for col in cols:
        missing = sum(1 for r in rows if not r.get(col, "").strip())
        if missing:
            print(f"  {col:<25} {missing} missing ({missing/len(rows)*100:.1f}%)")
            any_missing = True
    if not any_missing:
        print("  No missing values found. ✓")


def report_sample(rows: List[dict], n: int = 5):
    _section(f"SAMPLE ROWS  (first {n})")
    if not rows:
        print("  No rows.")
        return
    cols = list(rows[0].keys())
    # Print header
    print("  " + " | ".join(f"{c[:14]:<14}" for c in cols))
    print("  " + "-" * (17 * len(cols)))
    import random
    sample = random.sample(rows, min(n, len(rows)))
    for r in sample:
        print("  " + " | ".join(f"{str(r.get(c,''))[:14]:<14}" for c in cols))


# ── main ──────────────────────────────────────────────────────────────────────

def inspect(csv_path: Path = DEFAULT_LABELED_CSV, sample_n: int = 0):
    csv_path = Path(csv_path)
    if not csv_path.exists():
        print(f"[ERROR] File not found: {csv_path}")
        print("  Run the collector first:  python -m ml.telemetry.collector --duration 60")
        print("  Then label:               python -m ml.telemetry.labeler")
        return

    rows = _load_csv(csv_path)
    if not rows:
        print("[ERROR] CSV is empty.")
        return

    report_basic(rows, csv_path)
    report_feature_stats(rows)
    report_class_distribution(rows)
    report_per_service(rows)
    report_failure_types(rows)
    report_missing(rows)
    if sample_n > 0:
        report_sample(rows, sample_n)

    print("\n" + "=" * 60)
    print("  Inspection complete.")
    print("=" * 60 + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SentinelOps Phase 4A: Dataset inspector")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--csv", type=Path, default=None,               help="CSV file to inspect (default: labeled CSV)")
    grp.add_argument("--raw", action="store_true",                   help="Inspect the raw (unlabeled) CSV")
    p.add_argument("--sample", type=int, default=0,                  help="Print N random sample rows")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.raw:
        target = DEFAULT_RAW_CSV
    elif args.csv:
        target = args.csv
    else:
        target = DEFAULT_LABELED_CSV
    inspect(target, sample_n=args.sample)
