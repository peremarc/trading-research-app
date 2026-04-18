from app.providers.market_data.ibkr_realtime_client import IBKRRealtimeClient, SSEEventEnvelope


def test_ibkr_realtime_client_extracts_quote_from_direct_payload() -> None:
    client = IBKRRealtimeClient(base_url="https://example.test")
    envelope = SSEEventEnvelope(
        event="market",
        data={"conid": "265598", "31": "270.75", "84": "270.50", "86": "270.80"},
        raw_data='{"conid":"265598","31":"270.75","84":"270.50","86":"270.80"}',
        received_at="2026-04-18T18:00:00+00:00",
    )

    quote = client.extract_quote(envelope)

    assert quote is not None
    assert quote.conid == "265598"
    assert quote.last_price == 270.75
    assert quote.bid_price == 270.50
    assert quote.ask_price == 270.80


def test_ibkr_realtime_client_extracts_quote_from_nested_upstream_payload() -> None:
    client = IBKRRealtimeClient(base_url="https://example.test")
    envelope = SSEEventEnvelope(
        event="system",
        data={"type": "upstream_message", "data": {"topic": "smd+265598", "31": "271.10", "84": "271.00", "86": "271.20"}},
        raw_data='{"type":"upstream_message","data":{"topic":"smd+265598","31":"271.10","84":"271.00","86":"271.20"}}',
        received_at="2026-04-18T18:00:00+00:00",
    )

    quote = client.extract_quote(envelope)

    assert quote is not None
    assert quote.conid == "265598"
    assert quote.topic == "smd+265598"
    assert quote.last_price == 271.10
