from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.config.settings import Settings
from app.llm.router import LLMRouter
from app.models.schemas import QueueTaskMessage, StepResult, StepStatus


class WriterAgent:
    def __init__(self, settings: Settings, router: LLMRouter) -> None:
        self.settings = settings
        self.router = router

    async def execute(
        self,
        message: QueueTaskMessage,
        on_token: Callable[[str], Awaitable[None]],
    ) -> StepResult:
        prompt = (
            "You are the writer agent in a multi-agent system.\n"
            "Produce a concise, evidence-aware response.\n"
            "If retrieval is empty, state the limitation.\n\n"
            f"Original task:\n{message.user_task}\n\n"
            f"Prior step results:\n{message.prior_results}"
        )
        fallback = self._build_fallback(message)
        collected: list[str] = []
        provider_name = "local-fallback"
        async for provider, token in self.router.stream(prompt, fallback_text=fallback):
            provider_name = provider
            collected.append(token)
            await on_token(token)
        text = "".join(collected).strip()
        return StepResult(
            step_id=message.step_id,
            agent=message.agent,
            status=StepStatus.DONE,
            output={"summary": text[:200], "text": text},
            attempt=message.attempt,
            provider_used=provider_name,
        )

    def _build_fallback(self, message: QueueTaskMessage) -> str:
        documents = []
        for result in message.prior_results.values():
            for document in result.get("documents", []):
                documents.append(document[:300])
        if not documents:
            return (
                "I could not retrieve outside context, so this answer is based on the task alone. "
                f"Requested task: {message.user_task}"
            )
        snippets = " ".join(documents[:2])
        return f"Using the retrieved context, here is a synthesized answer: {snippets}"
