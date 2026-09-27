# SentinelOps - Microservices Architecture & Monitoring

SentinelOps is a microservices health monitoring and anomaly-detection platform.
- **Phase 1**: FastAPI REST APIs for User, Order, and Payment services with API Gateway proxying and Docker Compose orchestration.
- **Phase 2**: Microservice Monitoring with Prometheus-compatible metrics collection across all services and automated Prometheus scraping.
- **Phase 3**: Controlled Failure Injection — seven safe, manually-triggerable chaos scenarios with automatic recovery, history tracking, and Prometheus observability.

---

## 1. Services Architecture & Port Mapping

| Service | Port | Description | Key Endpoints |
| :--- | :--- | :--- | :--- |
| **API Gateway** | `8000` | Entry point for external requests; proxies traffic downstream. | `GET /health`<br>`/api/users` → User Service<br>`/api/orders` → Order Service<br>`/api/payments` → Payment Service |
| **User Service** | `8001` | Manages user profiles. | `GET /health`<br>`GET /metrics`<br>`GET /users`<br>`GET /users/{user_id}` |
| **Order Service** | `8002` | Manages orders; communicates with Payment Service. | `GET /health`<br>`GET /metrics`<br>`GET /orders`<br>`POST /orders` |
| **Payment Service** | `8003` | Processes payment transactions and authorizations. | `GET /health`<br>`GET /metrics`<br>`POST /payments` |
| **Prometheus** | `9090` | Time-series metrics collection and query engine. | `GET /targets`<br>`GET /graph`<br>`GET /api/v1/query` |

---

## 2. Microservice Monitoring (Phase 2)

Each microservice exports Prometheus-compatible metrics via its `/metrics` endpoint on its dedicated port.

### Collected Metrics

| Metric Requirement | Prometheus Metric Name | Type | Description |
| :--- | :--- | :--- | :--- |
| **CPU Usage** | `sentinel_cpu_usage_percent` | Gauge | Current process CPU usage percentage (0-100%) |
| **Memory Usage** | `sentinel_memory_usage_bytes`<br>`sentinel_memory_usage_percent` | Gauge<br>Gauge | Process Resident Set Size (RSS) memory in bytes and memory percentage |
| **Request Count** | `sentinel_request_count_total`<br>`sentinel_requests_total` | Counter | Total incoming HTTP requests labeled by `service`, `method`, `endpoint`, and `status_code` |
| **Request Rate** | `sentinel_request_rate`<br>`sentinel_request_rate_per_second` | Gauge | Instantaneous request rate (requests/sec over a 60s sliding window) |
| **Response Latency** | `sentinel_response_latency_seconds` | Histogram | Request-response latency distribution in seconds labeled by `service`, `method`, and `endpoint` |
| **Error Count** | `sentinel_error_count_total`<br>`sentinel_errors_total` | Counter | Total HTTP error responses (HTTP status $\ge$ 400) labeled by `service`, `method`, `endpoint`, and `status_code` |
| **Error Rate** | `sentinel_error_rate`<br>`sentinel_error_rate_percent` | Gauge | Error rate percentage (0-100% over a 60s sliding window) |
| **Service Availability** | `sentinel_service_availability` | Gauge | Health availability indicator (`1.0` = healthy/up, `0.0` = unavailable) |

---

## 3. How to Start the System

### Using Docker Compose (Recommended)

From the project root directory:

```bash
# 1. Build and start all microservices and Prometheus in the background
docker compose up --build -d

# 2. Verify all 5 containers are running
docker compose ps

# 3. View live logs from all services (or specify a service like 'prometheus')
docker compose logs -f

# 4. Stop all services when finished
docker compose down
```

### Running Locally (Without Docker)

If developing locally outside Docker, install requirements and start each service:

