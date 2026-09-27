from fastapi import FastAPI, Request, Response, HTTPException
import os
import httpx

app = FastAPI(title="SentinelOps - API Gateway")

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:8001")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://payment-service:8003")

@app.get("/health")
async def health():
    return {
        "service": "api-gateway",
        "status": "healthy"
    }

async def forward_request(target_url: str, request: Request) -> Response:
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding", "connection")
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                params=dict(request.query_params),
                content=body
            )
            response_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in ("content-length", "transfer-encoding", "connection")
            }
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=response_headers,
                media_type=resp.headers.get("content-type")
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"API Gateway failed to reach service at {target_url}: {str(exc)}"
            )

# Core Business API Routes
@app.api_route("/api/users", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.api_route("/api/users/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def route_users(request: Request, path: str = ""):
    clean_path = path.strip("/")
    subpath = f"/{clean_path}" if clean_path else ""
    target_url = f"{USER_SERVICE_URL.rstrip('/')}/users{subpath}"
    return await forward_request(target_url, request)

@app.api_route("/api/orders", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.api_route("/api/orders/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def route_orders(request: Request, path: str = ""):
    clean_path = path.strip("/")
    subpath = f"/{clean_path}" if clean_path else ""
    target_url = f"{ORDER_SERVICE_URL.rstrip('/')}/orders{subpath}"
    return await forward_request(target_url, request)

@app.api_route("/api/payments", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.api_route("/api/payments/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def route_payments(request: Request, path: str = ""):
    clean_path = path.strip("/")
    subpath = f"/{clean_path}" if clean_path else ""
    target_url = f"{PAYMENT_SERVICE_URL.rstrip('/')}/payments{subpath}"
    return await forward_request(target_url, request)

# Chaos Engineering Routes
@app.api_route("/api/chaos/users/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def route_chaos_users(request: Request, path: str = ""):
    clean_path = path.strip("/")
    target_url = f"{USER_SERVICE_URL.rstrip('/')}/chaos/{clean_path}"
    return await forward_request(target_url, request)

@app.api_route("/api/chaos/orders/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def route_chaos_orders(request: Request, path: str = ""):
    clean_path = path.strip("/")
    target_url = f"{ORDER_SERVICE_URL.rstrip('/')}/chaos/{clean_path}"
    return await forward_request(target_url, request)

@app.api_route("/api/chaos/payments/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def route_chaos_payments(request: Request, path: str = ""):
    clean_path = path.strip("/")
    target_url = f"{PAYMENT_SERVICE_URL.rstrip('/')}/chaos/{clean_path}"
    return await forward_request(target_url, request)

@app.get("/api/chaos/status")
async def consolidated_chaos_status():
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = {}
        for name, url in [("user-service", USER_SERVICE_URL), ("order-service", ORDER_SERVICE_URL), ("payment-service", PAYMENT_SERVICE_URL)]:
            try:
                res = await client.get(f"{url.rstrip('/')}/chaos/status")
                results[name] = res.json()
            except Exception as e:
                results[name] = {"error": str(e)}
        return results

@app.get("/api/chaos/history")
async def consolidated_chaos_history():
    async with httpx.AsyncClient(timeout=5.0) as client:
        all_history = []
        for _, url in [("user-service", USER_SERVICE_URL), ("order-service", ORDER_SERVICE_URL), ("payment-service", PAYMENT_SERVICE_URL)]:
            try:
                res = await client.get(f"{url.rstrip('/')}/chaos/history")
                all_history.extend(res.json())
            except Exception:
                pass
        all_history.sort(key=lambda x: x.get("start_timestamp", ""), reverse=True)
        return all_history

@app.post("/api/chaos/reset")
async def consolidated_chaos_reset():
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = {}
        for name, url in [("user-service", USER_SERVICE_URL), ("order-service", ORDER_SERVICE_URL), ("payment-service", PAYMENT_SERVICE_URL)]:
            try:
                res = await client.post(f"{url.rstrip('/')}/chaos/reset")
                results[name] = res.json()
            except Exception as e:
                results[name] = {"error": str(e)}
        return {"status": "all_reset", "services": results}
