#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Compact oversized historical strategy hypotheses.")
    parser.add_argument("--apply", action="store_true", help="Persist the compaction instead of running a dry run.")
    parser.add_argument("--keep-recent", type=int, default=5, help="Keep this many newest versions per strategy untouched.")
    parser.add_argument("--max-chars", type=int, default=600, help="Maximum hypothesis size for compacted historical versions.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional DATABASE_URL override, for example sqlite:///./trading_research.db",
    )
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after applying compaction.")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.db.session import SessionLocal, engine
    from app.domains.strategy.services import StrategyMaintenanceService

    with SessionLocal() as session:
        result = StrategyMaintenanceService().compact_historical_hypotheses(
            session,
            dry_run=not args.apply,
            keep_recent=args.keep_recent,
            max_chars=args.max_chars,
        )

    if args.apply and args.vacuum:
        with engine.connect() as connection:
            connection.exec_driver_sql("VACUUM")

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