```bash
# Terminal 1 - User Service
cd services/user-service
pip install -r requirements.txt
uvicorn app.main:app --port 8001 --reload

# Terminal 2 - Payment Service
cd services/payment-service
pip install -r requirements.txt
uvicorn app.main:app --port 8003 --reload

# Terminal 3 - Order Service
cd services/order-service
pip install -r requirements.txt
PAYMENT_SERVICE_URL=http://localhost:8003 uvicorn app.main:app --port 8002 --reload

# Terminal 4 - API Gateway
cd gateway
pip install -r requirements.txt
USER_SERVICE_URL=http://localhost:8001 ORDER_SERVICE_URL=http://localhost:8002 PAYMENT_SERVICE_URL=http://localhost:8003 uvicorn app.main:app --port 8000 --reload

# Terminal 5 - Prometheus
prometheus --config.file=monitoring/prometheus.yml
```

---

## 4. How to Verify Health & Metrics

### A. Health Endpoints (`/health`)

Verify that all services report healthy:
- **API Gateway**: `curl http://localhost:8000/health`
- **User Service**: `curl http://localhost:8001/health`
- **Order Service**: `curl http://localhost:8002/health`
- **Payment Service**: `curl http://localhost:8003/health`

Expected response:
```json
{"service": "<service-name>", "status": "healthy"}
```

---

### B. Metrics Endpoints (`/metrics`)

Inspect the raw Prometheus text exposition metrics directly from each microservice:
- **User Service Metrics**: `curl http://localhost:8001/metrics`
- **Order Service Metrics**: `curl http://localhost:8002/metrics`
- **Payment Service Metrics**: `curl http://localhost:8003/metrics`

Each endpoint exposes the CPU, memory, request count, request rate, latency, error count, error rate, and availability metrics.

---

### C. Verify Prometheus Scrapes All Three Microservices

