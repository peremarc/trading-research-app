from app.domains.learning import api as learning_api
from app.domains.market import api as market_api
from app.providers.calendar import CalendarEvent


class StubCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                event_type="earnings",
                title=f"Earnings {ticker}",
                event_date="2026-04-24",
                ticker=ticker,
                source="stub",
            )
        ]

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                event_type="macro",
                title="US CPI",
                event_date="2026-04-20",
                country="US",
                impact="high",
                source="stub",
            )
        ]


def test_calendar_endpoints_return_stubbed_events(client) -> None:
    original = market_api.calendar_service
    market_api.calendar_service = StubCalendarService()
    try:
        corporate = client.get("/api/v1/calendar/corporate/NVDA")
        macro = client.get("/api/v1/calendar/macro")
    finally:
        market_api.calendar_service = original

    assert corporate.status_code == 200
    assert corporate.json()[0]["title"] == "Earnings NVDA"
    assert macro.status_code == 200
    assert macro.json()[0]["title"] == "US CPI"


def test_chat_and_tools_can_use_calendar_context(client) -> None:
    original_chat = learning_api.bot_chat_service.calendar_service
    original_tool = learning_api.agent_tool_gateway_service.calendar_service
    stub = StubCalendarService()
    learning_api.bot_chat_service.calendar_service = stub
    learning_api.agent_tool_gateway_service.calendar_service = stub
    try:
        chat = client.post("/api/v1/chat", json={"message": "proximos earnings de NVDA"})
        tool = client.post(
            "/api/v1/agent-tools/execute",
            json={"tool_name": "calendar.get_ticker_events", "arguments": {"ticker": "NVDA"}},
        )
        macro_tool = client.post(
            "/api/v1/agent-tools/execute",
            json={"tool_name": "calendar.get_macro_events", "arguments": {"days_ahead": 7}},
        )
    finally:
        learning_api.bot_chat_service.calendar_service = original_chat
        learning_api.agent_tool_gateway_service.calendar_service = original_tool

    assert chat.status_code == 200
    assert chat.json()["topic"] == "calendar"
    assert "Earnings NVDA" in chat.json()["reply"]

    assert tool.status_code == 200
    assert tool.json()["result"]["events"][0]["ticker"] == "NVDA"

    assert macro_tool.status_code == 200
    assert macro_tool.json()["result"]["events"][0]["title"] == "US CPI"
