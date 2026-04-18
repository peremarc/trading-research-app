from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Thread
import time

from sqlalchemy import select

from app.core.config import Settings
from app.db.models.position import Position
from app.db.session import SessionLocal
from app.domains.execution.services import ExitManagementService
from app.providers.market_data.ibkr_proxy_provider import IBKRProxyProvider
from app.providers.market_data.ibkr_realtime_client import (
    IBKRRealtimeClient,
    IBKRRealtimeQuote,
    IBKRRealtimeStreamError,
)


@dataclass
class RealtimePositionHandle:
    position_id: int
    ticker: str
    side: str
    entry_price: float
    stop_price: float | None
    target_price: float | None


@dataclass
class RealtimeSubscriptionTarget:
    conid: str
    ticker: str
    positions: list[RealtimePositionHandle] = field(default_factory=list)


@dataclass
class RealtimeMonitorRuntimeState:
    enabled: bool
    active: bool = False
    transport: str = "sse"
    subscribed_tickers: list[str] = field(default_factory=list)
    subscribed_conids: list[str] = field(default_factory=list)
    last_connected_at: datetime | None = None
    last_event_at: datetime | None = None
    processed_events: int = 0
    adjusted_positions: int = 0
    closed_positions: int = 0
    reconnect_count: int = 0
    last_error: str | None = None
    last_event_summary: str | None = None


