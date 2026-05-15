"""
NexusDesk API Server v4 (Cyber-Premium)
=====================================
Upgraded with Server-Sent Events (SSE) for real-time streaming,
CORS lockdown, Rate Limiting, API Key Auth, PII Masking, and Request Queuing.
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import time
import json
import asyncio
import threading
from typing import List, Dict, Any, AsyncGenerator
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import existing logic
from data_manager import get_all_tickets
from agent import resolve_ticket
from tools import get_audit_log, reset_audit_log
from evaluator import evaluate_results
from config import MAX_WORKERS, CORS_ALLOWED_ORIGINS, REQUEST_QUEUE_SIZE
from security import RateLimiter, validate_api_key, mask_pii_in_dict

app = FastAPI(title="NexusDesk Agent API v4")

# CORS Lockdown (B8)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Rate Limiter Instance (B9)
rate_limiter = RateLimiter()

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Skip rate limiting for SSE events to avoid connection drops
    if request.url.path == "/api/events":
        return await call_next(request)
        
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not rate_limiter.is_allowed(client_ip):
        return StreamingResponse(
            iter([json.dumps({"detail": "Rate limit exceeded. Try again later."})]),
            status_code=429,
            media_type="application/json"
        )
    return await call_next(request)

# Global State
_STATE = {
    "is_running": False,
    "last_run_time": 0,
    "progress": 0,
    "total": 0,
    "results": [],
    "last_error": None
}
_LOCK = threading.Lock()
_EVENT_QUEUES: List[asyncio.Queue] = [] # For SSE
_SWEEP_QUEUE = asyncio.Queue(maxsize=REQUEST_QUEUE_SIZE) # For Request Queuing (B13)

@app.get("/api/health")
def health_check():
    """Health check endpoint for load balancers (B12)."""
    return {"status": "ok", "uptime": time.time(), "workers": MAX_WORKERS}

@app.get("/api/status", dependencies=[Depends(validate_api_key)])
def get_status():
    with _LOCK:
        return _STATE

@app.get("/api/results", dependencies=[Depends(validate_api_key)])
def get_results():
    with _LOCK:
        results = _STATE["results"]
        audit_log = get_audit_log()
        
        # Apply PII Masking to results and audit log (B11)
        masked_results = [mask_pii_in_dict(r) for r in results]
        masked_audit = [mask_pii_in_dict(a) for a in audit_log]
        
        return {
            "results": masked_results,
            "audit_log": masked_audit,
            "evaluation": evaluate_results(get_all_tickets(), results) if results else None
        }

@app.post("/api/run", dependencies=[Depends(validate_api_key)])
async def run_autonomous_sweep(background_tasks: BackgroundTasks):
    with _LOCK:
        if _STATE["is_running"]:
            # Implement queuing if already running (B13)
            if _SWEEP_QUEUE.full():
                raise HTTPException(status_code=429, detail="Sweep queue full. Try again later.")
            try:
                _SWEEP_QUEUE.put_nowait(1)
                return {"message": "Sweep queued.", "total": len(get_all_tickets())}
            except asyncio.QueueFull:
                raise HTTPException(status_code=429, detail="Sweep queue full.")
        
        _STATE["is_running"] = True
        _STATE["progress"] = 0
        _STATE["results"] = []
        _STATE["last_error"] = None
        reset_audit_log()

    # Launch in background
    loop = asyncio.get_event_loop()
    background_tasks.add_task(_background_runner_async, loop)
    return {"message": "Autonomous sweep started", "total": len(get_all_tickets())}

async def _background_runner_async(loop: asyncio.AbstractEventLoop):
    global _STATE
    
    while True:
        tickets = get_all_tickets()
        total = len(tickets)
        
        with _LOCK:
            _STATE["total"] = total
            _STATE["results"] = []
            _STATE["progress"] = 0
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(resolve_ticket, t): i for i, t in enumerate(tickets)}
            
            for future in as_completed(futures):
                i = futures[future]
                try:
                    res = future.result()
                    await _broadcast_event({
                        "type": "ticket_complete",
                        "ticket_id": tickets[i]["ticket_id"],
                        "resolution": res["resolution"],
                        "category": res["category"]
                    })
                    
                    for step in res.get("reasoning_steps", []):
                        # Mask PII in streaming events (B11)
                        masked_step = mask_pii_in_dict(step)
                        await _broadcast_event({
                            "type": "reasoning_step",
                            "ticket_id": tickets[i]["ticket_id"],
                            "data": masked_step
                        })
                        await asyncio.sleep(0.02) 
    
                except Exception as e:
                    res = {"ticket_id": tickets[i]["ticket_id"], "error": str(e), "resolution": "error"}
                
                with _LOCK:
                    _STATE["results"].append(res)
                    _STATE["progress"] += 1
                    
        elapsed = time.time() - start_time
        
        with _LOCK:
            _STATE["last_run_time"] = round(elapsed, 3)
            
        await _broadcast_event({"type": "sweep_complete", "elapsed": elapsed})
        
        # Check queue for next run (B13)
        try:
            _SWEEP_QUEUE.get_nowait()
            reset_audit_log()
        except asyncio.QueueEmpty:
            with _LOCK:
                _STATE["is_running"] = False
            break

# SSE Logic
async def _broadcast_event(data: dict):
    msg = json.dumps(data)
    for q in _EVENT_QUEUES:
        await q.put(msg)

@app.get("/api/events")
async def sse_events(request: Request):
    queue = asyncio.Queue()
    _EVENT_QUEUES.append(queue)
    
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                data = await queue.get()
                yield f"data: {data}\n\n"
        finally:
            if queue in _EVENT_QUEUES:
                _EVENT_QUEUES.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Graceful Shutdown (B14)
@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down NexusDesk API server. Cleaning up queues...")
    _EVENT_QUEUES.clear()

# Static mounting
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

