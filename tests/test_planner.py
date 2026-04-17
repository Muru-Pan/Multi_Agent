import pytest

from app.agents.planner import PlannerAgent
from app.config.settings import Settings
from app.models.schemas import AgentType


class StubRouter:
    def __init__(self, text: str) -> None:
        self.text = text

    async def generate(self, prompt: str, *, fallback_text: str | None = None):
        class Response:
            def __init__(self, text: str) -> None:
                self.text = text

        return Response(self.text)


@pytest.mark.asyncio
async def test_planner_falls_back_on_bad_json():
    planner = PlannerAgent(Settings(), StubRouter("not-json"))
    plan = await planner.plan("task-1", "research coding models")
    assert len(plan.steps) == 2
    assert plan.steps[-1].agent is AgentType.WRITER
    assert plan.steps[-1].critical is True


@pytest.mark.asyncio
async def test_planner_accepts_valid_json():
    planner = PlannerAgent(
        Settings(),
        StubRouter(
            """
            {
              "task_id": "task-1",
              "steps": [
                {"id": "s1", "agent": "retriever", "input": "x", "depends_on": [], "critical": false, "max_retries": 1},
                {"id": "s2", "agent": "writer", "input": "y", "depends_on": ["s1"], "critical": true, "max_retries": 1}
              ]
            }
            """
        ),
    )
    plan = await planner.plan("task-1", "research coding models")
    assert [step.id for step in plan.steps] == ["s1", "s2"]
