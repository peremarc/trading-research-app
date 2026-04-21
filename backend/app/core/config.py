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
    database_auto_upgrade_on_startup: bool = True
    bootstrap_seed_on_startup: bool = False
    scheduler_enabled: bool = False
    scheduler_mode: str = "continuous"
    scheduler_interval_minutes: int = 5
    scheduler_continuous_idle_seconds: int = 5
    scheduler_market_closed_idle_seconds: int = 1800
    scheduler_run_on_startup: bool = False
    scheduler_timezone: str = "Europe/Madrid"
    scheduler_plan_hour: int = 8
    scheduler_do_hour: int = 15
    scheduler_check_hour: int = 22
    market_data_provider: str = "ibkr_proxy"
    ibkr_proxy_base_url: str = "https://dev-ibkr.peremarc.com"
    ibkr_proxy_api_key: str | None = None
    ibkr_proxy_timeout_seconds: int = 15
    market_data_max_concurrent_requests: int = 2
    ibkr_market_monitor_enabled: bool = True
    ibkr_market_monitor_transport: str = "sse"
    ibkr_market_monitor_fields: str = "31,84,86"
    ibkr_market_monitor_sync_seconds: int = 30
    ibkr_market_monitor_read_timeout_seconds: int = 15
    ibkr_market_monitor_reconnect_delay_seconds: int = 5
    ibkr_market_monitor_management_cooldown_seconds: int = 60
    ibkr_market_monitor_price_move_threshold_pct: float = 0.003
    alpha_vantage_api_key: str | None = None
    twelve_data_api_key: str | None = None
    eodhd_api_key: str | None = None
    finnhub_api_key: str | None = None
    fred_api_key: str | None = None
    fred_request_timeout_seconds: int = 15
    calendar_earnings_cache_ttl_seconds: int = 86400
    calendar_ticker_events_cache_ttl_seconds: int = 300
    calendar_macro_events_cache_ttl_seconds: int = 900
    calendar_corporate_max_concurrent_requests: int = 2
    calendar_earnings_max_concurrent_requests: int = 1
    calendar_macro_max_concurrent_requests: int = 1
    gnews_api_key: str | None = None
    gnews_base_url: str = "https://gnews.io/api/v4"
    gnews_language: str = "en"
    gnews_country: str = "us"
    gnews_max_results: int = 10
    gnews_cache_ttl_seconds: int = 300
    gnews_max_concurrent_requests: int = 2
    decision_context_io_parallelism_enabled: bool = True
    decision_context_io_max_workers: int = 3
    web_research_enabled: bool = True
    web_search_provider: str = "duckduckgo"
    web_allowed_domains: str = (
        "reuters.com,cnbc.com,finance.yahoo.com,marketwatch.com,nasdaq.com,investing.com,sec.gov"
    )
    web_request_timeout_seconds: int = 15
    web_search_max_results: int = 5
    web_fetch_max_chars: int = 12000
    benchmark_ticker: str = "SPY"
    opportunity_discovery_enabled: bool = True
    opportunity_discovery_per_watchlist: int = 2
    opportunity_discovery_min_score: float = 0.65
    opportunity_discovery_universe_source: str = "ibkr_scanner"
    opportunity_discovery_universe_limit: int = 60
    opportunity_discovery_scanner_instrument: str = "STK"
    opportunity_discovery_scanner_location: str = "STK.US.MAJOR"
    opportunity_discovery_scanner_types: str = "MOST_ACTIVE,TOP_PERC_GAIN,HIGH_VS_52W_HL"
    opportunity_discovery_scanner_filters_json: str = "[]"
    opportunity_discovery_universe: str = (
        "NVDA,MSFT,META,AAPL,AMZN,UBER,GOOGL,AMD,TSLA,AVGO,CRM,NFLX,SHOP,SNOW,DDOG,MDB,NOW,ROKU,SQ,PLTR"
    )
    orchestrator_scan_when_market_closed: bool = True
    orchestrator_scheduled_reanalysis_jitter_seconds: int = 120
    orchestrator_scheduled_reanalysis_max_checks_per_cycle: int = 8
    orchestrator_scheduled_reanalysis_budget_seconds: int = 15
    orchestrator_scheduled_reanalysis_budget_deferral_seconds: int = 60
    orchestrator_scheduled_reanalysis_budget_spacing_seconds: int = 5
    opportunity_discovery_run_when_market_closed: bool = True
    idle_research_enabled: bool = True
    idle_research_per_cycle: int = 2
    idle_research_scan_limit: int = 6
    idle_research_max_open_tasks: int = 6
    macro_research_enabled: bool = True
    macro_research_per_cycle: int = 1
    macro_research_days_ahead: int = 45
    macro_research_max_news_per_topic: int = 3
    macro_research_max_web_results: int = 2
    macro_research_max_article_chars: int = 4000
    macro_research_max_open_tasks: int = 6
    macro_indicators_enabled: bool = True
    macro_indicators_cache_ttl_seconds: int = 900
    macro_indicators_request_timeout_seconds: int = 12
    strategy_company_enabled: bool = True
    strategy_company_cache_ttl_seconds: int = 21600
    strategy_company_request_timeout_seconds: int = 12
    strategy_company_btc_proxy_symbol: str = "IBIT"
    ai_agent_enabled: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    ai_primary_provider: str = "gemini"
    ai_primary_model: str = "gemini-2.5-flash"
    gemini_api_key: str | None = None
    gemini_api_key_free1: str | None = None
    gemini_api_key_free2: str | None = None
    codex_gateway_base_url: str | None = None
    codex_gateway_api_key: str | None = None
    codex_gateway_model_label: str = "gpt-5.3-codex-spark"
    codex_gateway_codex_model: str | None = None
    ai_fallback_provider: str = "openai_compatible"
    ai_fallback_model: str = "qwen2.5:3b"
    ai_fallback_api_key: str | None = None
    ai_fallback_api_base: str | None = None
    chat_llm_default: str | None = None
    chat_qwen_model: str | None = None
    chat_qwen_api_key: str | None = None
    chat_qwen_api_base: str | None = None
    chat_gpt54_model: str = "gpt-5.4"
    chat_gpt54_api_key: str | None = None
    chat_gpt54_api_base: str | None = None
    chat_gpt54_reasoning_effort: str = "xhigh"
    ai_temperature: float = 0.15
    ai_max_output_tokens: int = 500
    ai_request_timeout_seconds: int = 20
    ai_failure_cooldown_seconds: int = 180
    ai_market_closed_enabled: bool = True
    paper_entry_when_market_closed: bool = False
    ai_memory_limit: int = 8
    ai_journal_limit: int = 12
    ai_failure_pattern_limit: int = 5
    ai_runtime_skill_limit: int = 3
    ai_runtime_skill_step_limit: int = 4
    ai_runtime_claim_limit: int = 3
    ai_runtime_claim_evidence_limit: int = 2
    knowledge_claim_aging_days: int = 14
    knowledge_claim_stale_days: int = 30
    learning_workflow_governance_enabled: bool = True
    learning_workflow_governance_interval_minutes: int = 30
    paper_portfolio_capital_base: float = 100000.0
    paper_risk_per_trade_fraction: float = 0.01
    paper_max_portfolio_risk_fraction: float = 0.06
    paper_max_notional_fraction_per_trade: float = 0.2
    paper_daily_drawdown_limit_pct: float = -10.0
    paper_weekly_drawdown_limit_pct: float = -20.0

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
