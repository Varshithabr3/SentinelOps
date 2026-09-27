import time
import threading
from collections import deque
from typing import Tuple

import psutil
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST


class SlidingWindowMetrics:
    """Sliding window tracking requests and errors over the last N seconds (default: 60s)."""
    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        self.requests = deque()
        self.errors = deque()
        self.lock = threading.Lock()

    def record(self, is_error: bool = False):
        now = time.time()
        with self.lock:
            self.requests.append(now)
            if is_error:
                self.errors.append(now)
            self._prune(now)

    def _prune(self, now: float):
        cutoff = now - self.window_seconds
        while self.requests and self.requests[0] < cutoff:
            self.requests.popleft()
        while self.errors and self.errors[0] < cutoff:
            self.errors.popleft()

    def get_rates(self) -> Tuple[float, float]:
        now = time.time()
        with self.lock:
            self._prune(now)
            req_count = len(self.requests)
            err_count = len(self.errors)
            req_rate = round(req_count / self.window_seconds, 4)
            err_rate = round((err_count / req_count * 100.0), 2) if req_count > 0 else 0.0
            return req_rate, err_rate


class MetricsCollector:
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.chaos_manager = None
        self.registry = CollectorRegistry()
        self.window = SlidingWindowMetrics(window_seconds=60.0)
        self.process = psutil.Process()

        # Prime psutil CPU percent
        try:
            self.process.cpu_percent(interval=None)
        except Exception:
            pass

        # 1. CPU Usage
        self.cpu_usage_percent = Gauge(
            "sentinel_cpu_usage_percent",
            "Current process CPU usage percentage",
            ["service"],
            registry=self.registry
        )
        self.system_cpu_usage_percent = Gauge(
            "sentinel_system_cpu_usage_percent",
            "Overall system CPU usage percentage",
            ["service"],
            registry=self.registry
        )

        # 2. Memory Usage
        self.memory_usage_bytes = Gauge(
            "sentinel_memory_usage_bytes",
            "Current process memory usage in bytes (RSS)",
            ["service"],
            registry=self.registry
        )
        self.memory_usage_percent = Gauge(
            "sentinel_memory_usage_percent",
            "Current process memory usage percentage",
            ["service"],
            registry=self.registry
        )
        self.system_memory_usage_percent = Gauge(
            "sentinel_system_memory_usage_percent",
            "Overall system memory usage percentage",
            ["service"],
            registry=self.registry
        )

        # 3. Request Count
        self.request_count_total = Counter(
            "sentinel_request_count_total",
            "Total count of HTTP requests",
            ["service", "method", "endpoint", "status_code"],
            registry=self.registry
        )
        self.requests_total = Counter(
            "sentinel_requests_total",
            "Total count of HTTP requests",
            ["service", "method", "endpoint", "status_code"],
            registry=self.registry
        )
        self.http_requests_total = Counter(
            "http_requests_total",
            "Total count of HTTP requests",
            ["service", "method", "endpoint", "status_code"],
            registry=self.registry
        )

        # 4. Request Rate
        self.request_rate = Gauge(
            "sentinel_request_rate",
            "Current request rate (requests/sec over 60s sliding window)",
            ["service"],
            registry=self.registry
        )
        self.request_rate_per_second = Gauge(
            "sentinel_request_rate_per_second",
            "Current request rate (requests/sec over 60s sliding window)",
            ["service"],
            registry=self.registry
        )

        # 5. Response Latency
        self.response_latency_seconds = Histogram(
            "sentinel_response_latency_seconds",
            "HTTP response latency in seconds",
            ["service", "method", "endpoint"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["service", "method", "endpoint"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry
        )

        # 6. Error Count
        self.error_count_total = Counter(
            "sentinel_error_count_total",
            "Total count of HTTP errors (status >= 400)",
            ["service", "method", "endpoint", "status_code"],
            registry=self.registry
        )
        self.errors_total = Counter(
            "sentinel_errors_total",
            "Total count of HTTP errors (status >= 400)",
            ["service", "method", "endpoint", "status_code"],
            registry=self.registry
        )
        self.http_errors_total = Counter(
            "http_errors_total",
            "Total count of HTTP errors (status >= 400)",
            ["service", "method", "endpoint", "status_code"],
            registry=self.registry
        )

        # 7. Error Rate
        self.error_rate = Gauge(
            "sentinel_error_rate",
            "Current error rate percentage over 60s sliding window",
            ["service"],
            registry=self.registry
        )
        self.error_rate_percent = Gauge(
            "sentinel_error_rate_percent",
            "Current error rate percentage over 60s sliding window",
            ["service"],
            registry=self.registry
        )

        # 8. Service Availability
        self.service_availability = Gauge(
            "sentinel_service_availability",
            "Service availability status (1.0 = available/healthy, 0.0 = down)",
            ["service"],
            registry=self.registry
        )

        # Initialize gauge labels
        self.service_availability.labels(service=self.service_name).set(1.0)
        self.request_rate.labels(service=self.service_name).set(0.0)
        self.request_rate_per_second.labels(service=self.service_name).set(0.0)
        self.error_rate.labels(service=self.service_name).set(0.0)
        self.error_rate_percent.labels(service=self.service_name).set(0.0)
        self.update_system_metrics()

    def update_system_metrics(self):
        try:
            cpu = self.process.cpu_percent(interval=None)
            mem_info = self.process.memory_info()
            mem_pct = self.process.memory_percent()
            sys_cpu = psutil.cpu_percent(interval=None)
            sys_mem = psutil.virtual_memory().percent

            self.cpu_usage_percent.labels(service=self.service_name).set(cpu)
            self.system_cpu_usage_percent.labels(service=self.service_name).set(sys_cpu)
            self.memory_usage_bytes.labels(service=self.service_name).set(float(mem_info.rss))
            self.memory_usage_percent.labels(service=self.service_name).set(mem_pct)
            self.system_memory_usage_percent.labels(service=self.service_name).set(sys_mem)

            if self.chaos_manager and self.chaos_manager.unavailability_active:
                self.service_availability.labels(service=self.service_name).set(0.0)
            else:
                self.service_availability.labels(service=self.service_name).set(1.0)
        except Exception:
            pass

        req_rate, err_rate = self.window.get_rates()
        self.request_rate.labels(service=self.service_name).set(req_rate)
        self.request_rate_per_second.labels(service=self.service_name).set(req_rate)
        self.error_rate.labels(service=self.service_name).set(err_rate)
        self.error_rate_percent.labels(service=self.service_name).set(err_rate)

    def record_request(self, method: str, endpoint: str, status_code: int, latency: float):
        str_status = str(status_code)
        is_error = (status_code >= 400)

        # Update sliding window
        self.window.record(is_error=is_error)

        # Update request count counters
        self.request_count_total.labels(
            service=self.service_name,
            method=method,
            endpoint=endpoint,
            status_code=str_status
        ).inc()
        self.requests_total.labels(
            service=self.service_name,
            method=method,
            endpoint=endpoint,
            status_code=str_status
        ).inc()
        self.http_requests_total.labels(
            service=self.service_name,
            method=method,
            endpoint=endpoint,
            status_code=str_status
        ).inc()

        # Update latency histograms
        self.response_latency_seconds.labels(
            service=self.service_name,
            method=method,
            endpoint=endpoint
        ).observe(latency)
        self.http_request_duration_seconds.labels(
            service=self.service_name,
            method=method,
            endpoint=endpoint
        ).observe(latency)

        # Update error count counters if error
        if is_error:
            self.error_count_total.labels(
                service=self.service_name,
                method=method,
                endpoint=endpoint,
                status_code=str_status
            ).inc()
            self.errors_total.labels(
                service=self.service_name,
                method=method,
                endpoint=endpoint,
                status_code=str_status
            ).inc()
            self.http_errors_total.labels(
                service=self.service_name,
                method=method,
                endpoint=endpoint,
                status_code=str_status
            ).inc()


class PrometheusMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, collector: MetricsCollector):
        super().__init__(app)
        self.collector = collector

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        method = request.method
        raw_path = request.url.path

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            status_code = 500
            raise exc
        finally:
            latency = time.time() - start_time
            route = request.scope.get("route")
            endpoint = route.path if route and hasattr(route, "path") else raw_path

            self.collector.record_request(
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                latency=latency
            )


def setup_monitoring(app: FastAPI, service_name: str) -> MetricsCollector:
    collector = MetricsCollector(service_name=service_name)
    app.add_middleware(PrometheusMiddleware, collector=collector)

    @app.get("/metrics")
    async def metrics():
        collector.update_system_metrics()
        return Response(
            content=generate_latest(collector.registry),
            media_type=CONTENT_TYPE_LATEST
        )

    return collector