class IBKRRealtimePositionMonitorService:
    def __init__(
        self,
        settings: Settings,
        *,
        exit_management_service: ExitManagementService | None = None,
        realtime_client: IBKRRealtimeClient | None = None,
    ) -> None:
        self.settings = settings
        self.exit_management_service = exit_management_service or ExitManagementService(
            execution_event_source="monitor_stream"
        )
        self.runtime = RealtimeMonitorRuntimeState(
            enabled=bool(
                settings.ibkr_market_monitor_enabled
                and settings.market_data_provider == "ibkr_proxy"
                and settings.ibkr_market_monitor_transport == "sse"
            ),
            transport=settings.ibkr_market_monitor_transport,
        )
        self.realtime_client = realtime_client or IBKRRealtimeClient(
            base_url=settings.ibkr_proxy_base_url,
            api_key=settings.ibkr_proxy_api_key,
            read_timeout_seconds=settings.ibkr_market_monitor_read_timeout_seconds,
        )
        provider = self.exit_management_service.market_data_service.provider
        self.ibkr_provider = provider if isinstance(provider, IBKRProxyProvider) else None
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._last_evaluation_at: dict[str, float] = {}
        self._last_evaluation_price: dict[str, float] = {}

    def start(self) -> dict:
        if not self.runtime.enabled:
            return self.get_status_payload()
        if self.runtime.active and self._thread is not None and self._thread.is_alive():
            return self.get_status_payload()

        self._stop_event.clear()
        self.runtime.active = True
        self.runtime.last_error = None
        self._thread = Thread(target=self._run_loop, name="ibkr-realtime-monitor", daemon=True)
        self._thread.start()
        return self.get_status_payload()

    def stop(self) -> dict:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.runtime.active = False
        self.runtime.subscribed_tickers = []
        self.runtime.subscribed_conids = []
        return self.get_status_payload()

    def get_status_payload(self) -> dict:
        return {
            "enabled": self.runtime.enabled,
            "active": self.runtime.active,
            "transport": self.runtime.transport,
            "subscribed_tickers": list(self.runtime.subscribed_tickers),
            "subscribed_conids": list(self.runtime.subscribed_conids),
            "last_connected_at": self.runtime.last_connected_at.isoformat() if self.runtime.last_connected_at else None,
            "last_event_at": self.runtime.last_event_at.isoformat() if self.runtime.last_event_at else None,
            "processed_events": self.runtime.processed_events,
            "adjusted_positions": self.runtime.adjusted_positions,
            "closed_positions": self.runtime.closed_positions,
            "reconnect_count": self.runtime.reconnect_count,
            "last_error": self.runtime.last_error,
            "last_event_summary": self.runtime.last_event_summary,
        }

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.ibkr_provider is None:
                self.runtime.last_error = "IBKR realtime monitor requires the ibkr_proxy market-data provider."
                self.runtime.active = False
                return

            targets = self._load_subscription_targets()
            self.runtime.subscribed_tickers = sorted({target.ticker for target in targets.values()})
            self.runtime.subscribed_conids = sorted(targets.keys())

            if not targets:
                self._wait(self.settings.ibkr_market_monitor_sync_seconds)
                continue

            self.runtime.last_connected_at = datetime.now(UTC)
            self.runtime.reconnect_count += 1

            try:
                self._consume_stream(targets)
            except IBKRRealtimeStreamError as exc:
                self.runtime.last_error = str(exc)
                self._wait(self.settings.ibkr_market_monitor_reconnect_delay_seconds)
            except Exception as exc:
                self.runtime.last_error = str(exc)
                self._wait(self.settings.ibkr_market_monitor_reconnect_delay_seconds)

        self.runtime.active = False

    def _consume_stream(self, targets: dict[str, RealtimeSubscriptionTarget]) -> None:
        stream_started_at = time.monotonic()
        for envelope in self.realtime_client.stream_sse_events(
            conids=list(targets.keys()),
            fields=self.settings.ibkr_market_monitor_fields,
            stop_event=self._stop_event,
        ):
            if self._stop_event.is_set():
                break

            quote = self.realtime_client.extract_quote(envelope)
            if quote is None:
                continue
            target = targets.get(quote.conid)
            if target is None:
                continue
            if not self._should_evaluate_target(target, quote):
                continue

            self.runtime.last_event_at = datetime.now(UTC)
            self.runtime.processed_events += 1
            observed_price = quote.last_price if quote.last_price is not None else quote.bid_price or quote.ask_price
            self.runtime.last_event_summary = f"{target.ticker} @ {observed_price}" if observed_price is not None else target.ticker
            result = self._evaluate_target(target, quote)
            self.runtime.adjusted_positions += result.adjusted_positions
            self.runtime.closed_positions += result.closed_positions

            if result.closed_positions > 0:
                break
            if time.monotonic() - stream_started_at >= self.settings.ibkr_market_monitor_sync_seconds:
                break

    def _load_subscription_targets(self) -> dict[str, RealtimeSubscriptionTarget]:
        with SessionLocal() as session:
            positions = list(session.scalars(select(Position).where(Position.status == "open")).all())

        targets: dict[str, RealtimeSubscriptionTarget] = {}
        for position in positions:
            try:
                conid = self.ibkr_provider.resolve_conid(position.ticker) if self.ibkr_provider is not None else None
            except Exception as exc:
                self.runtime.last_error = f"Failed to resolve {position.ticker} for realtime monitoring: {exc}"
                continue

            if not conid:
                continue
            target = targets.setdefault(
                conid,
                RealtimeSubscriptionTarget(conid=conid, ticker=position.ticker.upper()),
            )
            target.positions.append(
                RealtimePositionHandle(
                    position_id=position.id,
                    ticker=position.ticker.upper(),
                    side=position.side,
                    entry_price=position.entry_price,
                    stop_price=position.stop_price,
                    target_price=position.target_price,
                )
            )
        return targets

    def _should_evaluate_target(self, target: RealtimeSubscriptionTarget, quote: IBKRRealtimeQuote) -> bool:
        price = quote.last_price
        if price is None:
            if quote.bid_price is not None and quote.ask_price is not None:
                price = round((quote.bid_price + quote.ask_price) / 2, 4)
            else:
                price = quote.bid_price or quote.ask_price
        if price is None:
            return False

        if self._hits_immediate_trigger(target, price):
            return True

        ticker = target.ticker
        now = time.monotonic()
        last_eval_at = self._last_evaluation_at.get(ticker)
        if last_eval_at is not None and now - last_eval_at < self.settings.ibkr_market_monitor_management_cooldown_seconds:
            return False

        last_price = self._last_evaluation_price.get(ticker)
        if last_price is None:
            return True

        move_pct = abs(price - last_price) / max(abs(last_price), 0.01)
        return move_pct >= self.settings.ibkr_market_monitor_price_move_threshold_pct

    @staticmethod
    def _hits_immediate_trigger(target: RealtimeSubscriptionTarget, price: float) -> bool:
        for position in target.positions:
            if position.side == "long":
                if position.stop_price is not None and price <= position.stop_price:
                    return True
                if position.target_price is not None and price >= position.target_price:
                    return True
            else:
                if position.stop_price is not None and price >= position.stop_price:
                    return True
                if position.target_price is not None and price <= position.target_price:
                    return True
        return False

    def _evaluate_target(self, target: RealtimeSubscriptionTarget, quote: IBKRRealtimeQuote):
        observed_price = quote.last_price
        if observed_price is None and quote.bid_price is not None and quote.ask_price is not None:
            observed_price = round((quote.bid_price + quote.ask_price) / 2, 4)
        if observed_price is None:
            observed_price = quote.bid_price or quote.ask_price or 0.0

        self._last_evaluation_at[target.ticker] = time.monotonic()
        self._last_evaluation_price[target.ticker] = observed_price

        with SessionLocal() as session:
            return self.exit_management_service.evaluate_positions_for_market_event(
                session,
                ticker=target.ticker,
                realtime_quote=quote.to_monitor_context(ticker=target.ticker),
            )

    def _wait(self, seconds: int) -> None:
        deadline = time.monotonic() + max(int(seconds), 0)
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.2)
