from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List
from app.monitoring import setup_monitoring
from app.chaos import ChaosManager, setup_chaos_routes

app = FastAPI(title="SentinelOps - User Service")

# Setup monitoring and chaos injection
monitoring_collector = setup_monitoring(app, "user-service")
chaos_manager = ChaosManager(service_name="user-service", port=8001)
setup_chaos_routes(app, chaos_manager)
monitoring_collector.chaos_manager = chaos_manager

class User(BaseModel):
    id: int
    name: str
    email: str
    role: str

# In-memory user database
USERS_DB = [
    User(id=1, name="Alice Johnson", email="alice@sentinelops.local", role="Admin"),
    User(id=2, name="Bob Smith", email="bob@sentinelops.local", role="Developer"),
    User(id=3, name="Charlie Brown", email="charlie@sentinelops.local", role="Analyst"),
]

@app.get("/health")
async def health():
    if chaos_manager.unavailability_active:
        return JSONResponse(
            status_code=503,
            content={
                "service": "user-service",
                "status": "unavailable",
                "detail": "Simulated service outage"
            }
        )
    return {
        "service": "user-service",
        "status": "healthy"
    }

@app.get("/users", response_model=List[User])
async def get_users():
    return USERS_DB

@app.get("/users/{user_id}", response_model=User)
async def get_user(user_id: int):
    for user in USERS_DB:
        if user.id == user_id:
            return user
    raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found")
