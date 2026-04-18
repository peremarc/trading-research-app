from app.domains.learning import api as learning_api
from app.providers.web_research import WebPage, WebResearchError, WebSearchResult


class StubWebResearchService:
    def search(self, query: str, *, max_results: int | None = None, domains: list[str] | None = None) -> list[WebSearchResult]:
        del max_results
        del domains
        return [
            WebSearchResult(
                title=f"{query} outlook improves",
                url="https://reuters.com/markets/nvda-outlook",
                snippet="Sample search snippet",
                source="stub",
            )
        ]

    def fetch_article(self, url: str, *, max_chars: int | None = None) -> WebPage:
        del max_chars
        return WebPage(
            url=url,
            title="NVDA outlook improves",
            text="Revenue expectations improved after stronger AI demand.",
            source="stub",
        )


class RejectingWebResearchService:
    def search(self, query: str, *, max_results: int | None = None, domains: list[str] | None = None) -> list[WebSearchResult]:
        del query
        del max_results
        del domains
        raise WebResearchError("Requested domains are outside the allowed web research policy: example.com")

    def fetch_article(self, url: str, *, max_chars: int | None = None) -> WebPage:
        del url
        del max_chars
        raise WebResearchError("URL domain is not allowed by web research policy.")


def test_agent_tools_catalog_exposes_core_tools(client) -> None:
    response = client.get("/api/v1/agent-tools")

    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert "market.get_snapshot" in names
    assert "market.get_chart" in names
    assert "market.get_multitimeframe_context" in names
    assert "web.search" in names
    assert "web.fetch_article" in names
    assert "macro.get_context" in names
    assert "calendar.get_ticker_events" in names
    assert "calendar.get_macro_events" in names
    assert "positions.open" in names
    assert "positions.manage" in names
    assert "positions.close" in names
    assert "strategies.list_pipelines" in names


def test_agent_tool_gateway_can_open_manage_and_list_positions(client) -> None:
    opened = client.post(
        "/api/v1/agent-tools/execute",
        json={
            "tool_name": "positions.open",
            "arguments": {
                "ticker": "NVDA",
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 112,
                "size": 1,
                "thesis": "Tool-opened trade",
                "opening_reason": "Explicit tool invocation",
            },
        },
    )
    assert opened.status_code == 200
    position = opened.json()["result"]
    assert position["ticker"] == "NVDA"
    assert position["events"][0]["event_type"] == "open"

    managed = client.post(
        "/api/v1/agent-tools/execute",
        json={
            "tool_name": "positions.manage",
            "arguments": {
                "position_id": position["id"],
                "event_type": "risk_update",
                "observed_price": 104,
                "stop_price": 99,
                "target_price": 118,
                "rationale": "Momentum confirmation after breakout retest",
                "management_context": {"source": "agent_tool_test"},
            },
        },
    )
    assert managed.status_code == 200
    managed_position = managed.json()["result"]
    assert managed_position["stop_price"] == 99
    assert managed_position["target_price"] == 118
    assert managed_position["events"][-1]["event_type"] == "risk_update"

    listed = client.post(
        "/api/v1/agent-tools/execute",
        json={"tool_name": "positions.list_open", "arguments": {}},
    )
    assert listed.status_code == 200
    assert len(listed.json()["result"]["positions"]) == 1
    assert listed.json()["result"]["positions"][0]["id"] == position["id"]

    journal = client.get("/api/v1/journal")
    assert journal.status_code == 200
    tool_calls = [entry for entry in journal.json() if entry["entry_type"] == "agent_tool_call"]
    assert len(tool_calls) >= 3
    assert any(entry["decision"] == "positions.open" for entry in tool_calls)
    assert any(entry["decision"] == "positions.manage" for entry in tool_calls)
    assert any(entry["decision"] == "positions.list_open" for entry in tool_calls)


def test_agent_tool_gateway_rejects_unknown_tools(client) -> None:
    response = client.post(
        "/api/v1/agent-tools/execute",
        json={"tool_name": "unknown.tool", "arguments": {}},
    )

    assert response.status_code == 400
    assert "Unsupported tool" in response.json()["detail"]


