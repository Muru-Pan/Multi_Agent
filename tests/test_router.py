import pytest

from app.config.settings import Settings
from app.llm.router import LLMProviderError, LLMRouter


class FailingProvider:
    def __init__(self, name: str, status_code: int | None = None) -> None:
        self.name = name
        self.status_code = status_code

    async def generate(self, prompt: str) -> str:
        raise LLMProviderError(self.name, "boom", self.status_code)


class SuccessProvider:
    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text

    async def generate(self, prompt: str) -> str:
        return self.text


@pytest.mark.asyncio
async def test_router_uses_fallback_text_when_all_providers_fail():
    router = LLMRouter(Settings(max_retries=1))
    router.providers = [FailingProvider("groq"), FailingProvider("gemini"), FailingProvider("together")]
    result = await router.generate("hello", fallback_text="local answer")
    assert result.provider == "local-fallback"
    assert result.text == "local answer"


@pytest.mark.asyncio
async def test_router_switches_provider_after_retryable_error():
    router = LLMRouter(Settings(max_retries=1))
    router.providers = [FailingProvider("groq", 429), SuccessProvider("gemini", "ok")]
    result = await router.generate("hello")
    assert result.provider == "gemini"
    assert result.text == "ok"
