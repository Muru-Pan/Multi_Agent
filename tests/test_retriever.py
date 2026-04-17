from app.agents.retriever import RetrieverAgent
from app.config.settings import Settings


class DummyResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_retriever_html_fallback_parses_urls(monkeypatch):
    agent = RetrieverAgent(Settings(max_search_results=2))
    html = """
    <html>
      <body>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle-1&rut=1">One</a>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Farticle-2&rut=2">Two</a>
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.net%2Farticle-3&rut=3">Three</a>
      </body>
    </html>
    """

    monkeypatch.setattr(agent, "_search_urls_from_library", lambda query: [])
    monkeypatch.setattr("app.agents.retriever.httpx.get", lambda *args, **kwargs: DummyResponse(html))

    urls = agent._search_urls("coding llms")

    assert urls == ["https://example.com/article-1", "https://example.org/article-2"]
