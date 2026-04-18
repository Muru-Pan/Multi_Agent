from __future__ import annotations

from collections.abc import Awaitable, Callable
import re

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
        evidence = self._prepare_evidence(message)
        prompt = (
            "You are the writer agent in a multi-agent system.\n"
            "Your job is to produce a clean technical comparison and recommendation.\n"
            "Write in plain, professional language.\n"
            "Do not copy raw webpage text, navigation text, or marketing copy.\n"
            "Do not use inline source names like '(Java World)' unless the user explicitly asks for citations.\n"
            "Keep the answer concise and structured.\n"
            "Return clean markdown only.\n"
            "Use short sections and bullet points.\n"
            "Use this format:\n"
            "## Overview\n"
            "<1-2 short sentences>\n"
            "## Comparison\n"
            "- **Option name**: strengths and weaknesses\n"
            "- **Option name**: strengths and weaknesses\n"
            "- **Option name**: strengths and weaknesses if relevant\n"
            "## Recommendation\n"
            "- **Best choice**: <option>\n"
            "- **Why**: <short justification>\n"
            "If evidence is incomplete, add one short limitation line at the end.\n\n"
            f"Original task:\n{message.user_task}\n\n"
            f"Prepared evidence:\n{evidence}"
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
        evidence = self._prepare_evidence(message)
        if evidence == "No external evidence available.":
            return (
                "## Overview\n"
                "I could not retrieve enough outside evidence for a strong comparison.\n\n"
                "## Recommendation\n"
                "- **Best choice**: Use the option with the simplest setup and lowest operational overhead for your MVP.\n"
                "- **Why**: Without reliable external evidence, the safest choice is usually the simplest tool that can satisfy current needs.\n\n"
                f"Limitation: This response is based on the task alone. Requested task: {message.user_task}"
            )
        return (
            "## Overview\n"
            "Based on the available evidence, here is a concise comparison.\n\n"
            "## Comparison\n"
            f"{evidence}\n\n"
            "## Recommendation\n"
            "- **Best choice**: Choose the option that best balances simplicity, reliability, and current scale.\n"
            "- **Why**: For an MVP, lower complexity and faster delivery usually matter more than maximum theoretical scale."
        )

    def _prepare_evidence(self, message: QueueTaskMessage) -> str:
        snippets: list[str] = []
        for result in message.prior_results.values():
            for document in result.get("documents", []):
                cleaned = self._clean_document(document)
                if cleaned:
                    snippets.append(cleaned)
        if not snippets:
            return "No external evidence available."
        return "\n".join(f"- {snippet}" for snippet in snippets[:3])

    def _clean_document(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        noise_patterns = [
            r"Skip to content",
            r"Navigation menu",
            r"Log in",
            r"Create account",
            r"Pricing",
            r"Contact us",
            r"Try for free",
            r"Book a Demo",
            r"All rights reserved",
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        useful = [sentence.strip() for sentence in sentences if 30 <= len(sentence.strip()) <= 240]
        useful = [sentence for sentence in useful if sentence and not sentence.lower().startswith(("home ", "blog ", "search "))]
        return " ".join(useful[:2]).strip()
