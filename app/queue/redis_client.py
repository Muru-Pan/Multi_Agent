from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, AsyncIterator

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config.settings import Settings
from app.models.schemas import EventEnvelope, QueueResultMessage, QueueTaskMessage, StepState, TaskLifecycle, TaskStatusResponse

logger = logging.getLogger(__name__)


class RedisQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._event_queues: dict[str, list[asyncio.Queue[EventEnvelope]]] = defaultdict(list)
        self._event_lock = asyncio.Lock()

    async def connect(self) -> None:
        await self.redis.ping()

    async def close(self) -> None:
        await self.redis.aclose()

    def agent_stream(self, agent: str) -> str:
        return f"{self.settings.task_stream_prefix}:{agent}"

    def event_stream(self, task_id: str) -> str:
        return f"{self.settings.event_stream_prefix}:{task_id}"

    def task_key(self, task_id: str) -> str:
        return f"task:{task_id}"

    async def create_consumer_group(self, stream: str, group: str) -> None:
        try:
            await self.redis.xgroup_create(name=stream, groupname=group, id="$", mkstream=True)
        except RedisError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish_task(self, message: QueueTaskMessage) -> str:
        return await self.redis.xadd(self.agent_stream(message.agent.value), {"payload": message.model_dump_json()})

    async def publish_result(self, message: QueueResultMessage) -> str:
        return await self.redis.xadd(self.settings.result_stream, {"payload": message.model_dump_json()})

    async def publish_event(self, task_id: str, event: str, data: dict[str, Any]) -> str:
        envelope = EventEnvelope(event=event, data=data)
        event_id = await self.redis.xadd(self.event_stream(task_id), {"payload": envelope.model_dump_json()})
        await self.redis.expire(self.event_stream(task_id), self.settings.task_ttl_seconds)
        await self._fan_out_event(task_id, envelope)
        return event_id

    async def _fan_out_event(self, task_id: str, envelope: EventEnvelope) -> None:
        async with self._event_lock:
            for queue in list(self._event_queues[task_id]):
                await queue.put(envelope)

    async def subscribe_events(self, task_id: str) -> AsyncIterator[EventEnvelope]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        async with self._event_lock:
            self._event_queues[task_id].append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.event in {"task_complete", "task_failed"}:
                    return
        finally:
            async with self._event_lock:
                self._event_queues[task_id].remove(queue)
                if not self._event_queues[task_id]:
                    self._event_queues.pop(task_id, None)

    async def load_past_events(self, task_id: str) -> list[EventEnvelope]:
        items = await self.redis.xrange(self.event_stream(task_id))
        events: list[EventEnvelope] = []
        for _, payload in items:
            raw = payload.get("payload")
            if raw:
                events.append(EventEnvelope.model_validate_json(raw))
        return events

    async def set_task_status(
        self,
        task_id: str,
        *,
        status: TaskLifecycle,
        user_task: str,
        partial_result: str | None = None,
        final_result: str | None = None,
        error: str | None = None,
    ) -> None:
        mapping = {
            "status": status.value,
            "user_task": user_task,
            "partial_result": partial_result or "",
            "final_result": final_result or "",
            "error": error or "",
        }
        await self.redis.hset(self.task_key(task_id), mapping=mapping)
        await self.redis.expire(self.task_key(task_id), self.settings.task_ttl_seconds)

    async def get_task_status(self, task_id: str) -> dict[str, str]:
        return await self.redis.hgetall(self.task_key(task_id))

    async def set_step_state(self, task_id: str, state: StepState) -> None:
        key = f"{self.task_key(task_id)}:steps"
        await self.redis.hset(key, state.step_id, state.model_dump_json())
        await self.redis.expire(key, self.settings.task_ttl_seconds)

    async def get_step_states(self, task_id: str) -> list[StepState]:
        raw = await self.redis.hgetall(f"{self.task_key(task_id)}:steps")
        return [StepState.model_validate_json(value) for _, value in sorted(raw.items())]

    async def build_status_response(self, task_id: str) -> TaskStatusResponse | None:
        task = await self.get_task_status(task_id)
        if not task:
            return None
        return TaskStatusResponse(
            task_id=task_id,
            status=TaskLifecycle(task["status"]),
            user_task=task.get("user_task", ""),
            steps=await self.get_step_states(task_id),
            partial_result=task.get("partial_result") or None,
            final_result=task.get("final_result") or None,
            error=task.get("error") or None,
        )

    async def read_group(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[tuple[str, QueueTaskMessage]]:
        response = await self.redis.xreadgroup(groupname=group, consumername=consumer, streams={stream: ">"}, count=count, block=block_ms)
        messages: list[tuple[str, QueueTaskMessage]] = []
        for _, entries in response:
            for message_id, payload in entries:
                messages.append((message_id, QueueTaskMessage.model_validate_json(payload["payload"])))
        return messages

    async def read_result_group(
        self,
        *,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 1000,
    ) -> list[tuple[str, QueueResultMessage]]:
        response = await self.redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={self.settings.result_stream: ">"},
            count=count,
            block=block_ms,
        )
        messages: list[tuple[str, QueueResultMessage]] = []
        for _, entries in response:
            for message_id, payload in entries:
                messages.append((message_id, QueueResultMessage.model_validate_json(payload["payload"])))
        return messages

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        await self.redis.xack(stream, group, message_id)

    async def claim_stale(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int = 10,
    ) -> list[tuple[str, QueueTaskMessage]]:
        try:
            _, entries, _ = await self.redis.xautoclaim(
                name=stream,
                groupname=group,
                consumername=consumer,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=count,
            )
        except RedisError:
            return []
        claimed: list[tuple[str, QueueTaskMessage]] = []
        for message_id, payload in entries:
            claimed.append((message_id, QueueTaskMessage.model_validate_json(payload["payload"])))
        return claimed

    async def dead_letter(self, agent: str, message: QueueTaskMessage, error: str) -> None:
        stream = f"{self.settings.dead_letter_prefix}:{agent}"
        payload = {"payload": json.dumps({"message": message.model_dump(), "error": error})}
        await self.redis.xadd(stream, payload)
