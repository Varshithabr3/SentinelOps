from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import os
import httpx
from datetime import datetime
from app.monitoring import setup_monitoring
from app.chaos import ChaosManager, setup_chaos_routes

app = FastAPI(title="SentinelOps - Order Service")

# Setup monitoring and chaos injection
monitoring_collector = setup_monitoring(app, "order-service")
chaos_manager = ChaosManager(service_name="order-service", port=8002)
setup_chaos_routes(app, chaos_manager)
monitoring_collector.chaos_manager = chaos_manager

PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8003")

class OrderCreate(BaseModel):
    user_id: int
    items: List[str] = Field(..., min_items=1, description="List of item names in the order")
    total_amount: float = Field(..., gt=0, description="Total order amount")
    payment_method: str = "credit_card"

class Order(BaseModel):
    id: int
    user_id: int
    items: List[str]
    total_amount: float
    status: str
    payment_details: Optional[Dict[str, Any]] = None
    created_at: str

# In-memory database for orders
ORDERS_DB: List[Order] = []
order_counter = 1

@app.get("/health")
async def health():
    if chaos_manager.unavailability_active:
        return JSONResponse(
            status_code=503,
            content={
                "service": "order-service",
                "status": "unavailable",
                "detail": "Simulated service outage"
            }
        )
    return {
        "service": "order-service",
        "status": "healthy"
    }

@app.get("/orders", response_model=List[Order])
async def get_orders():
    return ORDERS_DB

@app.post("/orders", response_model=Order, status_code=status.HTTP_201_CREATED)
async def create_order(order_data: OrderCreate):
    global order_counter
    current_order_id = order_counter

    # Check for simulated downstream dependency failure
    if chaos_manager.dependency_active:
        target = chaos_manager.dependency_target or "payment-service"
        mode = chaos_manager.dependency_mode
        if mode == "timeout":
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Dependency failure: Request to {target} timed out"
            )
        elif mode == "error_503":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Dependency failure: Downstream service {target} is unavailable (503)"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Dependency failure: Unable to reach {target} (connection refused)"
            )

    # Prepare payment request payload
    payment_payload = {
        "order_id": current_order_id,
        "amount": order_data.total_amount,
        "payment_method": order_data.payment_method
    }

    # Call Payment Service over HTTP
    payment_url = f"{PAYMENT_SERVICE_URL.rstrip('/')}/payments"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(payment_url, json=payment_payload)
            
            if response.status_code != 201:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Payment processing failed: {response.text}"
                )
            
            payment_info = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to reach Payment Service at {payment_url}: {str(exc)}"
        )

    # Store created order
    new_order = Order(
        id=current_order_id,
        user_id=order_data.user_id,
        items=order_data.items,
        total_amount=order_data.total_amount,
        status="completed",
        payment_details=payment_info,
        created_at=datetime.utcnow().isoformat()
    )
    
    ORDERS_DB.append(new_order)
    order_counter += 1

    return new_order
