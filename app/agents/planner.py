from __future__ import annotations

import json
from collections import defaultdict, deque

from app.config.settings import Settings
from app.llm.router import LLMRouter, json_dumps
from app.models.schemas import AgentType, PlanStep, TaskPlan


class PlannerAgent:
    def __init__(self, settings: Settings, router: LLMRouter) -> None:
        self.settings = settings
        self.router = router

    async def plan(self, task_id: str, user_task: str) -> TaskPlan:
        fallback_plan = self.default_plan(task_id, user_task)
        prompt = (
            "You are a planning agent for a backend-only multi-agent system.\n"
            "Return JSON only with shape {task_id, steps}.\n"
            "Use only agents: retriever, writer.\n"
            "Each step must contain id, agent, input, depends_on, critical, max_retries.\n"
            f"Max steps: {self.settings.max_task_steps}.\n"
            f"Task:\n{user_task}"
        )
        response = await self.router.generate(prompt, fallback_text=json_dumps(fallback_plan.model_dump(mode="json")))
        try:
            data = json.loads(response.text)
            plan = TaskPlan.model_validate(data)
            self._validate_plan(plan)
            return plan
        except Exception:
            return fallback_plan

    def default_plan(self, task_id: str, user_task: str) -> TaskPlan:
        return TaskPlan(
            task_id=task_id,
            steps=[
                PlanStep(
                    id="step_1",
                    agent=AgentType.RETRIEVER,
                    input=user_task,
                    depends_on=[],
                    critical=False,
                    max_retries=2,
                ),
                PlanStep(
                    id="step_2",
                    agent=AgentType.WRITER,
                    input="Synthesize the retrieved findings into a final answer.",
                    depends_on=["step_1"],
                    critical=True,
                    max_retries=2,
                ),
            ],
        )

    def _validate_plan(self, plan: TaskPlan) -> None:
        if len(plan.steps) > self.settings.max_task_steps:
            raise ValueError("planner exceeded step limit")
        self._assert_acyclic(plan)
        if not any(step.critical for step in plan.steps):
            raise ValueError("plan must contain a critical step")

    def _assert_acyclic(self, plan: TaskPlan) -> None:
        indegree: dict[str, int] = {step.id: 0 for step in plan.steps}
        graph: dict[str, list[str]] = defaultdict(list)
        for step in plan.steps:
            for dep in step.depends_on:
                graph[dep].append(step.id)
                indegree[step.id] += 1
        queue = deque([step_id for step_id, degree in indegree.items() if degree == 0])
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for neighbor in graph[current]:
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)
        if visited != len(plan.steps):
            raise ValueError("planner returned cyclic dependencies")
