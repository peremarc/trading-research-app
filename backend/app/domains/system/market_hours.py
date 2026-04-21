from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


US_EQUITIES_TIMEZONE = "America/New_York"
US_REGULAR_OPEN = time(9, 30)
US_REGULAR_CLOSE = time(16, 0)


@dataclass(frozen=True)
class MarketSessionState:
    market: str
    timezone: str
    session_label: str
    is_weekend: bool
    is_trading_day: bool
    is_regular_session_open: bool
    is_extended_hours: bool
    now_utc: str
    now_local: str
    next_regular_open: str | None
    next_regular_close: str | None

    def to_payload(self) -> dict:
        return {
            "market": self.market,
            "timezone": self.timezone,
            "session_label": self.session_label,
            "is_weekend": self.is_weekend,
            "is_trading_day": self.is_trading_day,
            "is_regular_session_open": self.is_regular_session_open,
            "is_extended_hours": self.is_extended_hours,
            "now_utc": self.now_utc,
            "now_local": self.now_local,
            "next_regular_open": self.next_regular_open,
            "next_regular_close": self.next_regular_close,
        }


class USMarketHoursService:
    def __init__(self, timezone_name: str = US_EQUITIES_TIMEZONE) -> None:
        self.timezone_name = timezone_name
        self._timezone = ZoneInfo(timezone_name)

    def get_session_state(self, *, now: datetime | None = None) -> MarketSessionState:
        current_utc = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        current_local = current_utc.astimezone(self._timezone)
        current_time = current_local.timetz().replace(tzinfo=None)
        is_weekend = current_local.weekday() >= 5
        is_trading_day = not is_weekend

        if is_weekend:
            session_label = "weekend"
            is_regular_session_open = False
            is_extended_hours = False
        elif US_REGULAR_OPEN <= current_time < US_REGULAR_CLOSE:
            session_label = "regular"
            is_regular_session_open = True
            is_extended_hours = False
        elif current_time < US_REGULAR_OPEN:
            session_label = "pre_market"
            is_regular_session_open = False
            is_extended_hours = True
        else:
            session_label = "after_hours"
            is_regular_session_open = False
            is_extended_hours = True

        next_open = self._next_regular_open(current_local)
        next_close = self._next_regular_close(current_local)
        return MarketSessionState(
            market="us_equities",
            timezone=self.timezone_name,
            session_label=session_label,
            is_weekend=is_weekend,
            is_trading_day=is_trading_day,
            is_regular_session_open=is_regular_session_open,
            is_extended_hours=is_extended_hours,
            now_utc=current_utc.isoformat(),
            now_local=current_local.isoformat(),
            next_regular_open=next_open.astimezone(timezone.utc).isoformat() if next_open is not None else None,
            next_regular_close=next_close.astimezone(timezone.utc).isoformat() if next_close is not None else None,
        )

    def _next_regular_open(self, current_local: datetime) -> datetime:
        candidate_date = current_local.date()
        current_time = current_local.timetz().replace(tzinfo=None)
        if current_local.weekday() < 5 and current_time < US_REGULAR_OPEN:
            return datetime.combine(candidate_date, US_REGULAR_OPEN, tzinfo=self._timezone)

        candidate_date += timedelta(days=1)
        while candidate_date.weekday() >= 5:
            candidate_date += timedelta(days=1)
        return datetime.combine(candidate_date, US_REGULAR_OPEN, tzinfo=self._timezone)

    def _next_regular_close(self, current_local: datetime) -> datetime | None:
        if current_local.weekday() >= 5:
            return None
        current_time = current_local.timetz().replace(tzinfo=None)
        if current_time < US_REGULAR_CLOSE:
            return datetime.combine(current_local.date(), US_REGULAR_CLOSE, tzinfo=self._timezone)
        return None
