from __future__ import annotations

from typing import Any, Protocol


class BacktestingProviderError(RuntimeError):
    pass


class BacktestingProvider(Protocol):
    base_url: str

    def get_capabilities(self) -> dict[str, Any]:
        raise NotImplementedError

    def get_ai_context(self) -> dict[str, Any]:
        raise NotImplementedError

    def submit_backtest(self, spec: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def get_backtest_run(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_backtest_metrics(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_backtest_artifacts(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def cancel_backtest(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError
