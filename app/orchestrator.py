from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict, deque
from typing import Any
from uuid import uuid4

from app.agents.planner import PlannerAgent
from app.agents.retriever import RetrieverAgent
from app.agents.writer import WriterAgent
from app.config.settings import Settings
from app.llm.router import LLMRouter
from app.models.schemas import (
    AgentType,
    PlanStep,
    QueueResultMessage,
    QueueTaskMessage,
    StepResult,
    StepState,
    StepStatus,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskLifecycle,
    TaskPlan,
)
from app.queue.redis_client import RedisQueue

logger = logging.getLogger(__name__)


class TaskOrchestrator:
    def __init__(self, settings: Settings, queue: RedisQueue) -> None:
        self.settings = settings
        self.queue = queue
        self.router = LLMRouter(settings)
        self.planner = PlannerAgent(settings, self.router)
        self.retriever = RetrieverAgent(settings)
        self.writer = WriterAgent(settings, self.router)
        self._task_runs: dict[str, asyncio.Task[None]] = {}
        self._result_waiters: dict[tuple[str, str], asyncio.Future[StepResult]] = {}
        self._result_listener: asyncio.Task[None] | None = None
        self._worker_tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        await self.queue.connect()
        await self.queue.create_consumer_group(self.settings.result_stream, "orchestrator_group")
        for agent in AgentType:
            await self.queue.create_consumer_group(self.queue.agent_stream(agent.value), f"{agent.value}_group")
        self._result_listener = asyncio.create_task(self._result_listener_loop(), name="result-listener")
        for agent in AgentType:
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(agent), name=f"worker-{agent.value}"))

    async def shutdown(self) -> None:
        for task in self._worker_tasks:
            task.cancel()
        if self._result_listener:
            self._result_listener.cancel()
        for run in self._task_runs.values():
            run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        if self._result_listener:
            with contextlib.suppress(asyncio.CancelledError):
                await self._result_listener
        await self.queue.close()

    async def create_task(self, request: TaskCreateRequest) -> TaskCreateResponse:
        task_id = str(uuid4())
        await self.queue.set_task_status(task_id, status=TaskLifecycle.RECEIVED, user_task=request.task)
        self._task_runs[task_id] = asyncio.create_task(self._run_task(task_id, request.task), name=f"task-{task_id}")
        return TaskCreateResponse(
            task_id=task_id,
            status=TaskLifecycle.RECEIVED,
            stream_url=f"/task/{task_id}/stream",
            status_url=f"/task/{task_id}/status",
        )

    async def _run_task(self, task_id: str, user_task: str) -> None:
        try:
            await self.queue.set_task_status(task_id, status=TaskLifecycle.PLANNING, user_task=user_task)
            try:
                plan = await asyncio.wait_for(
                    self.planner.plan(task_id, user_task),
                    timeout=self.settings.planner_timeout_seconds,
                )
            except Exception:
                logger.warning("planner failed, using default plan", extra={"task_id": task_id})
                plan = self.planner.default_plan(task_id, user_task)
            await self.queue.publish_event(task_id, "plan_ready", {"steps": [step.model_dump(mode="json") for step in plan.steps]})
            await self.queue.set_task_status(task_id, status=TaskLifecycle.EXECUTING, user_task=user_task)
            step_results = await self._execute_plan(plan, user_task)
            final_result = self._extract_final_result(step_results)
            lifecycle = TaskLifecycle.COMPLETE if final_result else TaskLifecycle.PARTIAL_FAILURE
            await self.queue.set_task_status(task_id, status=lifecycle, user_task=user_task, final_result=final_result)
            event = "task_complete" if final_result else "task_failed"
            payload = {"full_result": final_result} if final_result else {"reason": "critical output missing", "partial_results": step_results}
            await self.queue.publish_event(task_id, event, payload)
        except Exception as exc:
            logger.exception("task failed", extra={"task_id": task_id})
            await self.queue.set_task_status(task_id, status=TaskLifecycle.FAILED, user_task=user_task, error=str(exc))
            await self.queue.publish_event(task_id, "task_failed", {"reason": str(exc), "partial_results": {}})

    async def _execute_plan(self, plan: TaskPlan, user_task: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
        state_by_id: dict[str, StepState] = {
            step.id: StepState(step_id=step.id, agent=step.agent, critical=step.critical) for step in plan.steps
        }
        for state in state_by_id.values():
            await self.queue.set_step_state(plan.task_id, state)
        for batch in self._dependency_batches(plan.steps):
            await asyncio.gather(*(self._run_step(plan.task_id, step, user_task, results, state_by_id) for step in batch))
        return results

    async def _run_step(
        self,
        task_id: str,
        step: PlanStep,
        user_task: str,
        results: dict[str, Any],
        state_by_id: dict[str, StepState],
    ) -> None:
        if any(dep not in results for dep in step.depends_on):
            raise RuntimeError(f"step {step.id} scheduled before dependencies completed")
        state = state_by_id[step.id]
        last_error: str | None = None
        for attempt in range(1, step.max_retries + 2):
            state.status = StepStatus.IN_PROGRESS
            state.attempt = attempt
            state.error = None
            await self.queue.set_step_state(task_id, state)
            await self.queue.publish_event(task_id, "step_started", {"step_id": step.id, "agent": step.agent.value})
            future: asyncio.Future[StepResult] = asyncio.get_running_loop().create_future()
            self._result_waiters[(task_id, step.id)] = future
            message = QueueTaskMessage(
                task_id=task_id,
                step_id=step.id,
                agent=step.agent,
                input=step.input,
                user_task=user_task,
                depends_on=step.depends_on,
                prior_results=results,
                attempt=attempt,
                critical=step.critical,
                timeout_seconds=step.timeout_seconds or self.settings.step_timeout_seconds,
            )
            await self.queue.publish_task(message)
            try:
                result = await asyncio.wait_for(future, timeout=message.timeout_seconds)
                if result.status is StepStatus.DONE:
                    results[step.id] = result.output or {}
                    state.status = StepStatus.DONE
                    state.result_preview = (result.output or {}).get("summary", "")[:200]
                    await self.queue.set_step_state(task_id, state)
                    await self.queue.publish_event(
                        task_id,
                        "step_done",
                        {"step_id": step.id, "agent": step.agent.value, "result_preview": state.result_preview},
                    )
                    if step.agent is AgentType.WRITER:
                        await self.queue.set_task_status(
                            task_id,
                            status=TaskLifecycle.STREAMING,
                            user_task=user_task,
                            partial_result=(result.output or {}).get("text"),
                        )
                    return
            except Exception as exc:
                last_error = str(exc)
                state.status = StepStatus.RETRYING if attempt <= step.max_retries else StepStatus.FAILED
                state.error = last_error
                await self.queue.set_step_state(task_id, state)
                await self.queue.publish_event(
                    task_id,
                    "step_failed",
                    {"step_id": step.id, "reason": last_error, "retry_count": attempt - 1},
                )
            finally:
                self._result_waiters.pop((task_id, step.id), None)
        if step.critical:
            raise RuntimeError(f"critical step failed: {step.id} ({last_error})")

    def _dependency_batches(self, steps: list[PlanStep]) -> list[list[PlanStep]]:
        step_map = {step.id: step for step in steps}
        indegree = {step.id: len(step.depends_on) for step in steps}
        graph: dict[str, list[str]] = defaultdict(list)
        for step in steps:
            for dep in step.depends_on:
                graph[dep].append(step.id)
        queue = deque([step_id for step_id, degree in indegree.items() if degree == 0])
        batches: list[list[PlanStep]] = []
        while queue:
            level_size = len(queue)
            batch: list[PlanStep] = []
            for _ in range(level_size):
                current = queue.popleft()
                batch.append(step_map[current])
                for neighbor in graph[current]:
                    indegree[neighbor] -= 1
                    if indegree[neighbor] == 0:
                        queue.append(neighbor)
            batches.append(batch)
        if sum(len(batch) for batch in batches) != len(steps):
            raise RuntimeError("dependency graph contains a cycle")
        return batches

    async def _result_listener_loop(self) -> None:
        consumer = "orchestrator-1"
        while True:
            messages = await self.queue.read_result_group(group="orchestrator_group", consumer=consumer, count=10)
            for message_id, payload in messages:
                waiter = self._result_waiters.get((payload.task_id, payload.step_id))
                if waiter and not waiter.done():
                    waiter.set_result(payload.result)
                await self.queue.ack(self.settings.result_stream, "orchestrator_group", message_id)

    async def _worker_loop(self, agent: AgentType) -> None:
        stream = self.queue.agent_stream(agent.value)
        group = f"{agent.value}_group"
        consumer = f"{agent.value}-worker-1"
        while True:
            reclaimed = await self.queue.claim_stale(
                stream=stream,
                group=group,
                consumer=consumer,
                min_idle_ms=self.settings.pending_timeout_seconds * 1000,
            )
            messages = reclaimed or await self.queue.read_group(stream=stream, group=group, consumer=consumer)
            for message_id, payload in messages:
                try:
                    result = await self._dispatch_agent(payload)
                    await self.queue.publish_result(
                        QueueResultMessage(task_id=payload.task_id, step_id=payload.step_id, result=result)
                    )
                    await self.queue.ack(stream, group, message_id)
                except Exception as exc:
                    if payload.attempt > self.settings.max_retries:
                        await self.queue.dead_letter(agent.value, payload, str(exc))
                        await self.queue.ack(stream, group, message_id)

    async def _dispatch_agent(self, message: QueueTaskMessage) -> StepResult:
        if message.agent is AgentType.RETRIEVER:
            return await self.retriever.execute(message)
        if message.agent is AgentType.WRITER:
            return await self.writer.execute(
                message,
                on_token=lambda token: self.queue.publish_event(message.task_id, "stream_token", {"token": token}),
            )
        raise RuntimeError(f"unsupported agent: {message.agent}")

    def _extract_final_result(self, step_results: dict[str, Any]) -> str | None:
        writer_outputs = [value.get("text") for value in step_results.values() if isinstance(value, dict) and "text" in value]
        return writer_outputs[-1] if writer_outputs else None
