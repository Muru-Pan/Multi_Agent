from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from app.config.settings import Settings

logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    def __init__(self, provider: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


@dataclass
class LLMResponse:
    provider: str
    text: str


class BaseProvider:
    name: str

    async def generate(self, prompt: str) -> str:
        raise NotImplementedError


class GroqProvider(BaseProvider):
    name = "groq"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def generate(self, prompt: str) -> str:
        if not self.api_key:
            raise LLMProviderError(self.name, "missing api key")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise LLMProviderError(self.name, response.text, response.status_code)
        data = response.json()
        return data["choices"][0]["message"]["content"]


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def generate(self, prompt: str) -> str:
        if not self.api_key:
            raise LLMProviderError(self.name, "missing api key")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            raise LLMProviderError(self.name, response.text, response.status_code)
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


class TogetherProvider(BaseProvider):
    name = "together"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def generate(self, prompt: str) -> str:
        if not self.api_key:
            raise LLMProviderError(self.name, "missing api key")
        url = "https://api.together.xyz/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise LLMProviderError(self.name, response.text, response.status_code)
        data = response.json()
        return data["choices"][0]["message"]["content"]


class LLMRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.providers: list[BaseProvider] = [
            GroqProvider(settings.groq_api_key),
            GeminiProvider(settings.gemini_api_key),
            TogetherProvider(settings.together_api_key),
        ]

    async def generate(self, prompt: str, *, fallback_text: str | None = None) -> LLMResponse:
        last_error: LLMProviderError | None = None
        for provider in self.providers:
            for attempt in range(1, self.settings.max_retries + 1):
                try:
                    text = await provider.generate(prompt)
                    return LLMResponse(provider=provider.name, text=text)
                except LLMProviderError as exc:
                    last_error = exc
                    retryable = exc.status_code is None or exc.status_code == 429 or exc.status_code >= 500
                    logger.warning(
                        "provider attempt failed",
                        extra={"provider": provider.name, "attempt": attempt, "status_code": exc.status_code},
                    )
                    if not retryable:
                        break
                    await asyncio.sleep((2 ** (attempt - 1)) + random.random())
        if fallback_text is not None:
            return LLMResponse(provider="local-fallback", text=fallback_text)
        raise last_error or LLMProviderError("unknown", "no providers configured")

    async def stream(self, prompt: str, *, fallback_text: str | None = None) -> AsyncIterator[tuple[str, str]]:
        response = await self.generate(prompt, fallback_text=fallback_text)
        for token in response.text.split():
            yield response.provider, token + " "


def json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=True, indent=2)
