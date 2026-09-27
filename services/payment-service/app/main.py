from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uuid
from datetime import datetime
from app.monitoring import setup_monitoring
from app.chaos import ChaosManager, setup_chaos_routes

app = FastAPI(title="SentinelOps - Payment Service")

# Setup monitoring and chaos injection
monitoring_collector = setup_monitoring(app, "payment-service")
chaos_manager = ChaosManager(service_name="payment-service", port=8003)
setup_chaos_routes(app, chaos_manager)
monitoring_collector.chaos_manager = chaos_manager

class PaymentRequest(BaseModel):
    order_id: int
    amount: float = Field(gt=0, description="Payment amount must be greater than 0")
    payment_method: str = "credit_card"

class PaymentResponse(BaseModel):
    payment_id: str
    order_id: int
    amount: float
    status: str
    processed_at: str

@app.get("/health")
async def health():
    if chaos_manager.unavailability_active:
        return JSONResponse(
            status_code=503,
            content={
                "service": "payment-service",
                "status": "unavailable",
                "detail": "Simulated service outage"
            }
        )
    return {
        "service": "payment-service",
        "status": "healthy"
    }

@app.post("/payments", response_model=PaymentResponse, status_code=201)
async def create_payment(payment_req: PaymentRequest):
    # Check for simulated downstream payment gateway failure
    if chaos_manager.dependency_active:
        target = chaos_manager.dependency_target or "bank-gateway"
        mode = chaos_manager.dependency_mode
        if mode == "timeout":
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Dependency failure: Timeout connecting to payment gateway {target}"
            )
        elif mode == "error_503":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Dependency failure: Payment gateway {target} returned 503"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Dependency failure: Payment gateway {target} connection refused"
            )

    payment_id = f"pay_{uuid.uuid4().hex[:8]}"
    return PaymentResponse(
        payment_id=payment_id,
        order_id=payment_req.order_id,
        amount=payment_req.amount,
        status="success",
        processed_at=datetime.utcnow().isoformat()
    )
