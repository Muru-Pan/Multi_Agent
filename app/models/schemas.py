from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskLifecycle(str, Enum):
    RECEIVED = "RECEIVED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    STREAMING = "STREAMING"
    COMPLETE = "COMPLETE"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"
    RETRYING = "RETRYING"


class AgentType(str, Enum):
    RETRIEVER = "retriever"
    WRITER = "writer"


class TaskCreateRequest(BaseModel):
    task: str = Field(min_length=1, max_length=2000)

    @field_validator("task")
    @classmethod
    def normalize_task(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("task must not be empty")
        return trimmed


class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskLifecycle
    stream_url: str
    status_url: str


class PlanStep(BaseModel):
    id: str
    agent: AgentType
    input: str
    depends_on: list[str] = Field(default_factory=list)
    critical: bool = False
    timeout_seconds: int | None = None
    max_retries: int = 2


class TaskPlan(BaseModel):
    task_id: str
    steps: list[PlanStep]

    @model_validator(mode="after")
    def validate_references(self) -> "TaskPlan":
        step_ids = {step.id for step in self.steps}
        if len(step_ids) != len(self.steps):
            raise ValueError("step ids must be unique")
        for step in self.steps:
            unknown = set(step.depends_on) - step_ids
            if unknown:
                raise ValueError(f"unknown step dependency found: {unknown}")
        return self


class StepResult(BaseModel):
    step_id: str
    agent: AgentType
    status: StepStatus
    output: dict[str, Any] | None = None
    error: str | None = None
    attempt: int = 1
    provider_used: str | None = None
    started_at: str = Field(default_factory=utc_now)
    finished_at: str = Field(default_factory=utc_now)


class StepState(BaseModel):
    step_id: str
    agent: AgentType
    status: StepStatus = StepStatus.PENDING
    attempt: int = 0
    critical: bool = False
    result_preview: str | None = None
    error: str | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskLifecycle
    user_task: str
    steps: list[StepState]
    partial_result: str | None = None
    final_result: str | None = None
    error: str | None = None


class QueueTaskMessage(BaseModel):
    task_id: str
    step_id: str
    agent: AgentType
    input: str
    user_task: str
    depends_on: list[str] = Field(default_factory=list)
    prior_results: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 1
    critical: bool = False
    timeout_seconds: int | None = None


class QueueResultMessage(BaseModel):
    task_id: str
    step_id: str
    result: StepResult


class EventEnvelope(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    event: str
    data: dict[str, Any]
    created_at: str = Field(default_factory=utc_now)
