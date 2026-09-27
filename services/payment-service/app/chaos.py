import asyncio
import gc
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


class ChaosRecord(BaseModel):
    id: str
    service: str
    failure_type: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    start_timestamp: str
    end_timestamp: Optional[str] = None
    duration_seconds: Optional[float] = None
    status: str = "active"  # "active" | "stopped"


# Request models
class CpuSpikeRequest(BaseModel):
    duration_seconds: int = Field(default=30, ge=1, le=300, description="Duration in seconds")
    load_percentage: float = Field(default=85.0, ge=10.0, le=100.0, description="Target CPU load %")


class MemoryGrowthRequest(BaseModel):
    mb_to_allocate: int = Field(default=150, ge=10, le=500, description="MB to allocate (safe cap 500MB)")
    duration_seconds: Optional[int] = Field(default=30, ge=1, le=300, description="Optional auto-stop duration")


class LatencyRequest(BaseModel):
    delay_seconds: float = Field(default=2.0, ge=0.1, le=10.0, description="Injected delay in seconds")
    probability: float = Field(default=1.0, ge=0.01, le=1.0, description="Probability (0.0 to 1.0)")
    duration_seconds: Optional[int] = Field(default=60, ge=1, le=300, description="Optional auto-stop duration")


class ErrorRateRequest(BaseModel):
    error_rate: float = Field(default=0.5, ge=0.01, le=1.0, description="Error probability (0.0 to 1.0)")
    status_code: int = Field(default=500, ge=400, le=599, description="HTTP status code to return")
    message: str = Field(default="Simulated internal server error", description="Error message")
    duration_seconds: Optional[int] = Field(default=60, ge=1, le=300, description="Optional auto-stop duration")


class UnavailabilityRequest(BaseModel):
    duration_seconds: Optional[int] = Field(default=60, ge=1, le=300, description="Optional auto-stop duration")


class DependencyFailureRequest(BaseModel):
    dependency_name: str = Field(default="bank-gateway", description="Target dependency name")
    failure_mode: str = Field(default="timeout", description="timeout | error_503 | connection_error")
    duration_seconds: Optional[int] = Field(default=60, ge=1, le=300, description="Optional auto-stop duration")


class TrafficSpikeRequest(BaseModel):
    requests_per_second: int = Field(default=15, ge=1, le=50, description="Requests per second")
    duration_seconds: int = Field(default=10, ge=1, le=60, description="Duration of spike in seconds")
    target_path: Optional[str] = Field(default=None, description="Path to hit, e.g. /payments")


class CpuStressWorker(threading.Thread):
    def __init__(self, stop_event: threading.Event, load_pct: float = 85.0):
        super().__init__(daemon=True)
        self.stop_event = stop_event
        self.load_ratio = max(0.1, min(1.0, load_pct / 100.0))

    def run(self):
        slice_duration = 0.05
        busy_duration = slice_duration * self.load_ratio
        sleep_duration = slice_duration * (1.0 - self.load_ratio)
        while not self.stop_event.is_set():
            start = time.perf_counter()
            while (time.perf_counter() - start) < busy_duration:
                _ = 1234567.89 * 987654.321 / 3.14159
            if sleep_duration > 0.001:
                time.sleep(sleep_duration)


