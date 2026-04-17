from dataclasses import dataclass
from typing import Protocol


@dataclass
class OHLCVCandle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketSnapshot:
    ticker: str
    price: float
    sma_20: float
    sma_50: float
    sma_200: float
    rsi_14: float
    relative_volume: float
    atr_14: float
    week_performance: float
    month_performance: float


class MarketDataProvider(Protocol):
    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        raise NotImplementedError

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        raise NotImplementedError
