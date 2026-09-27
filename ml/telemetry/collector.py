"""
collector.py — SentinelOps Phase 4A Telemetry Collector
========================================================
Queries the Prometheus HTTP API at a configurable interval and writes
one row per (timestamp, service) snapshot to a raw CSV file.

Features collected per row:
  timestamp          ISO-8601 UTC
  service            user-service | order-service | payment-service
  cpu_usage          sentinel_cpu_usage_percent        (%)
  memory_mb          sentinel_memory_usage_bytes / 1e6 (MB)
  memory_pct         sentinel_memory_usage_percent     (%)
  request_rate       sentinel_request_rate             (req/s)
  latency_p95        histogram_quantile(0.95, ...)     (s)
  error_rate         sentinel_error_rate               (%)
  availability       sentinel_service_availability     (0 or 1)

Usage:
    python -m ml.telemetry.collector               # run forever (Ctrl-C to stop)
    python -m ml.telemetry.collector --duration 60 # run for 60 seconds
    python -m ml.telemetry.collector --interval 5  # sample every 5 s
    python -m ml.telemetry.collector --output data/my_run.csv
"""

import argparse
import csv
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import requests

# ── defaults ──────────────────────────────────────────────────────────────────
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
GATEWAY_URL    = os.getenv("GATEWAY_URL",    "http://localhost:8000")
DEFAULT_OUTPUT = Path(__file__).parent.parent / "data" / "telemetry_raw.csv"
SERVICES       = ["user-service", "order-service", "payment-service"]

CSV_COLUMNS = [
    "timestamp",
    "service",
    "cpu_usage",
    "memory_mb",
    "memory_pct",
    "request_rate",
    "latency_p95",
    "error_rate",
    "availability",
]

# ── Prometheus helpers ────────────────────────────────────────────────────────

def _prom_instant(query: str, timeout: float = 5.0) -> Dict[str, float]:
    """
    Run an instant PromQL query. Returns {service_label: float_value}.
    Falls back to {} on any error.
    """
    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        result: Dict[str, float] = {}
        for item in data.get("data", {}).get("result", []):
            svc = item["metric"].get("service", "")
            if svc:
                result[svc] = float(item["value"][1])
        return result
    except Exception as exc:
        print(f"[WARN] Prometheus query failed ({query[:60]}…): {exc}", file=sys.stderr)
        return {}


def _collect_snapshot(ts: str) -> list:
    """
    Query all metrics once and return a list of dicts, one per service.
    """
    # Instant queries
    cpu   = _prom_instant("sentinel_cpu_usage_percent")
    mem_b = _prom_instant("sentinel_memory_usage_bytes")
    mem_p = _prom_instant("sentinel_memory_usage_percent")
    rrate = _prom_instant("sentinel_request_rate")
    erate = _prom_instant("sentinel_error_rate")
    avail = _prom_instant("sentinel_service_availability")

    # p95 latency — histogram_quantile returns {service: value} (no 'service' label, group by service)
    lat_q = (
        "histogram_quantile(0.95,"
        "sum by(le,service)(rate(sentinel_response_latency_seconds_bucket[1m])))"
    )
    lat = _prom_instant(lat_q)

    rows = []
    for svc in SERVICES:
        rows.append({
            "timestamp":    ts,
            "service":      svc,
            "cpu_usage":    round(cpu.get(svc, 0.0), 4),
            "memory_mb":    round(mem_b.get(svc, 0.0) / 1_000_000, 4),
            "memory_pct":   round(mem_p.get(svc, 0.0), 4),
            "request_rate": round(rrate.get(svc, 0.0), 6),
            "latency_p95":  round(lat.get(svc, 0.0), 6),
            "error_rate":   round(erate.get(svc, 0.0), 4),
            "availability": float(avail.get(svc, 1.0)),
        })
    return rows


# ── main collection loop ──────────────────────────────────────────────────────

def collect(
    interval: float = 10.0,
    duration: Optional[float] = None,
    output: Path = DEFAULT_OUTPUT,
    quiet: bool = False,
) -> int:
    """
    Sample Prometheus at *interval* seconds for *duration* seconds (or forever).
    Appends rows to *output* CSV.  Returns the total number of rows written.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    file_exists = output.exists()
    total_written = 0
    stop = False

    def _handle_signal(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    start_wall = time.monotonic()

    with open(output, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()

        print(f"[collector] Writing to: {output}")
        print(f"[collector] Interval: {interval}s  |  Duration: {duration or 'unlimited'}s")
        print("[collector] Ctrl-C or SIGTERM to stop.\n")

        while not stop:
            loop_start = time.monotonic()

            ts = datetime.now(timezone.utc).isoformat()
            rows = _collect_snapshot(ts)
            writer.writerows(rows)
            fh.flush()
            total_written += len(rows)

            if not quiet:
                print(f"[{ts}] Wrote {len(rows)} rows  (total: {total_written})")

            # Check duration limit
            if duration and (time.monotonic() - start_wall) >= duration:
                break

            # Sleep remainder of interval
            elapsed = time.monotonic() - loop_start
            sleep_for = max(0.0, interval - elapsed)
            if sleep_for > 0:
                time.sleep(sleep_for)

    print(f"\n[collector] Done. Total rows written: {total_written}")
    return total_written


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SentinelOps Phase 4A: Prometheus telemetry collector")
    p.add_argument("--interval",  type=float, default=10.0,          help="Seconds between samples (default: 10)")
    p.add_argument("--duration",  type=float, default=None,          help="Total seconds to run (default: unlimited)")
    p.add_argument("--output",    type=Path,  default=DEFAULT_OUTPUT, help="Output CSV path")
    p.add_argument("--prometheus",type=str,   default=PROMETHEUS_URL, help="Prometheus base URL")
    p.add_argument("--quiet",     action="store_true",                help="Suppress per-row log lines")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    PROMETHEUS_URL = args.prometheus
    collect(
        interval=args.interval,
        duration=args.duration,
        output=args.output,
        quiet=args.quiet,
    )
