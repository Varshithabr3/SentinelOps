"""
Live integration test for Phase 4A telemetry pipeline.

Steps:
  1. Fire all 7 chaos scenarios across all 3 services (staggered).
  2. Simultaneously collect Prometheus telemetry (130-second window).
  3. Label the raw CSV using chaos ground-truth from the Gateway API.
  4. Inspect the labeled dataset (statistics + class distribution).
"""
import sys
import time
import threading
import requests
from pathlib import Path

# ---- point Python at the project root ----------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ml.telemetry.collector        import collect, DEFAULT_OUTPUT as RAW_CSV
from ml.telemetry.labeler          import label,   DEFAULT_LABELED_CSV as LABELED_CSV
from ml.telemetry.dataset_inspector import inspect

GW = "http://localhost:8000"
U  = "http://localhost:8001"
O  = "http://localhost:8002"
P  = "http://localhost:8003"


def post(url, body=None, label=""):
    try:
        r = requests.post(url, json=body or {}, timeout=10)
        status = "OK" if r.status_code < 400 else "HTTP {}".format(r.status_code)
        print("  [chaos] {:<45} -> {}".format(label, status))
    except Exception as e:
        print("  [chaos] {:<45} -> ERROR: {}".format(label, e))


def chaos_sequence():
    """
    Inject all 7 chaos types with deliberate baseline gaps so the dataset
    contains clear normal AND anomaly windows.
    """
    # 5 s baseline (normal data first)
    time.sleep(5)

    # Scenario 1: CPU spike - User Service
    print("\n[chaos] --- Scenario 1: CPU Spike ---")
    post(U + "/chaos/cpu", {"duration_seconds": 15, "load_percentage": 85.0}, "CPU spike -> user-service")
    time.sleep(18)   # wait for auto-stop + gap

    # Scenario 2: Memory Growth - Order Service
    print("\n[chaos] --- Scenario 2: Memory Growth ---")
    post(O + "/chaos/memory", {"mb_to_allocate": 100, "duration_seconds": 15}, "Memory 100MB -> order-service")
    time.sleep(18)

    # Scenario 3: Latency - Payment Service
    print("\n[chaos] --- Scenario 3: Latency ---")
    post(P + "/chaos/latency", {"delay_seconds": 2.5, "probability": 1.0, "duration_seconds": 15}, "Latency 2.5s -> payment-service")
    time.sleep(18)

    # Scenario 4: Error Rate - User Service
    print("\n[chaos] --- Scenario 4: Error Rate ---")
    post(U + "/chaos/error", {"error_rate": 0.8, "status_code": 500, "message": "Simulated 500", "duration_seconds": 10}, "80% errors -> user-service")
    time.sleep(13)

    # Scenario 5: Service Unavailability - Order Service
    print("\n[chaos] --- Scenario 5: Unavailability ---")
    post(O + "/chaos/unavailability", {"duration_seconds": 10}, "503 outage -> order-service")
    time.sleep(13)

    # Scenario 6: Dependency Failure - Payment Service
    print("\n[chaos] --- Scenario 6: Dependency Failure ---")
    post(P + "/chaos/dependency", {"dependency_name": "bank-gateway", "failure_mode": "error_503", "duration_seconds": 10}, "Dep failure (503) -> payment-service")
    time.sleep(13)

    # Scenario 7: Traffic Spike - User Service
    print("\n[chaos] --- Scenario 7: Traffic Spike ---")
    post(U + "/chaos/traffic-spike", {"requests_per_second": 20, "duration_seconds": 10, "target_path": "/users"}, "Traffic 20rps -> user-service")
    time.sleep(12)

    # All-services reset
    print("\n[chaos] --- Full Reset ---")
    post(GW + "/api/chaos/reset", label="Consolidated reset -> all services")
    print("[chaos] All chaos scenarios complete.")


# ---- Clean slate for this test run -------------------------------------------
if RAW_CSV.exists():
    RAW_CSV.unlink()
    print("[test] Removed existing raw CSV: {}".format(RAW_CSV))

LABELED_CSV.parent.mkdir(parents=True, exist_ok=True)
if LABELED_CSV.exists():
    LABELED_CSV.unlink()
    print("[test] Removed existing labeled CSV: {}".format(LABELED_CSV))

# ---- Launch chaos in a background thread -------------------------------------
chaos_thread = threading.Thread(target=chaos_sequence, daemon=True)
chaos_thread.start()

# ---- Collect 130 seconds of Prometheus telemetry ----------------------------
print("\n[test] Starting telemetry collection (130 s, every 5 s) ...\n")
collect(interval=5, duration=130, output=RAW_CSV, quiet=False)

# ---- Wait for chaos thread to finish ----------------------------------------
chaos_thread.join(timeout=10)

# ---- Label ------------------------------------------------------------------
print("\n[test] Labeling raw CSV ...\n")
summary = label(input_csv=RAW_CSV, output_csv=LABELED_CSV)

# ---- Inspect ----------------------------------------------------------------
print()
inspect(LABELED_CSV, sample_n=5)

print("\n[test] Phase 4A integration test complete.")
print("       Raw CSV     -> {}".format(RAW_CSV))
print("       Labeled CSV -> {}".format(LABELED_CSV))
print("       Rows: {}  |  Anomaly%: {}%".format(summary["total_rows"], summary["anomaly_pct"]))