def test_agent_tool_gateway_can_return_chart_and_multitimeframe_context(client) -> None:
    chart = client.post(
        "/api/v1/agent-tools/execute",
        json={
            "tool_name": "market.get_chart",
            "arguments": {"ticker": "NVDA", "timeframe": "1y"},
        },
    )
    context = client.post(
        "/api/v1/agent-tools/execute",
        json={
            "tool_name": "market.get_multitimeframe_context",
            "arguments": {"ticker": "NVDA", "timeframes": ["1m", "3m", "6m", "1y", "5y"]},
        },
    )

    assert chart.status_code == 200
    assert chart.json()["result"]["ticker"] == "NVDA"
    assert chart.json()["result"]["timeframe"] == "1Y"
    assert "<svg" in chart.json()["result"]["chart_svg"]

    assert context.status_code == 200
    charts = context.json()["result"]["charts"]
    assert [item["timeframe"] for item in charts] == ["1M", "3M", "6M", "1Y", "5Y"]
    assert all("<svg" in item["chart_svg"] for item in charts)


def test_agent_tool_gateway_can_search_and_fetch_web_content(client) -> None:
    original = learning_api.agent_tool_gateway_service.web_research_service
    learning_api.agent_tool_gateway_service.web_research_service = StubWebResearchService()
    try:
        search = client.post(
            "/api/v1/agent-tools/execute",
            json={
                "tool_name": "web.search",
                "arguments": {
                    "query": "NVDA earnings outlook",
                    "domains": ["reuters.com"],
                    "max_results": 3,
                },
            },
        )
        fetch = client.post(
            "/api/v1/agent-tools/execute",
            json={
                "tool_name": "web.fetch_article",
                "arguments": {
                    "url": "https://reuters.com/markets/nvda-outlook",
                    "max_chars": 5000,
                },
            },
        )
    finally:
        learning_api.agent_tool_gateway_service.web_research_service = original

    assert search.status_code == 200
    assert search.json()["result"]["results"][0]["title"] == "NVDA earnings outlook outlook improves"
    assert search.json()["result"]["results"][0]["url"] == "https://reuters.com/markets/nvda-outlook"

    assert fetch.status_code == 200
    assert fetch.json()["result"]["title"] == "NVDA outlook improves"
    assert "AI demand" in fetch.json()["result"]["text"]


def test_agent_tool_gateway_rejects_disallowed_web_targets(client) -> None:
    original = learning_api.agent_tool_gateway_service.web_research_service
    learning_api.agent_tool_gateway_service.web_research_service = RejectingWebResearchService()
    try:
        search = client.post(
            "/api/v1/agent-tools/execute",
            json={
                "tool_name": "web.search",
                "arguments": {"query": "NVDA", "domains": ["example.com"]},
            },
        )
        fetch = client.post(
            "/api/v1/agent-tools/execute",
            json={
                "tool_name": "web.fetch_article",
                "arguments": {"url": "https://example.com/article"},
            },
        )
    finally:
        learning_api.agent_tool_gateway_service.web_research_service = original

    assert search.status_code == 400
    assert "allowed web research policy" in search.json()["detail"]
    assert fetch.status_code == 400
    assert "not allowed" in fetch.json()["detail"]


def test_macro_context_can_be_recorded_and_consumed_by_chat_and_tools(client) -> None:
    created = client.post(
        "/api/v1/macro/signals",
        json={
            "key": "fed_higher_for_longer",
            "content": "La Fed mantiene un sesgo restrictivo y eso presiona growth de larga duración.",
            "regime": "hawkish_rates",
            "relevance": "equities",
            "tickers": ["QQQ", "NVDA"],
            "timeframe": "4w",
            "scenario": "multiple compression if CPI stays sticky",
            "source": "manual_research",
            "importance": 0.9,
        },
    )
    assert created.status_code == 201
    assert created.json()["key"] == "fed_higher_for_longer"

    context = client.get("/api/v1/macro/context")
    assert context.status_code == 200
    payload = context.json()
    assert "hawkish_rates" in payload["active_regimes"]
    assert any(signal["key"] == "fed_higher_for_longer" for signal in payload["signals"])

    tool = client.post(
        "/api/v1/agent-tools/execute",
        json={"tool_name": "macro.get_context", "arguments": {"limit": 5}},
    )
    assert tool.status_code == 200
    assert "hawkish_rates" in tool.json()["result"]["active_regimes"]

    chat = client.post("/api/v1/chat", json={"message": "cual es el contexto macro actual"})
    assert chat.status_code == 200
    assert chat.json()["topic"] == "macro"
    assert "fed_higher_for_longer" in chat.json()["reply"]
