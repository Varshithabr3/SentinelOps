"""
SentinelOps Telemetry Pipeline
Phase 4A: Telemetry dataset collection for anomaly detection.

Modules:
  collector  - Queries Prometheus and saves raw timestamped snapshots.
  labeler    - Fetches chaos history and applies is_anomaly labels.
  pipeline   - Orchestrates collection + labeling in one command.
  inspect    - Dataset statistics and class-distribution reporting.
"""
