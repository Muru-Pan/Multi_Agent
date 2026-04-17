from __future__ import annotations

import ipaddress
import logging
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config.settings import Settings
from app.models.schemas import QueueTaskMessage, StepResult, StepStatus

try:
    from duckduckgo_search import DDGS
except ImportError:  # pragma: no cover
    DDGS = None

logger = logging.getLogger(__name__)


class RetrieverAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, message: QueueTaskMessage) -> StepResult:
        documents: list[str] = []
        summary = "No external documents retrieved."
        urls = self._search_urls(message.input)
        async with httpx.AsyncClient(timeout=self.settings.fetch_timeout_seconds, follow_redirects=True) as client:
            for url in urls[: self.settings.max_fetched_pages]:
                if not self._is_safe_url(url):
                    continue
                try:
                    response = await client.get(url, headers={"User-Agent": "agentic-ai-backend/1.0"})
                    content_type = response.headers.get("content-type", "")
                    if "text/html" not in content_type:
                        continue
                    soup = BeautifulSoup(response.text, "html.parser")
                    text = " ".join(soup.stripped_strings)
                    cleaned = text[:4000]
                    if cleaned:
                        documents.append(cleaned)
                except Exception:
                    continue
        if documents:
            summary = f"Retrieved {len(documents)} supporting document(s)."
        return StepResult(
            step_id=message.step_id,
            agent=message.agent,
            status=StepStatus.DONE,
            output={"summary": summary, "documents": documents, "sources": urls[: self.settings.max_fetched_pages]},
            attempt=message.attempt,
        )

    def _search_urls(self, query: str) -> list[str]:
        urls = self._search_urls_from_library(query)
        if urls:
            return urls
        return self._search_urls_from_html(query)

    def _search_urls_from_library(self, query: str) -> list[str]:
        if DDGS is None:
            return []
        urls: list[str] = []
        seen_domains: set[str] = set()
        try:
            with DDGS() as ddgs:
                for item in ddgs.text(query, max_results=self.settings.max_search_results):
                    url = item.get("href")
                    if not url:
                        continue
                    domain = urlparse(url).netloc
                    if domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    urls.append(url)
        except Exception as exc:
            logger.warning("duckduckgo_search library lookup failed", extra={"error": str(exc)})
        return urls

    def _search_urls_from_html(self, query: str) -> list[str]:
        try:
            response = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.settings.search_timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("duckduckgo html fallback failed", extra={"error": str(exc)})
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        urls: list[str] = []
        seen_domains: set[str] = set()
        for anchor in soup.select("a.result__a, a[href]"):
            href = self._normalize_search_result_url(anchor.get("href"))
            if not href or not self._is_safe_url(href):
                continue
            domain = urlparse(href).netloc
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            urls.append(href)
            if len(urls) >= self.settings.max_search_results:
                break
        return urls

    def _normalize_search_result_url(self, href: str | None) -> str | None:
        if not href:
            return None
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            encoded = parse_qs(parsed.query).get("uddg", [])
            if encoded:
                return unquote(encoded[0])
        return href

    def _is_safe_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = parsed.hostname or ""
        if host in {"localhost", "127.0.0.1"}:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_reserved:
                return False
        except ValueError:
            pass
        return True
