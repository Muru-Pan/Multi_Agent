from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette import EventSourceResponse

from app.config.settings import get_settings
from app.models.schemas import TaskCreateRequest
from app.orchestrator import TaskOrchestrator
from app.queue.redis_client import RedisQueue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = get_settings()
queue = RedisQueue(settings)
orchestrator = TaskOrchestrator(settings, queue)

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    await orchestrator.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await orchestrator.shutdown()


@app.post("/task")
async def create_task(request: TaskCreateRequest):
    return await orchestrator.create_task(request)


@app.get("/task/{task_id}/stream")
async def stream_task(task_id: str):
    status = await queue.build_status_response(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="task not found")

    async def event_generator():
        if settings.enable_event_replay:
            for envelope in await queue.load_past_events(task_id):
                yield {
                    "id": envelope.id,
                    "event": envelope.event,
                    "data": json.dumps(envelope.data),
                }
        async for envelope in queue.subscribe_events(task_id):
            yield {
                "id": envelope.id,
                "event": envelope.event,
                "data": json.dumps(envelope.data),
            }

    return EventSourceResponse(event_generator())


@app.get("/task/{task_id}/status")
async def get_task_status(task_id: str):
    status = await queue.build_status_response(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="task not found")
    return status


@app.get("/health")
async def health():
    try:
        await queue.redis.ping()
        redis_status = "connected"
    except Exception:
        redis_status = "disconnected"
    return {"status": "ok", "redis": redis_status}
