from __future__ import annotations

from typing import Any

import httpx

from app.providers.backtesting.base import BacktestingProviderError


class RemoteBacktestingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def get_capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/capabilities")

    def get_ai_context(self) -> dict[str, Any]:
        return self._request("GET", "/ai/context")

    def submit_backtest(self, spec: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/backtests", json_body=spec)

    def get_backtest_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/backtests/{run_id}")

    def get_backtest_metrics(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/backtests/{run_id}/metrics")

    def get_backtest_artifacts(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/backtests/{run_id}/artifacts")

    def cancel_backtest(self, run_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/backtests/{run_id}/cancel")

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "trading-research-app/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        timeout = httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 10))
        try:
            with httpx.Client(base_url=self.base_url, timeout=timeout, headers=headers, follow_redirects=True) as client:
                response = client.request(method, path, json=json_body)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _extract_error_detail(exc.response)
            raise BacktestingProviderError(
                f"Backtesting service request failed with HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise BacktestingProviderError(f"Backtesting service request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise BacktestingProviderError("Backtesting service returned a non-JSON response.") from exc
        if not isinstance(payload, dict):
            raise BacktestingProviderError("Backtesting service returned an invalid payload.")
        return payload


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text or response.reason_phrase
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return response.reason_phrase
