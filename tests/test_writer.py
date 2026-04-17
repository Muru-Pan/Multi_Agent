import pytest

from app.agents.writer import WriterAgent
from app.config.settings import Settings
from app.models.schemas import AgentType, QueueTaskMessage


class StubRouter:
    async def stream(self, prompt: str, *, fallback_text: str | None = None):
        for token in ["Hello ", "world "]:
            yield "stub", token


@pytest.mark.asyncio
async def test_writer_streams_tokens_and_builds_result():
    writer = WriterAgent(Settings(), StubRouter())
    tokens: list[str] = []
    result = await writer.execute(
        QueueTaskMessage(
            task_id="task-1",
            step_id="step-2",
            agent=AgentType.WRITER,
            input="write",
            user_task="answer this",
            prior_results={"step-1": {"documents": ["doc"]}},
        ),
        on_token=lambda token: _collect(tokens, token),
    )
    assert tokens == ["Hello ", "world "]
    assert result.output["text"] == "Hello world"


async def _collect(tokens: list[str], token: str):
    tokens.append(token)