1. Open the Prometheus Web UI in your browser:
   **[http://localhost:9090](http://localhost:9090)**

2. Navigate to **Status $\rightarrow$ Targets** (or open [http://localhost:9090/targets](http://localhost:9090/targets)).
   You should see all targets with State **UP**:
   - `user-service` (`http://user-service:8001/metrics`) &rarr; **UP**
   - `order-service` (`http://order-service:8002/metrics`) &rarr; **UP**
   - `payment-service` (`http://payment-service:8003/metrics`) &rarr; **UP**
   - `prometheus` (`http://localhost:9090/metrics`) &rarr; **UP**

---

### D. Verify Metrics via PromQL Queries

Enter these queries in the Prometheus Expression Browser ([http://localhost:9090/graph](http://localhost:9090/graph)) or query via HTTP:

1. **Service Availability**:
   ```promql
   sentinel_service_availability
   ```
   *(Returns `1` for all active services)*

2. **CPU Usage (%)**:
   ```promql
   sentinel_cpu_usage_percent
   ```

3. **Memory Usage (in Megabytes)**:
   ```promql
   sentinel_memory_usage_bytes / 1024 / 1024
   ```

4. **Total Request Count**:
   ```promql
   sum by (service, endpoint, status_code) (sentinel_request_count_total)
   ```

5. **Request Rate (req/sec)**:
   ```promql
   sentinel_request_rate
   ```
   *Or calculated over time:*
   ```promql
   rate(sentinel_requests_total[1m])
   ```

6. **Response Latency (95th percentile)**:
   ```promql
   histogram_quantile(0.95, sum by (le, service) (rate(sentinel_response_latency_seconds_bucket[1m])))
   ```

7. **Error Count**:
   ```promql
   sentinel_error_count_total
   ```

8. **Error Rate (%)**:
   ```promql
   sentinel_error_rate
   ```
   *Or calculated over time:*
   ```promql
   sum(rate(sentinel_errors_total[1m])) / sum(rate(sentinel_requests_total[1m])) * 100
   ```

---

## 5. End-to-End Functional Test of REST APIs

Test the REST APIs through the API Gateway (port `8000`) and observe Prometheus metrics update:

```bash
# 1. Fetch users (User Service)
curl -X GET http://localhost:8000/api/users

# 2. Fetch specific user (User Service)
curl -X GET http://localhost:8000/api/users/1

# 3. Create an order (Triggers Order Service -> Payment Service communication)
curl -X POST http://localhost:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": ["Laptop", "Mouse"], "total_amount": 1299.99, "payment_method": "credit_card"}'

# 4. Fetch orders (Order Service)
curl -X GET http://localhost:8000/api/orders

# 5. Direct payment (Payment Service)
curl -X POST http://localhost:8000/api/payments \
  -H "Content-Type: application/json" \
  -d '{"order_id": 100, "amount": 99.50, "payment_method": "credit_card"}'

# 6. Trigger a 404 error to test error tracking
curl -X GET http://localhost:8000/api/users/999
```

After running these commands, refresh the Prometheus query for `sentinel_requests_total` and `sentinel_error_count_total` to see the live metrics collected across all three services.

---

## 6. Phase 3: Controlled Failure Injection (Chaos Engineering)

Each service has a `ChaosManager` that provides **seven safe, manually-triggerable failure simulations**. All chaos activity is:
- **Stoppable** — every scenario has a dedicated `/stop` endpoint.
- **Auto-resetting** — pass `duration_seconds` for automatic cleanup.
- **Recorded** — every trigger/stop is timestamped in an in-memory history log.
- **Non-destructive** — no container exits, no persistent data loss; Prometheus keeps scraping throughout.

### Architecture

```
API Gateway (:8000)              Microservices (:8001/8002/8003)
┌──────────────────────┐         ┌──────────────────────────────────┐
│ /api/chaos/users/*   │──────▶  │ ChaosMiddleware (request-layer)  │
│ /api/chaos/orders/*  │         │   ↳ 503 if unavailability_active │
│ /api/chaos/payments/*│         │   ↳ asyncio.sleep if latency     │
│                      │         │   ↳ random 4xx/5xx if error_rate │
│ /api/chaos/status    │◀──────  │ ChaosManager (state + history)   │
│ /api/chaos/history   │         │   ↳ CpuStressWorker (threads)    │
│ /api/chaos/reset     │         │   ↳ bytearray memory blocks      │
└──────────────────────┘         │   ↳ asyncio traffic tasks        │
                                 └──────────────────────────────────┘
```

### Chaos API Reference

Every endpoint below is available on **each service directly** or via the **API Gateway** proxy.

| Direct (per service)             | Via Gateway (`/api/chaos/<service>/<path>`)  |
| :------------------------------- | :------------------------------------------- |
| `POST http://localhost:8001/chaos/*` | `POST http://localhost:8000/api/chaos/users/*` |
| `POST http://localhost:8002/chaos/*` | `POST http://localhost:8000/api/chaos/orders/*` |
| `POST http://localhost:8003/chaos/*` | `POST http://localhost:8000/api/chaos/payments/*` |

| Endpoint | Method | Body (JSON) | Description |
| :--- | :--- | :--- | :--- |
| `/chaos/cpu` | `POST` | `{"duration_seconds":30,"load_percentage":85.0}` | Start CPU stress workers |
| `/chaos/cpu/stop` | `POST` | — | Stop CPU stress immediately |
| `/chaos/memory` | `POST` | `{"mb_to_allocate":150,"duration_seconds":30}` | Allocate large memory blocks |
| `/chaos/memory/stop` | `POST` | — | Release memory blocks (GC collect) |
| `/chaos/latency` | `POST` | `{"delay_seconds":2.0,"probability":1.0,"duration_seconds":60}` | Inject response delay |
| `/chaos/latency/stop` | `POST` | — | Remove latency injection |
| `/chaos/error` | `POST` | `{"error_rate":0.5,"status_code":500,"message":"...","duration_seconds":60}` | Inject random HTTP errors |
| `/chaos/error/stop` | `POST` | — | Stop error injection |
| `/chaos/unavailability` | `POST` | `{"duration_seconds":60}` | Return 503 on all non-chaos requests |
| `/chaos/unavailability/stop` | `POST` | — | Restore service to healthy |
| `/chaos/dependency` | `POST` | `{"dependency_name":"bank-gateway","failure_mode":"timeout","duration_seconds":60}` | Simulate downstream failure |
| `/chaos/dependency/stop` | `POST` | — | Restore dependency |
| `/chaos/traffic-spike` | `POST` | `{"requests_per_second":15,"duration_seconds":10,"target_path":"/users"}` | Generate synthetic load |
| `/chaos/traffic-spike/stop` | `POST` | — | Cancel traffic task |
| `/chaos/stop` | `POST` | — | Reset all failures for that service |
| `/chaos/status` | `GET` | — | Current active failures & state booleans |
| `/chaos/history` | `GET` | — | Full history of all chaos records |

**Consolidated (across all three services via Gateway):**

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `GET  /api/chaos/status` | `GET` | Aggregated status from all three services |
| `GET  /api/chaos/history` | `GET` | Merged + sorted history from all three services |
| `POST /api/chaos/reset`  | `POST` | Reset all failures on all three services at once |

### Dependency Failure Modes

| `failure_mode` value | HTTP status returned to caller |
| :--- | :--- |
| `timeout` | `504 Gateway Timeout` |
| `error_503` | `503 Service Unavailable` |
| `connection_error` | `502 Bad Gateway` |

---

### How to Test Each Failure Scenario

Replace `<PORT>` with `8001` (User), `8002` (Order), or `8003` (Payment) in the examples below.
All examples also work via the Gateway — substitute `http://localhost:8000/api/chaos/<service>/<path>`.

#### 1. CPU Spike

```bash
# Trigger: 70% CPU load on User Service for 20 seconds
curl -X POST http://localhost:8001/chaos/cpu \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 20, "load_percentage": 70.0}'

# Verify: watch CPU in Prometheus
# PromQL: sentinel_cpu_usage_percent{service="user-service"}

# Stop manually (optional — auto-stops after duration)
curl -X POST http://localhost:8001/chaos/cpu/stop
```

#### 2. Memory Growth

```bash
# Trigger: allocate 200 MB in Order Service for 30 seconds
curl -X POST http://localhost:8002/chaos/memory \
  -H "Content-Type: application/json" \
  -d '{"mb_to_allocate": 200, "duration_seconds": 30}'

# Verify in Prometheus:
# PromQL: sentinel_memory_usage_bytes{service="order-service"} / 1024 / 1024

# Stop manually
curl -X POST http://localhost:8002/chaos/memory/stop
```

#### 3. Increased Response Latency

```bash
# Trigger: inject 3-second delay on ALL requests to Payment Service
curl -X POST http://localhost:8003/chaos/latency \
  -H "Content-Type: application/json" \
  -d '{"delay_seconds": 3.0, "probability": 1.0, "duration_seconds": 60}'

# Verify: time a health check — it should take ~3 seconds
curl -w "\nTotal time: %{time_total}s\n" http://localhost:8003/health

# Verify in Prometheus (95th percentile latency):
# PromQL: histogram_quantile(0.95, sum by(le, service) (rate(sentinel_response_latency_seconds_bucket[1m])))

# Stop
curl -X POST http://localhost:8003/chaos/latency/stop
```

#### 4. Increased Error Rate

```bash
# Trigger: 50% of requests to User Service return HTTP 500
curl -X POST http://localhost:8001/chaos/error \
  -H "Content-Type: application/json" \
  -d '{"error_rate": 0.5, "status_code": 500, "message": "Simulated internal error", "duration_seconds": 60}'

# Verify: call the API several times — ~half should fail
curl http://localhost:8001/users   # may return 500
curl http://localhost:8001/users   # may return 200

# Verify in Prometheus:
# PromQL: sentinel_error_rate{service="user-service"}

# Stop
curl -X POST http://localhost:8001/chaos/error/stop
```

#### 5. Service Unavailability

```bash
# Trigger: Order Service returns 503 on all non-chaos endpoints
curl -X POST http://localhost:8002/chaos/unavailability \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 30}'

# Verify: health check should return 503
curl -i http://localhost:8002/health
# HTTP/1.1 503 Service Unavailable

# Verify in Prometheus:
# PromQL: sentinel_service_availability{service="order-service"}  → should be 0

# Restore
curl -X POST http://localhost:8002/chaos/unavailability/stop

# Verify recovery
curl http://localhost:8002/health
# {"service":"order-service","status":"healthy"}
```

#### 6. Dependency Failure

```bash
# Trigger: Payment Service simulates its payment gateway timing out
curl -X POST http://localhost:8003/chaos/dependency \
  -H "Content-Type: application/json" \
  -d '{"dependency_name": "bank-gateway", "failure_mode": "timeout", "duration_seconds": 30}'

# Verify: creating a payment should fail with 504
curl -X POST http://localhost:8003/payments \
  -H "Content-Type: application/json" \
  -d '{"order_id": 1, "amount": 99.99, "payment_method": "credit_card"}'
# {"detail":"Dependency failure: Timeout connecting to payment gateway bank-gateway"}

# Try other failure modes:
# "error_503" → 503, "connection_error" → 502

# Stop
curl -X POST http://localhost:8003/chaos/dependency/stop
```

#### 7. Traffic Spike

```bash
# Trigger: send 20 req/s to User Service /users for 10 seconds
curl -X POST http://localhost:8001/chaos/traffic-spike \
  -H "Content-Type: application/json" \
  -d '{"requests_per_second": 20, "duration_seconds": 10, "target_path": "/users"}'

# Verify in Prometheus (watch request rate spike):
# PromQL: sentinel_request_rate{service="user-service"}
# PromQL: rate(sentinel_requests_total{service="user-service"}[30s])

# Stop early (optional)
curl -X POST http://localhost:8001/chaos/traffic-spike/stop
```

---

### View Status, History & Reset

```bash
# Status of a single service
curl http://localhost:8001/chaos/status | python -m json.tool

# Consolidated status across all three services (via Gateway)
curl http://localhost:8000/api/chaos/status | python -m json.tool

# Full chaos history (all records, sorted newest first)
curl http://localhost:8000/api/chaos/history | python -m json.tool

# Reset all failures on all services at once
curl -X POST http://localhost:8000/api/chaos/reset
```

### Example History Record

```json
{
  "id": "chaos_a3f12b9c",
  "service": "user-service",
  "failure_type": "cpu_spike",
  "parameters": {
    "duration_seconds": 20,
    "load_percentage": 70.0,
    "workers": 4
  },
  "start_timestamp": "2026-09-27T07:10:00.000000+00:00",
  "end_timestamp": "2026-09-27T07:10:20.412000+00:00",
  "duration_seconds": 20.41,
  "status": "stopped"
}
```

### Observing Chaos in Prometheus

During and after failure injection, query these metrics in the Prometheus UI at **[http://localhost:9090](http://localhost:9090)**:

| Failure Type | PromQL to Watch |
| :--- | :--- |
| CPU Spike | `sentinel_cpu_usage_percent` |
| Memory Growth | `sentinel_memory_usage_bytes / 1024 / 1024` |
| Latency | `histogram_quantile(0.95, sum by(le, service)(rate(sentinel_response_latency_seconds_bucket[1m])))` |
| Error Rate | `sentinel_error_rate` or `rate(sentinel_errors_total[1m])` |
| Unavailability | `sentinel_service_availability` |
| Dependency Failure | `sentinel_error_count_total` (will spike on the affected service) |
| Traffic Spike | `rate(sentinel_requests_total[30s])` or `sentinel_request_rate` |

### Safety Guarantees

| Constraint | How it's enforced |
| :--- | :--- |
| CPU workers capped | Max 4 threads; `load_percentage` capped at 100% |
| Memory capped | `mb_to_allocate` limited to 500 MB per trigger |
| Traffic capped | `requests_per_second` limited to 50; `duration_seconds` limited to 60 |
| Latency capped | `delay_seconds` limited to 10 seconds |
| Auto-recovery | All scenarios accept `duration_seconds` for automatic stop |
| No process kill | "Unavailability" returns 503 — the container stays alive |
| Chaos endpoints bypass middleware | `/metrics` and `/chaos/*` are never blocked by chaos |
