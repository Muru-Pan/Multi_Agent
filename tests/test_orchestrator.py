import pytest

from app.config.settings import Settings
from app.models.schemas import AgentType, PlanStep, TaskLifecycle
from app.orchestrator import TaskOrchestrator


class DummyQueue:
    def __init__(self):
        self.status_calls = []
        self.events = []

    async def connect(self):
        return None

    async def close(self):
        return None

    async def create_consumer_group(self, stream: str, group: str):
        return None

    def agent_stream(self, agent: str) -> str:
        return f"task_stream:{agent}"

    async def set_task_status(self, task_id: str, **kwargs):
        self.status_calls.append((task_id, kwargs))

    async def publish_event(self, task_id: str, event: str, data: dict):
        self.events.append((task_id, event, data))

    async def set_step_state(self, task_id: str, state):
        return None

    async def publish_task(self, message):
        return None


@pytest.mark.asyncio
async def test_dependency_batches_are_grouped_by_level():
    orchestrator = TaskOrchestrator(Settings(), DummyQueue())
    steps = [
        PlanStep(id="s1", agent=AgentType.RETRIEVER, input="a"),
        PlanStep(id="s2", agent=AgentType.RETRIEVER, input="b"),
        PlanStep(id="s3", agent=AgentType.WRITER, input="c", depends_on=["s1", "s2"], critical=True),
    ]
    batches = orchestrator._dependency_batches(steps)
    assert [[step.id for step in batch] for batch in batches] == [["s1", "s2"], ["s3"]]


@pytest.mark.asyncio
async def test_dependency_batches_raise_on_cycle():
    orchestrator = TaskOrchestrator(Settings(), DummyQueue())
    steps = [
        PlanStep(id="s1", agent=AgentType.RETRIEVER, input="a", depends_on=["s2"]),
        PlanStep(id="s2", agent=AgentType.WRITER, input="b", depends_on=["s1"], critical=True),
    ]
    with pytest.raises(RuntimeError):
        orchestrator._dependency_batches(steps)


@pytest.mark.asyncio
async def test_run_task_falls_back_when_planner_times_out(monkeypatch):
    queue = DummyQueue()
    orchestrator = TaskOrchestrator(Settings(planner_timeout_seconds=0), queue)

    async def slow_plan(task_id: str, user_task: str):
        await __import__("asyncio").sleep(0.01)

    async def fake_execute(plan, user_task: str):
        return {"step_2": {"text": "done"}}

    monkeypatch.setattr(orchestrator.planner, "plan", slow_plan)
    monkeypatch.setattr(orchestrator, "_execute_plan", fake_execute)

    await orchestrator._run_task("task-1", "do something")

    assert any(event == "plan_ready" for _, event, _ in queue.events)
    assert queue.status_calls[-1][1]["status"] is TaskLifecycle.COMPLETE
