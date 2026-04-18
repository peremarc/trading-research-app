from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
from threading import Event
from typing import Iterator

import httpx


class IBKRRealtimeStreamError(RuntimeError):
    pass


@dataclass
class SSEEventEnvelope:
    event: str
    data: dict | list | str | None
    raw_data: str
    received_at: str


@dataclass
class IBKRRealtimeQuote:
    conid: str
    topic: str | None
    last_price: float | None
    bid_price: float | None
    ask_price: float | None
    payload: dict = field(default_factory=dict)
    received_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_monitor_context(self, *, ticker: str) -> dict:
        return {
            "source": "ibkr_realtime_sse",
            "ticker": ticker,
            "conid": self.conid,
            "topic": self.topic,
            "last_price": self.last_price,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "payload": self.payload,
            "received_at": self.received_at,
        }


class IBKRRealtimeClient:
    TOPIC_CONID_PATTERN = re.compile(r"(\d+)")

    def __init__(self, *, base_url: str, api_key: str | None = None, read_timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.read_timeout_seconds = read_timeout_seconds

    def stream_sse_events(
        self,
        *,
        conids: list[str],
        fields: str,
        stop_event: Event | None = None,
    ) -> Iterator[SSEEventEnvelope]:
        url = f"{self.base_url}/sse/market"
        params = {"conids": ",".join(conids), "fields": fields}
        timeout = httpx.Timeout(
            connect=min(float(self.read_timeout_seconds), 5.0),
            read=float(self.read_timeout_seconds),
            write=min(float(self.read_timeout_seconds), 5.0),
            pool=min(float(self.read_timeout_seconds), 5.0),
        )
        headers = {
            "Accept": "text/event-stream",
            "User-Agent": "trading-research-app/0.1 (+https://localhost)",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                with client.stream("GET", url, params=params) as response:
                    response.raise_for_status()
                    yield from self._iter_sse_response(response, stop_event=stop_event)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            raise IBKRRealtimeStreamError(
                f"IBKR realtime SSE request failed: HTTP {exc.response.status_code} {detail or exc.response.reason_phrase}"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise IBKRRealtimeStreamError("IBKR realtime SSE read timed out") from exc
        except httpx.HTTPError as exc:
            raise IBKRRealtimeStreamError(f"IBKR realtime SSE failed: {exc}") from exc

    def extract_quote(self, envelope: SSEEventEnvelope) -> IBKRRealtimeQuote | None:
        candidates = list(self._iter_candidate_payloads(envelope.data))
        for candidate in candidates:
            topic = self._coerce_topic(candidate)
            conid = self._coerce_conid(candidate, topic=topic)
            last_price = self._coerce_float(candidate.get("31"))
            bid_price = self._coerce_float(candidate.get("84"))
            ask_price = self._coerce_float(candidate.get("86"))
            if conid and any(value is not None for value in (last_price, bid_price, ask_price)):
                return IBKRRealtimeQuote(
                    conid=conid,
                    topic=topic,
                    last_price=last_price,
                    bid_price=bid_price,
                    ask_price=ask_price,
                    payload=candidate,
                    received_at=envelope.received_at,
                )
        return None

    @classmethod
    def _iter_sse_response(
        cls,
        response: httpx.Response,
        *,
        stop_event: Event | None = None,
    ) -> Iterator[SSEEventEnvelope]:
        current_event = "message"
        data_lines: list[str] = []

        for line in response.iter_lines():
            if stop_event is not None and stop_event.is_set():
                break

            if line == "":
                envelope = cls._build_envelope(current_event=current_event, data_lines=data_lines)
                if envelope is not None:
                    yield envelope
                current_event = "message"
                data_lines = []
                continue

            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip() or "message"
                continue
            if line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())

        if data_lines:
            envelope = cls._build_envelope(current_event=current_event, data_lines=data_lines)
            if envelope is not None:
                yield envelope

    @staticmethod
    def _build_envelope(*, current_event: str, data_lines: list[str]) -> SSEEventEnvelope | None:
        if not data_lines:
            return None

        raw_data = "\n".join(data_lines)
        data: dict | list | str | None
        try:
            data = json.loads(raw_data)
        except ValueError:
            data = raw_data
        return SSEEventEnvelope(
            event=current_event,
            data=data,
            raw_data=raw_data,
            received_at=datetime.now(UTC).isoformat(),
        )

    @classmethod
    def _iter_candidate_payloads(cls, payload) -> Iterator[dict]:
        stack = [payload]
        seen_ids: set[int] = set()
        while stack:
            item = stack.pop()
            if id(item) in seen_ids:
                continue
            seen_ids.add(id(item))
            if isinstance(item, dict):
                yield item
                for key in ("data", "args", "payload"):
                    nested = item.get(key)
                    if isinstance(nested, (dict, list)):
                        stack.append(nested)
            elif isinstance(item, list):
                for nested in item:
                    if isinstance(nested, (dict, list)):
                        stack.append(nested)

    @staticmethod
    def _coerce_topic(payload: dict) -> str | None:
        topic = payload.get("topic")
        if topic is None:
            return None
        text = str(topic).strip()
        return text or None

    @classmethod
    def _coerce_conid(cls, payload: dict, *, topic: str | None = None) -> str | None:
        for key in ("conid", "conidEx"):
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text

        if topic:
            match = cls.TOPIC_CONID_PATTERN.search(topic)
            if match is not None:
                return match.group(1)
        return None

    @staticmethod
    def _coerce_float(value) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
