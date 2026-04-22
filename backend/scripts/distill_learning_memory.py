#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Build compact learning-memory digests from noisy review and disagreement history.")
    parser.add_argument("--apply", action="store_true", help="Persist the distillation instead of running a dry run.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Optional DATABASE_URL override, for example sqlite:///./trading_research.db",
    )
    parser.add_argument("--claim-limit", type=int, default=200, help="Maximum number of claims to inspect.")
    parser.add_argument("--disagreement-limit", type=int, default=200, help="Maximum number of disagreement events to inspect.")
    parser.add_argument("--skill-gap-limit", type=int, default=200, help="Maximum number of skill gaps to inspect.")
    parser.add_argument(
        "--skill-candidate-limit",
        type=int,
        default=200,
        help="Maximum number of skill candidates to inspect.",
    )
    parser.add_argument("--min-group-size", type=int, default=2, help="Minimum grouped source size before emitting a digest.")
    parser.add_argument("--skip-claims", action="store_true", help="Skip claim-review distillation.")
    parser.add_argument("--skip-operator-feedback", action="store_true", help="Skip operator-disagreement distillation.")
    parser.add_argument("--skip-skill-gaps", action="store_true", help="Skip skill-gap backlog distillation.")
    parser.add_argument("--skip-skill-candidates", action="store_true", help="Skip skill-candidate backlog distillation.")
    args = parser.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    os.chdir(backend_dir)

    from app.db.session import SessionLocal
    from app.domains.learning.services import LearningMemoryDistillationService

    with SessionLocal() as session:
        result = LearningMemoryDistillationService().distill_memory(
            session,
            dry_run=not args.apply,
            include_claim_reviews=not args.skip_claims,
            include_operator_feedback=not args.skip_operator_feedback,
            include_skill_gaps=not args.skip_skill_gaps,
            include_skill_candidates=not args.skip_skill_candidates,
            claim_limit=args.claim_limit,
            disagreement_limit=args.disagreement_limit,
            skill_gap_limit=args.skill_gap_limit,
            skill_candidate_limit=args.skill_candidate_limit,
            min_group_size=args.min_group_size,
        )

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
