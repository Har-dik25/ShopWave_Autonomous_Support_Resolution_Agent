"""
ShopWave API Server v4 (Cyber-Premium)
=====================================
Upgraded with Server-Sent Events (SSE) for real-time streaming of
reasoning steps and tool calls.
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
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
from config import MAX_WORKERS

app = FastAPI(title="ShopWave Agent API v4")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/api/status")
def get_status():
    with _LOCK:
        return _STATE

@app.get("/api/results")
def get_results():
    with _LOCK:
        return {
            "results": _STATE["results"],
            "audit_log": get_audit_log(),
            "evaluation": evaluate_results(get_all_tickets(), _STATE["results"]) if _STATE["results"] else None
        }

@app.post("/api/run")
async def run_autonomous_sweep(background_tasks: BackgroundTasks):
    with _LOCK:
        if _STATE["is_running"]:
            raise HTTPException(status_code=400, detail="Sweep is already in progress.")
        
        _STATE["is_running"] = True
        _STATE["progress"] = 0
        _STATE["results"] = []
        _STATE["last_error"] = None
        reset_audit_log()

    # Launch in background
    # We use asyncio here to handle queue management better
    loop = asyncio.get_event_loop()
    background_tasks.add_task(_background_runner_async, loop)
    return {"message": "Autonomous sweep started", "total": len(get_all_tickets())}

async def _background_runner_async(loop: asyncio.AbstractEventLoop):
    global _STATE
    tickets = get_all_tickets()
    total = len(tickets)
    
    with _LOCK:
        _STATE["total"] = total
    
    start_time = time.time()
    
    # We'll run the legacy threaded resolve_ticket in the executor
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Wrap future completion in a way we can broadcast
        futures = {executor.submit(resolve_ticket, t): i for i, t in enumerate(tickets)}
        
        for future in as_completed(futures):
            i = futures[future]
            try:
                res = future.result()
                # Broadcast the result completion to SSE fans
                await _broadcast_event({
                    "type": "ticket_complete",
                    "ticket_id": tickets[i]["ticket_id"],
                    "resolution": res["resolution"],
                    "category": res["category"]
                })
                
                # Also broadcast every reasoning step for "streaming" effect
                for step in res.get("reasoning_steps", []):
                    await _broadcast_event({
                        "type": "reasoning_step",
                        "ticket_id": tickets[i]["ticket_id"],
                        "data": step
                    })
                    await asyncio.sleep(0.02) # Subtle artificial delay for visual flow

            except Exception as e:
                res = {"ticket_id": tickets[i]["ticket_id"], "error": str(e), "resolution": "error"}
            
            with _LOCK:
                _STATE["results"].append(res)
                _STATE["progress"] += 1
                
    elapsed = time.time() - start_time
    with _LOCK:
        _STATE["is_running"] = False
        _STATE["last_run_time"] = round(elapsed, 3)
    
    await _broadcast_event({"type": "sweep_complete", "elapsed": elapsed})

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
                # Check for client disconnect
                if await request.is_disconnected():
                    break
                data = await queue.get()
                yield f"data: {data}\n\n"
        finally:
            _EVENT_QUEUES.remove(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Static mounting
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
