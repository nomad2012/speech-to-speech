import pytest
from examples import realtime_web_search_tool


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeAsyncClient:
    def __init__(self, response, captured, **_kwargs):
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        self.captured.update(url=url, **kwargs)
        return self.response


@pytest.mark.asyncio
async def test_searxng_search_returns_results(monkeypatch):
    captured = {}
    response = FakeResponse(
        200,
        {
            "answers": ["21°C"],
            "results": [
                {"title": "Forecast", "content": "Sunny", "url": "https://example.com/weather"},
            ],
        },
    )
    monkeypatch.setattr(realtime_web_search_tool, "SEARXNG_URL", "http://fw16:8888")
    monkeypatch.setattr(
        realtime_web_search_tool.httpx,
        "AsyncClient",
        lambda **kwargs: FakeAsyncClient(response, captured, **kwargs),
    )

    result = await realtime_web_search_tool.execute_tool("web_search", {"query": "weather in Bern"})

    assert result.output["answer"] == "21°C"
    assert result.output["results"] == [{"title": "Forecast", "snippet": "Sunny", "url": "https://example.com/weather"}]
    assert "url" in captured
    assert "weather+in+Bern" in captured["url"] or "q=weather+in+Bern" in captured["url"] or "q=weather%2Bin%2BBern" in captured["url"]


@pytest.mark.asyncio
async def test_searxng_search_reports_missing_url(monkeypatch):
    monkeypatch.setattr(realtime_web_search_tool, "SEARXNG_URL", "")

    result = await realtime_web_search_tool.execute_tool("web_search", {"query": "latest news"})

    assert result.output == {
        "query": "latest news",
        "error": "SEARXNG_URL is not set.",
    }
