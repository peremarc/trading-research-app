#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune noisy journal and memory history using built-in retention rules.")
    parser.add_argument("--apply", action="store_true", help="Persist the pruning instead of running a dry run.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional DATABASE_URL override, for example sqlite:///./trading_research.db",
    )
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after applying pruning.")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    from app.db.session import SessionLocal, engine
    from app.domains.learning.services import LearningHistoryMaintenanceService

    with SessionLocal() as session:
        result = LearningHistoryMaintenanceService().trim_history(session, dry_run=not args.apply)

    if args.apply and args.vacuum:
        with engine.connect() as connection:
            connection.exec_driver_sql("VACUUM")

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