class ChaosManager:
    def __init__(self, service_name: str, port: int):
        self.service_name = service_name
        self.port = port
        self.lock = threading.Lock()

        # Failure states
        self.cpu_workers: List[CpuStressWorker] = []
        self.cpu_stop_event: Optional[threading.Event] = None

        self.memory_blocks: List[bytearray] = []

        self.latency_active = False
        self.latency_delay = 0.0
        self.latency_prob = 1.0

        self.error_active = False
        self.error_rate = 0.0
        self.error_status = 500
        self.error_message = ""

        self.unavailability_active = False

        self.dependency_active = False
        self.dependency_target = "bank-gateway"
        self.dependency_mode = "timeout"

        self.traffic_task: Optional[asyncio.Task] = None
        self.traffic_stop_event = asyncio.Event()

        # History and active tracking
        self.active_records: Dict[str, ChaosRecord] = {}
        self.history: List[ChaosRecord] = []

    def _start_record(self, failure_type: str, parameters: Dict[str, Any]) -> ChaosRecord:
        with self.lock:
            if failure_type in self.active_records:
                self._stop_record(failure_type)

            record = ChaosRecord(
                id=f"chaos_{uuid.uuid4().hex[:8]}",
                service=self.service_name,
                failure_type=failure_type,
                parameters=parameters,
                start_timestamp=datetime.now(timezone.utc).isoformat(),
                status="active"
            )
            self.active_records[failure_type] = record
            self.history.append(record)
            return record

    def _stop_record(self, failure_type: str) -> Optional[ChaosRecord]:
        with self.lock:
            record = self.active_records.pop(failure_type, None)
            if record:
                record.end_timestamp = datetime.now(timezone.utc).isoformat()
                start_dt = datetime.fromisoformat(record.start_timestamp)
                end_dt = datetime.fromisoformat(record.end_timestamp)
                record.duration_seconds = round((end_dt - start_dt).total_seconds(), 2)
                record.status = "stopped"
            return record

    # 1. CPU Spike
    def trigger_cpu_spike(self, duration_seconds: int = 30, load_percentage: float = 85.0) -> ChaosRecord:
        self.stop_cpu_spike()

        self.cpu_stop_event = threading.Event()
        worker_count = max(1, min(4, os.cpu_count() or 1))
        self.cpu_workers = [
            CpuStressWorker(self.cpu_stop_event, load_percentage) for _ in range(worker_count)
        ]
        for w in self.cpu_workers:
            w.start()

        record = self._start_record("cpu_spike", {
            "duration_seconds": duration_seconds,
            "load_percentage": load_percentage,
            "workers": worker_count
        })

        if duration_seconds > 0:
            def auto_stop():
                time.sleep(duration_seconds)
                self.stop_cpu_spike()
            threading.Thread(target=auto_stop, daemon=True).start()

        return record

    def stop_cpu_spike(self) -> Optional[ChaosRecord]:
        if self.cpu_stop_event:
            self.cpu_stop_event.set()
        for w in self.cpu_workers:
            w.join(timeout=0.2)
        self.cpu_workers = []
        self.cpu_stop_event = None
        return self._stop_record("cpu_spike")

    # 2. Memory Growth
    def trigger_memory_growth(self, mb_to_allocate: int = 150, duration_seconds: Optional[int] = 30) -> ChaosRecord:
        self.stop_memory_growth()

        chunk_size = 1024 * 1024
        sample_chunk = b"X" * chunk_size
        for _ in range(mb_to_allocate):
            self.memory_blocks.append(bytearray(sample_chunk))

        record = self._start_record("memory_growth", {
            "mb_allocated": mb_to_allocate,
            "duration_seconds": duration_seconds
        })

        if duration_seconds and duration_seconds > 0:
            def auto_stop():
                time.sleep(duration_seconds)
                self.stop_memory_growth()
            threading.Thread(target=auto_stop, daemon=True).start()

        return record

    def stop_memory_growth(self) -> Optional[ChaosRecord]:
        self.memory_blocks.clear()
        gc.collect()
        return self._stop_record("memory_growth")

    # 3. Increased Response Latency
    def trigger_latency(self, delay_seconds: float = 2.0, probability: float = 1.0, duration_seconds: Optional[int] = 60) -> ChaosRecord:
        self.latency_active = True
        self.latency_delay = delay_seconds
        self.latency_prob = probability

        record = self._start_record("latency", {
            "delay_seconds": delay_seconds,
            "probability": probability,
            "duration_seconds": duration_seconds
        })

        if duration_seconds and duration_seconds > 0:
            def auto_stop():
                time.sleep(duration_seconds)
                self.stop_latency()
            threading.Thread(target=auto_stop, daemon=True).start()

        return record

    def stop_latency(self) -> Optional[ChaosRecord]:
        self.latency_active = False
        self.latency_delay = 0.0
        self.latency_prob = 1.0
        return self._stop_record("latency")

    # 4. Increased Error Rate
    def trigger_error_rate(self, error_rate: float = 0.5, status_code: int = 500, message: str = "Simulated internal server error", duration_seconds: Optional[int] = 60) -> ChaosRecord:
        self.error_active = True
        self.error_rate = error_rate
        self.error_status = status_code
        self.error_message = message

        record = self._start_record("error_rate", {
            "error_rate": error_rate,
            "status_code": status_code,
            "message": message,
            "duration_seconds": duration_seconds
        })

        if duration_seconds and duration_seconds > 0:
            def auto_stop():
                time.sleep(duration_seconds)
                self.stop_error_rate()
            threading.Thread(target=auto_stop, daemon=True).start()

        return record

    def stop_error_rate(self) -> Optional[ChaosRecord]:
        self.error_active = False
        self.error_rate = 0.0
        return self._stop_record("error_rate")

    # 5. Service Unavailability
    def trigger_unavailability(self, duration_seconds: Optional[int] = 60) -> ChaosRecord:
        self.unavailability_active = True

        record = self._start_record("service_unavailability", {
            "duration_seconds": duration_seconds
        })

        if duration_seconds and duration_seconds > 0:
            def auto_stop():
                time.sleep(duration_seconds)
                self.stop_unavailability()
            threading.Thread(target=auto_stop, daemon=True).start()

        return record

    def stop_unavailability(self) -> Optional[ChaosRecord]:
        self.unavailability_active = False
        return self._stop_record("service_unavailability")

    # 6. Dependency Failure
    def trigger_dependency_failure(self, dependency_name: str = "bank-gateway", failure_mode: str = "timeout", duration_seconds: Optional[int] = 60) -> ChaosRecord:
        self.dependency_active = True
        self.dependency_target = dependency_name
        self.dependency_mode = failure_mode

        record = self._start_record("dependency_failure", {
            "dependency_name": dependency_name,
            "failure_mode": failure_mode,
            "duration_seconds": duration_seconds
        })

        if duration_seconds and duration_seconds > 0:
            def auto_stop():
                time.sleep(duration_seconds)
                self.stop_dependency_failure()
            threading.Thread(target=auto_stop, daemon=True).start()

        return record

    def stop_dependency_failure(self) -> Optional[ChaosRecord]:
        self.dependency_active = False
        return self._stop_record("dependency_failure")

    # 7. Traffic Spike
    def trigger_traffic_spike(self, requests_per_second: int = 15, duration_seconds: int = 10, target_path: Optional[str] = None) -> ChaosRecord:
        self.stop_traffic_spike()

        self.traffic_stop_event.clear()
        path = target_path or "/payments"
        url = f"http://localhost:{self.port}{path}"

        record = self._start_record("traffic_spike", {
            "requests_per_second": requests_per_second,
            "duration_seconds": duration_seconds,
            "target_url": url
        })

        async def run_traffic():
            end_time = time.time() + duration_seconds
            async with httpx.AsyncClient(timeout=5.0) as client:
                while not self.traffic_stop_event.is_set() and time.time() < end_time:
                    batch_start = time.time()
                    tasks = [
                        client.post(url, json={"order_id": 9999, "amount": 10.0, "payment_method": "credit_card"})
                        for _ in range(requests_per_second)
                    ]
                    try:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    except Exception:
                        pass
                    elapsed = time.time() - batch_start
                    remainder = 1.0 - elapsed
                    if remainder > 0:
                        await asyncio.sleep(remainder)
            self.stop_traffic_spike()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self.traffic_task = loop.create_task(run_traffic())
            else:
                threading.Thread(target=lambda: asyncio.run(run_traffic()), daemon=True).start()
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(run_traffic()), daemon=True).start()

        return record

    def stop_traffic_spike(self) -> Optional[ChaosRecord]:
        self.traffic_stop_event.set()
        if self.traffic_task and not self.traffic_task.done():
            self.traffic_task.cancel()
        self.traffic_task = None
        return self._stop_record("traffic_spike")

    # Global Stop / Reset
    def reset_all(self) -> List[ChaosRecord]:
        stopped = []
        for stop_fn in [
            self.stop_cpu_spike,
            self.stop_memory_growth,
            self.stop_latency,
            self.stop_error_rate,
            self.stop_unavailability,
            self.stop_dependency_failure,
            self.stop_traffic_spike,
        ]:
            rec = stop_fn()
            if rec:
                stopped.append(rec)
        return stopped

    def get_status(self) -> Dict[str, Any]:
        return {
            "service": self.service_name,
            "active_failures_count": len(self.active_records),
            "active_failures": list(self.active_records.values()),
            "states": {
                "cpu_spike": len(self.cpu_workers) > 0,
                "memory_growth": len(self.memory_blocks) > 0,
                "latency": self.latency_active,
                "error_rate": self.error_active,
                "unavailability": self.unavailability_active,
                "dependency_failure": self.dependency_active,
                "traffic_spike": self.traffic_task is not None and not self.traffic_task.done()
            }
        }

    def get_history(self) -> List[ChaosRecord]:
        return self.history


class ChaosMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, chaos_manager: ChaosManager):
        super().__init__(app)
        self.chaos_manager = chaos_manager

    def is_bypassed(self, path: str) -> bool:
        return path == "/metrics" or path.startswith("/chaos")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not self.is_bypassed(path):
            # 1. Check Service Unavailability
            if self.chaos_manager.unavailability_active:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "Service unavailable (simulated outage)",
                        "service": self.chaos_manager.service_name,
                        "status": "unavailable"
                    }
                )

            # 2. Check Latency Injection
            if self.chaos_manager.latency_active:
                if random.random() < self.chaos_manager.latency_prob:
                    await asyncio.sleep(self.chaos_manager.latency_delay)

            # 3. Check Error Rate Injection
            if self.chaos_manager.error_active:
                if random.random() < self.chaos_manager.error_rate:
                    return JSONResponse(
                        status_code=self.chaos_manager.error_status,
                        content={
                            "error": self.chaos_manager.error_message,
                            "chaos_simulated": True,
                            "service": self.chaos_manager.service_name
                        }
                    )

        return await call_next(request)


def setup_chaos_routes(app: FastAPI, chaos_manager: ChaosManager) -> APIRouter:
    router = APIRouter(prefix="/chaos", tags=["Chaos Engineering"])

    # 1. CPU Spike
    @router.post("/cpu")
    async def trigger_cpu(req: CpuSpikeRequest = CpuSpikeRequest()):
        record = chaos_manager.trigger_cpu_spike(
            duration_seconds=req.duration_seconds,
            load_percentage=req.load_percentage
        )
        return {"status": "triggered", "record": record}

    @router.post("/cpu/stop")
    async def stop_cpu():
        record = chaos_manager.stop_cpu_spike()
        return {"status": "stopped", "record": record}

    # 2. Memory Growth
    @router.post("/memory")
    async def trigger_memory(req: MemoryGrowthRequest = MemoryGrowthRequest()):
        record = chaos_manager.trigger_memory_growth(
            mb_to_allocate=req.mb_to_allocate,
            duration_seconds=req.duration_seconds
        )
        return {"status": "triggered", "record": record}

    @router.post("/memory/stop")
    async def stop_memory():
        record = chaos_manager.stop_memory_growth()
        return {"status": "stopped", "record": record}

    # 3. Increased Response Latency
    @router.post("/latency")
    async def trigger_latency_route(req: LatencyRequest = LatencyRequest()):
        record = chaos_manager.trigger_latency(
            delay_seconds=req.delay_seconds,
            probability=req.probability,
            duration_seconds=req.duration_seconds
        )
        return {"status": "triggered", "record": record}

    @router.post("/latency/stop")
    async def stop_latency_route():
        record = chaos_manager.stop_latency()
        return {"status": "stopped", "record": record}

    # 4. Increased Error Rate
    @router.post("/error")
    async def trigger_error_route(req: ErrorRateRequest = ErrorRateRequest()):
        record = chaos_manager.trigger_error_rate(
            error_rate=req.error_rate,
            status_code=req.status_code,
            message=req.message,
            duration_seconds=req.duration_seconds
        )
        return {"status": "triggered", "record": record}

    @router.post("/error/stop")
    async def stop_error_route():
        record = chaos_manager.stop_error_rate()
        return {"status": "stopped", "record": record}

    # 5. Service Unavailability
    @router.post("/unavailability")
    async def trigger_unavailability_route(req: UnavailabilityRequest = UnavailabilityRequest()):
        record = chaos_manager.trigger_unavailability(duration_seconds=req.duration_seconds)
        return {"status": "triggered", "record": record}

    @router.post("/unavailability/stop")
    async def stop_unavailability_route():
        record = chaos_manager.stop_unavailability()
        return {"status": "stopped", "record": record}

    # 6. Dependency Failure
    @router.post("/dependency")
    async def trigger_dependency_route(req: DependencyFailureRequest = DependencyFailureRequest()):
        record = chaos_manager.trigger_dependency_failure(
            dependency_name=req.dependency_name,
            failure_mode=req.failure_mode,
            duration_seconds=req.duration_seconds
        )
        return {"status": "triggered", "record": record}

    @router.post("/dependency/stop")
    async def stop_dependency_route():
        record = chaos_manager.stop_dependency_failure()
        return {"status": "stopped", "record": record}

    # 7. Traffic Spike
    @router.post("/traffic-spike")
    async def trigger_traffic_route(req: TrafficSpikeRequest = TrafficSpikeRequest()):
        record = chaos_manager.trigger_traffic_spike(
            requests_per_second=req.requests_per_second,
            duration_seconds=req.duration_seconds,
            target_path=req.target_path
        )
        return {"status": "triggered", "record": record}

    @router.post("/traffic-spike/stop")
    async def stop_traffic_route():
        record = chaos_manager.stop_traffic_spike()
        return {"status": "stopped", "record": record}

    # Reset / Stop All
    @router.post("/stop")
    @router.post("/reset")
    async def reset_all_route():
        stopped = chaos_manager.reset_all()
        return {"status": "reset", "stopped_records": stopped}

    # Status & History
    @router.get("/status")
    async def get_status_route():
        return chaos_manager.get_status()

    @router.get("/history", response_model=List[ChaosRecord])
    async def get_history_route():
        return chaos_manager.get_history()

    app.add_middleware(ChaosMiddleware, chaos_manager=chaos_manager)
    app.include_router(router)
    return router
