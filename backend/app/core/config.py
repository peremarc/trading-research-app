from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    app_name: str = "Trading Research Backend"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./trading_research.db"
    bootstrap_seed_on_startup: bool = False
    scheduler_enabled: bool = False
    scheduler_mode: str = "cron"
    scheduler_interval_minutes: int = 15
    scheduler_run_on_startup: bool = False
    scheduler_timezone: str = "Europe/Madrid"
    scheduler_plan_hour: int = 8
    scheduler_do_hour: int = 15
    scheduler_check_hour: int = 22
    market_data_provider: str = "twelve_data"
    twelve_data_api_key: str | None = None
    eodhd_api_key: str | None = None
    finnhub_api_key: str | None = None
    benchmark_ticker: str = "SPY"
    opportunity_discovery_enabled: bool = True
    opportunity_discovery_per_watchlist: int = 2
    opportunity_discovery_min_score: float = 0.65
    opportunity_discovery_universe: str = (
        "NVDA,MSFT,META,AAPL,AMZN,UBER,GOOGL,AMD,TSLA,AVGO,CRM,NFLX,SHOP,SNOW,DDOG,MDB,NOW,ROKU,SQ,PLTR"
    )

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
