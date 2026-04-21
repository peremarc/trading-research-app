from app.domains.learning import api as learning_api
from app.providers.macro_indicators import MacroIndicatorError, MacroIndicatorSnapshot, MacroIndicatorsService


class _FakeMacroIndicatorsService:
    def list_indicators(self) -> list[dict]:
        return [
            {
                "key": "fear_greed",
                "label": "CNN Fear & Greed",
                "value": 21.0,
                "unit": "score",
                "previous_value": 28.0,
                "change": -7.0,
                "change_pct": -25.0,
                "as_of": "2026-04-19T10:00:00+00:00",
                "source": "cnn",
                "status": "available",
                "interpretation": "Extreme Fear",
                "detail": "Sentimiento agregado de mercado",
            },
            {
                "key": "vix",
                "label": "VIX",
                "value": 24.4,
                "unit": "pts",
                "previous_value": 21.1,
                "change": 3.3,
                "change_pct": 15.6,
                "as_of": "2026-04-19T10:00:00+00:00",
                "source": "yahoo_finance",
                "status": "available",
                "interpretation": "risk_off",
                "detail": "Volatilidad implicita del S&P 500",
            },
            {
                "key": "us10y",
                "label": "US 10Y",
                "value": 4.42,
                "unit": "%",
                "previous_value": 4.36,
                "change": 0.06,
                "change_pct": 1.38,
                "as_of": "2026-04-18T00:00:00+00:00",
                "source": "us_treasury",
                "status": "available",
                "interpretation": "neutral_rates",
                "detail": "Cierre oficial del Treasury a 10 anos",
            },
            {
                "key": "gold",
                "label": "Gold",
                "value": 4879.6,
                "unit": "USD",
                "previous_value": 4825.0,
                "change": 54.6,
                "change_pct": 1.13,
                "as_of": "2026-04-19T10:00:00+00:00",
                "source": "yahoo_finance",
                "status": "available",
                "interpretation": "neutral",
                "detail": "Futuros del oro como termometro de refugio e inflacion",
            },
            {
                "key": "oil",
                "label": "WTI Crude",
                "value": 82.59,
                "unit": "USD",
                "previous_value": 91.28,
                "change": -8.69,
                "change_pct": -9.52,
                "as_of": "2026-04-19T10:00:00+00:00",
                "source": "yahoo_finance",
                "status": "available",
                "interpretation": "demand_cooling",
                "detail": "Futuros del petroleo WTI como proxy de energia e inflacion",
            },
            {
                "key": "copper",
                "label": "Copper",
                "value": 6.1145,
                "unit": "USD",
                "previous_value": 6.0705,
                "change": 0.044,
                "change_pct": 0.72,
                "as_of": "2026-04-19T10:00:00+00:00",
                "source": "yahoo_finance",
                "status": "available",
                "interpretation": "neutral",
                "detail": "Futuros del cobre como proxy de ciclo industrial y crecimiento",
            },
        ]


def test_macro_context_exposes_structured_macro_indicators(client) -> None:
    original = learning_api.macro_context_service.indicators_service
    learning_api.macro_context_service.indicators_service = _FakeMacroIndicatorsService()
    try:
        response = client.get("/api/v1/macro/context")
    finally:
        learning_api.macro_context_service.indicators_service = original

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["indicators"]) == 6
    assert payload["indicators"][0]["key"] == "fear_greed"
    assert payload["indicators"][1]["label"] == "VIX"
    assert payload["indicators"][2]["source"] == "us_treasury"
    assert payload["indicators"][3]["key"] == "gold"
    assert payload["indicators"][4]["label"] == "WTI Crude"
    assert payload["indicators"][5]["key"] == "copper"
    assert "CNN Fear & Greed" in payload["summary"]
    assert "VIX" in payload["summary"]
    assert "Gold" in payload["summary"]


class _FailingTreasuryProvider:
    def fetch_latest_10y(self) -> MacroIndicatorSnapshot:
        raise MacroIndicatorError("no official treasury rows")


class _YahooTNXProvider:
    def fetch_index_snapshot(self, *, symbol: str, key: str, label: str, detail: str | None = None) -> MacroIndicatorSnapshot:
        assert symbol == "^TNX"
        assert key == "us10y"
        return MacroIndicatorSnapshot(
            key=key,
            label=label,
            value=4.25,
            unit="pts",
            previous_value=4.30,
            change=-0.05,
            change_pct=-1.16,
            as_of="2026-04-19T10:00:00+00:00",
            source="yahoo_finance",
            detail=detail,
        )


def test_macro_indicators_service_falls_back_to_yahoo_for_us10y() -> None:
    service = MacroIndicatorsService(
        fear_greed_provider=None,
        yahoo_provider=_YahooTNXProvider(),
        treasury_provider=_FailingTreasuryProvider(),
    )
    payload = service._build_us10y_indicator()

    assert payload["source"] == "yahoo_finance"
    assert payload["value"] == 4.25
    assert payload["unit"] == "%"
    assert payload["interpretation"] == "neutral_rates"
    assert "fallback tras Treasury" in payload["detail"]


class _CommodityYahooProvider:
    def fetch_index_snapshot(self, *, symbol: str, key: str, label: str, detail: str | None = None, unit: str | None = "pts") -> MacroIndicatorSnapshot:
        values = {
            "GC=F": (4879.6, 4825.0),
            "CL=F": (82.59, 91.28),
            "HG=F": (6.1145, 6.0705),
        }
        current, previous = values[symbol]
        return MacroIndicatorSnapshot(
            key=key,
            label=label,
            value=current,
            unit=unit,
            previous_value=previous,
            change=current - previous,
            change_pct=((current - previous) / previous) * 100.0,
            as_of="2026-04-19T10:00:00+00:00",
            source="yahoo_finance",
            detail=detail,
        )


def test_macro_indicators_service_builds_commodity_indicators() -> None:
    service = MacroIndicatorsService(
        fear_greed_provider=None,
        yahoo_provider=_CommodityYahooProvider(),
        treasury_provider=_FailingTreasuryProvider(),
    )

    gold = service._build_gold_indicator()
    oil = service._build_oil_indicator()
    copper = service._build_copper_indicator()

    assert gold["unit"] == "USD"
    assert gold["key"] == "gold"
    assert oil["interpretation"] == "demand_cooling"
    assert copper["key"] == "copper"
    assert copper["source"] == "yahoo_finance"
