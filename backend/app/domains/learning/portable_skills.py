from __future__ import annotations

from datetime import UTC, datetime
import re

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.domains.learning.skills import (
    SKILL_CANDIDATE_MEMORY_TYPE,
    VALIDATED_SKILL_REVISION_MEMORY_TYPE,
    SkillCatalogService,
    SkillLifecycleService,
)


class SkillPortableArtifactService:
    ARTIFACT_VERSION = "skill_portable_v1"
    EXPORT_ARTIFACT_TYPE = "validated_skill_revision_export"
    MARKDOWN_FORMAT = "skill_md"
    YAML_FORMAT = "yaml"

    def __init__(
        self,
        *,
        catalog_service: SkillCatalogService | None = None,
        skill_lifecycle_service: SkillLifecycleService | None = None,
    ) -> None:
        self.catalog_service = catalog_service or SkillCatalogService()
        self.skill_lifecycle_service = skill_lifecycle_service or SkillLifecycleService(
            catalog_service=self.catalog_service
        )

    def export_revision(self, session: Session, *, revision_id: int) -> dict:
        revision = self.skill_lifecycle_service.get_revision(session, revision_id=revision_id)
        if revision is None:
            raise ValueError("Skill revision not found.")
        document = self._build_portable_document(session, revision=revision)
        exported_at = self._parse_datetime(document.get("exported_at"))
        return {
            "artifact_version": document.get("artifact_version"),
            "artifact_type": document.get("artifact_type"),
            "skill_code": self._skill_code_from_document(document),
            "target_skill_code": self._skill_code_from_document(document),
            "exported_at": exported_at,
            "document": document,
            "skill_md": self._render_skill_markdown(document),
            "yaml_text": self._render_yaml(document),
        }

    def import_artifact(
        self,
        session: Session,
        *,
        format: str,
        content: str,
        import_as: str,
        scope: str | None = None,
        key: str | None = None,
        summary: str | None = None,
        target_skill_code: str | None = None,
        candidate_action: str | None = None,
        ticker: str | None = None,
        strategy_version_id: int | None = None,
        candidate_id: int | None = None,
    ) -> dict:
        normalized_format = str(format or "").strip().lower()
        if normalized_format not in {self.MARKDOWN_FORMAT, self.YAML_FORMAT}:
            raise ValueError("Portable skill format must be skill_md or yaml.")
        normalized_import_as = str(import_as or "").strip().lower()
        if normalized_import_as not in {"candidate", "revision"}:
            raise ValueError("Portable skill import_as must be candidate or revision.")

        document = self._parse_portable_document(content=content, format=normalized_format)
        skill_code = str(
            target_skill_code
            or self._skill_code_from_document(document)
            or ""
        ).strip()
        if not skill_code:
            raise ValueError("Portable skill artifact does not declare a skill code.")

        if normalized_import_as == "candidate":
            candidate = self._import_candidate(
                session,
                document=document,
                format=normalized_format,
                skill_code=skill_code,
                scope=scope,
                key=key,
                summary=summary,
                candidate_action=candidate_action,
                ticker=ticker,
                strategy_version_id=strategy_version_id,
            )
            journal_entry = self._create_import_journal_entry(
                session,
                document=document,
                format=normalized_format,
                import_as=normalized_import_as,
                candidate=candidate,
                revision=None,
            )
            return {
                "format": normalized_format,
                "import_as": normalized_import_as,
                "document": document,
                "candidate": self.skill_lifecycle_service.get_candidate(session, candidate_id=candidate.id),
                "revision": None,
                "journal_entry_id": journal_entry.id,
            }

        revision = self._import_revision(
            session,
            document=document,
            format=normalized_format,
            skill_code=skill_code,
            key=key,
            summary=summary,
            ticker=ticker,
            strategy_version_id=strategy_version_id,
            candidate_id=candidate_id,
        )
        journal_entry = self._create_import_journal_entry(
            session,
            document=document,
            format=normalized_format,
            import_as=normalized_import_as,
            candidate=None,
            revision=revision,
        )
        return {
            "format": normalized_format,
            "import_as": normalized_import_as,
            "document": document,
            "candidate": None,
            "revision": self.skill_lifecycle_service.get_revision(session, revision_id=revision.id),
            "journal_entry_id": journal_entry.id,
        }

    def _build_portable_document(self, session: Session, *, revision: dict) -> dict:
        candidate = None
        if isinstance(revision.get("candidate_id"), int):
            candidate = self.skill_lifecycle_service.get_candidate(session, candidate_id=revision["candidate_id"])
        validation = None
        if isinstance(revision.get("validation_record_id"), int):
            validation = self.skill_lifecycle_service.get_validation_record(
                session,
                validation_record_id=revision["validation_record_id"],
            )

        skill_code = str(
            revision.get("skill_code")
            or (candidate or {}).get("target_skill_code")
            or ""
        ).strip() or None
        definition = self.catalog_service.get(skill_code or "")
        skill_name = (
            definition.name
            if definition is not None
            else self._humanize_skill_code(skill_code or "portable_skill")
        )
        candidate_meta = self._as_dict((candidate or {}).get("meta"))
        revision_meta = self._as_dict(revision.get("meta"))
        validation_payload = self._as_dict(validation)
        exported_at = datetime.now(UTC).isoformat()

        return {
            "artifact_version": self.ARTIFACT_VERSION,
            "artifact_type": self.EXPORT_ARTIFACT_TYPE,
            "exported_at": exported_at,
            "origin": {
                "revision_id": revision.get("id"),
                "candidate_id": (candidate or {}).get("id"),
                "validation_record_id": revision.get("validation_record_id"),
            },
            "skill": {
                "code": skill_code,
                "name": skill_name,
                "category": definition.category if definition is not None else "custom",
                "phases": list(definition.phases) if definition is not None else [],
                "objective": (
                    definition.objective
                    if definition is not None
                    else str((candidate or {}).get("summary") or revision.get("revision_summary") or "").strip()
                ),
                "description": (
                    definition.description
                    if definition is not None
                    else str(revision.get("revision_summary") or (candidate or {}).get("summary") or "").strip()
                ),
                "use_when": list(definition.use_when) if definition is not None else [],
                "avoid_when": list(definition.avoid_when) if definition is not None else [],
                "requires": list(definition.requires) if definition is not None else [],
                "produces": list(definition.produces) if definition is not None else [],
                "priority": definition.priority if definition is not None else 50,
                "dependencies": list(definition.dependencies) if definition is not None else [],
                "incompatible_with": list(definition.incompatible_with) if definition is not None else [],
                "tags": list(definition.tags) if definition is not None else [],
            },
            "candidate": {
                "id": (candidate or {}).get("id"),
                "scope": (candidate or {}).get("scope"),
                "key": (candidate or {}).get("key"),
                "summary": (candidate or {}).get("summary"),
                "target_skill_code": (candidate or {}).get("target_skill_code") or skill_code,
                "candidate_action": (candidate or {}).get("candidate_action"),
                "candidate_status": (candidate or {}).get("candidate_status"),
                "source_type": (candidate or {}).get("source_type"),
                "source_trade_review_id": (candidate or {}).get("source_trade_review_id"),
                "ticker": (candidate or {}).get("ticker"),
                "strategy_version_id": (candidate or {}).get("strategy_version_id"),
                "meta": candidate_meta,
            },
            "revision": {
                "id": revision.get("id"),
                "skill_code": skill_code,
                "candidate_id": revision.get("candidate_id"),
                "validation_record_id": revision.get("validation_record_id"),
                "activation_status": revision.get("activation_status"),
                "validation_mode": revision.get("validation_mode"),
                "validation_outcome": revision.get("validation_outcome"),
                "revision_summary": revision.get("revision_summary"),
                "source_trade_review_id": revision.get("source_trade_review_id"),
                "ticker": revision.get("ticker"),
                "strategy_version_id": revision.get("strategy_version_id"),
                "meta": revision_meta,
            },
            "validation": {
                "id": validation_payload.get("id"),
                "validation_mode": validation_payload.get("validation_mode"),
                "validation_outcome": validation_payload.get("validation_outcome"),
                "summary": validation_payload.get("summary"),
                "run_id": validation_payload.get("run_id"),
                "artifact_url": validation_payload.get("artifact_url"),
                "evidence_note": validation_payload.get("evidence_note"),
                "sample_size": validation_payload.get("sample_size"),
                "win_rate": validation_payload.get("win_rate"),
                "avg_pnl_pct": validation_payload.get("avg_pnl_pct"),
                "max_drawdown_pct": validation_payload.get("max_drawdown_pct"),
                "evidence_payload": self._as_dict(validation_payload.get("evidence_payload")),
            },
        }

    def _import_candidate(
        self,
        session: Session,
        *,
        document: dict,
        format: str,
        skill_code: str,
        scope: str | None,
        key: str | None,
        summary: str | None,
        candidate_action: str | None,
        ticker: str | None,
        strategy_version_id: int | None,
    ) -> MemoryItem:
        candidate_doc = self._as_dict(document.get("candidate"))
        revision_doc = self._as_dict(document.get("revision"))
        import_scope = self._bounded_string(
            str(scope or candidate_doc.get("scope") or f"skill:{skill_code}" or "skill:imported"),
            limit=50,
        )
        import_key = self._bounded_string(
            str(key or f"skill_candidate:portable:{self._slugify(skill_code)}:{int(datetime.now(UTC).timestamp())}"),
            limit=120,
        )
        summary_text = self._first_non_empty(
            summary,
            candidate_doc.get("summary"),
            revision_doc.get("revision_summary"),
            self._as_dict(document.get("skill")).get("objective"),
            "Imported portable skill artifact.",
        )
        action_value = self._first_non_empty(
            candidate_action,
            candidate_doc.get("candidate_action"),
            "update_existing_skill" if self.catalog_service.has(skill_code) else "draft_candidate_skill",
        )
        portable_meta = self._build_portable_meta(document=document, format=format)
        item = self._find_memory_item(
            session,
            memory_type=SKILL_CANDIDATE_MEMORY_TYPE,
            scope=import_scope,
            key=import_key,
        )
        meta = {
            **dict((item.meta if item is not None else {}) or {}),
            "summary": summary_text,
            "target_skill_code": skill_code,
            "candidate_action": action_value,
            "candidate_status": "draft",
            "activation_status": None,
            "validation_required": True,
            "source_type": "portable_skill_artifact",
            "source_trade_review_id": candidate_doc.get("source_trade_review_id"),
            "ticker": self._first_non_empty(ticker, candidate_doc.get("ticker"), revision_doc.get("ticker")),
            "strategy_version_id": strategy_version_id
            if strategy_version_id is not None
            else self._first_int(candidate_doc.get("strategy_version_id"), revision_doc.get("strategy_version_id")),
            "portable_skill_artifact": portable_meta,
        }
        if item is None:
            item = MemoryItem(
                memory_type=SKILL_CANDIDATE_MEMORY_TYPE,
                scope=import_scope,
                key=import_key,
                content=summary_text,
                meta=meta,
                importance=0.78,
            )
            session.add(item)
        else:
            item.content = summary_text
            item.meta = meta
            item.importance = max(float(item.importance or 0.0), 0.78)
            session.add(item)
        session.commit()
        session.refresh(item)
        return item

    def _import_revision(
        self,
        session: Session,
        *,
        document: dict,
        format: str,
        skill_code: str,
        key: str | None,
        summary: str | None,
        ticker: str | None,
        strategy_version_id: int | None,
        candidate_id: int | None,
    ) -> MemoryItem:
        candidate = None
        if candidate_id is not None:
            candidate = session.get(MemoryItem, candidate_id)
            if candidate is None or candidate.memory_type != SKILL_CANDIDATE_MEMORY_TYPE:
                raise ValueError("Skill candidate not found.")

        candidate_doc = self._as_dict(document.get("candidate"))
        revision_doc = self._as_dict(document.get("revision"))
        validation_doc = self._as_dict(document.get("validation"))
        summary_text = self._first_non_empty(
            summary,
            revision_doc.get("revision_summary"),
            candidate_doc.get("summary"),
            self._as_dict(document.get("skill")).get("objective"),
            "Imported portable skill revision.",
        )
        import_key = self._bounded_string(
            str(key or f"skill_revision:portable:{self._slugify(skill_code)}:{int(datetime.now(UTC).timestamp())}"),
            limit=120,
        )
        portable_meta = self._build_portable_meta(document=document, format=format)
        item = self._find_memory_item(
            session,
            memory_type=VALIDATED_SKILL_REVISION_MEMORY_TYPE,
            scope=f"skill:{skill_code}"[:50],
            key=import_key,
        )
        meta = {
            **dict((item.meta if item is not None else {}) or {}),
            "skill_code": skill_code,
            "candidate_id": candidate.id if candidate is not None else None,
            "candidate_key": candidate.key if candidate is not None else None,
            "validation_record_id": None,
            "activation_status": "imported_inactive",
            "validation_mode": self._first_non_empty(revision_doc.get("validation_mode"), "import"),
            "validation_outcome": self._first_non_empty(revision_doc.get("validation_outcome"), "approved"),
            "revision_summary": summary_text,
            "sample_size": validation_doc.get("sample_size"),
            "win_rate": validation_doc.get("win_rate"),
            "avg_pnl_pct": validation_doc.get("avg_pnl_pct"),
            "max_drawdown_pct": validation_doc.get("max_drawdown_pct"),
            "evidence": self._as_dict(validation_doc.get("evidence_payload")),
            "validated_at": datetime.now(UTC).isoformat(),
            "source_trade_review_id": self._first_int(
                revision_doc.get("source_trade_review_id"),
                candidate_doc.get("source_trade_review_id"),
            ),
            "ticker": self._first_non_empty(ticker, revision_doc.get("ticker"), candidate_doc.get("ticker")),
            "strategy_version_id": strategy_version_id
            if strategy_version_id is not None
            else self._first_int(revision_doc.get("strategy_version_id"), candidate_doc.get("strategy_version_id")),
            "validation_gate": "portable_import_v1",
            "known_catalog_skill": self.catalog_service.has(skill_code),
            "portable_skill_artifact": portable_meta,
        }
        if item is None:
            item = MemoryItem(
                memory_type=VALIDATED_SKILL_REVISION_MEMORY_TYPE,
                scope=f"skill:{skill_code}"[:50],
                key=import_key,
                content=summary_text,
                meta=meta,
                importance=0.8,
                valid_from=datetime.now(UTC),
            )
            session.add(item)
        else:
            item.content = summary_text
            item.meta = meta
            item.importance = max(float(item.importance or 0.0), 0.8)
            if item.valid_from is None:
                item.valid_from = datetime.now(UTC)
            session.add(item)
        session.commit()
        session.refresh(item)
        return item

    def _create_import_journal_entry(
        self,
        session: Session,
        *,
        document: dict,
        format: str,
        import_as: str,
        candidate: MemoryItem | None,
        revision: MemoryItem | None,
    ) -> JournalEntry:
        candidate_payload = (
            SkillLifecycleService._json_ready_payload(SkillLifecycleService._candidate_payload(candidate))
            if candidate is not None
            else None
        )
        revision_payload = (
            SkillLifecycleService._json_ready_payload(SkillLifecycleService._revision_payload(revision))
            if revision is not None
            else None
        )
        target_payload = candidate_payload or revision_payload or {}
        summary_text = self._first_non_empty(
            (candidate_payload or {}).get("summary"),
            (revision_payload or {}).get("revision_summary"),
            self._as_dict(document.get("revision")).get("revision_summary"),
            "Imported portable skill artifact.",
        )
        journal_entry = JournalEntry(
            entry_type="skill_portable_candidate_imported"
            if import_as == "candidate"
            else "skill_portable_revision_imported",
            ticker=target_payload.get("ticker"),
            strategy_version_id=target_payload.get("strategy_version_id"),
            observations={
                "portable_skill_artifact": {
                    "artifact_version": document.get("artifact_version"),
                    "artifact_type": document.get("artifact_type"),
                    "format": format,
                    "skill_code": self._skill_code_from_document(document),
                    "origin": self._as_dict(document.get("origin")),
                },
                "skill_candidate": candidate_payload,
                "skill_revision": revision_payload,
            },
            reasoning=summary_text,
            decision="import_portable_skill_candidate"
            if import_as == "candidate"
            else "import_portable_skill_revision",
            lessons=summary_text,
        )
        session.add(journal_entry)
        session.commit()
        session.refresh(journal_entry)
        return journal_entry

    def _parse_portable_document(self, *, content: str, format: str) -> dict:
        text = str(content or "").strip()
        if not text:
            raise ValueError("Portable skill content is required.")
        if format == self.YAML_FORMAT:
            parsed = yaml.safe_load(text)
        else:
            parsed = self._parse_markdown_frontmatter(text)
        if not isinstance(parsed, dict):
            raise ValueError("Portable skill artifact must decode to an object.")
        if str(parsed.get("artifact_version") or "").strip() != self.ARTIFACT_VERSION:
            raise ValueError("Unsupported portable skill artifact version.")
        if str(parsed.get("artifact_type") or "").strip() != self.EXPORT_ARTIFACT_TYPE:
            raise ValueError("Unsupported portable skill artifact type.")
        return self._json_ready(self._as_dict(parsed))

    @staticmethod
    def _parse_markdown_frontmatter(content: str) -> dict:
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("SKILL.md import requires YAML frontmatter.")
        closing_index = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                closing_index = index
                break
        if closing_index is None:
            raise ValueError("SKILL.md frontmatter is not closed.")
        frontmatter = "\n".join(lines[1:closing_index]).strip()
        parsed = yaml.safe_load(frontmatter)
        if not isinstance(parsed, dict):
            raise ValueError("SKILL.md frontmatter must decode to an object.")
        return parsed

    def _render_yaml(self, document: dict) -> str:
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=False)

    def _render_skill_markdown(self, document: dict) -> str:
        skill = self._as_dict(document.get("skill"))
        candidate = self._as_dict(document.get("candidate"))
        revision = self._as_dict(document.get("revision"))
        validation = self._as_dict(document.get("validation"))
        origin = self._as_dict(document.get("origin"))
        lines = [
            "---",
            self._render_yaml(document).strip(),
            "---",
            f"# {self._first_non_empty(skill.get('name'), skill.get('code'), 'Portable Skill Artifact')}",
            "",
        ]
        objective = self._first_non_empty(skill.get("objective"))
        if objective:
            lines.extend(["## Objective", objective, ""])
        description = self._first_non_empty(skill.get("description"))
        if description:
            lines.extend(["## Description", description, ""])
        self._append_bullet_section(lines, "Use When", skill.get("use_when"))
        self._append_bullet_section(lines, "Avoid When", skill.get("avoid_when"))
        self._append_bullet_section(lines, "Required Context", skill.get("requires"))
        self._append_bullet_section(lines, "Expected Outputs", skill.get("produces"))
        revision_summary = self._first_non_empty(revision.get("revision_summary"))
        if revision_summary:
            lines.extend(["## Validated Revision", revision_summary, ""])
        validation_rows = [
            f"mode: {validation.get('validation_mode')}" if validation.get("validation_mode") else None,
            f"outcome: {validation.get('validation_outcome')}" if validation.get("validation_outcome") else None,
            f"sample_size: {validation.get('sample_size')}" if validation.get("sample_size") is not None else None,
            f"win_rate: {validation.get('win_rate')}" if validation.get("win_rate") is not None else None,
            f"avg_pnl_pct: {validation.get('avg_pnl_pct')}" if validation.get("avg_pnl_pct") is not None else None,
            (
                f"max_drawdown_pct: {validation.get('max_drawdown_pct')}"
                if validation.get("max_drawdown_pct") is not None
                else None
            ),
        ]
        self._append_bullet_section(lines, "Validation Snapshot", validation_rows)
        provenance_rows = [
            f"exported_at: {document.get('exported_at')}" if document.get("exported_at") else None,
            f"origin_revision_id: {origin.get('revision_id')}" if origin.get("revision_id") is not None else None,
            f"origin_candidate_id: {origin.get('candidate_id')}" if origin.get("candidate_id") is not None else None,
            (
                f"origin_validation_record_id: {origin.get('validation_record_id')}"
                if origin.get("validation_record_id") is not None
                else None
            ),
            f"candidate_action: {candidate.get('candidate_action')}" if candidate.get("candidate_action") else None,
            f"activation_status: {revision.get('activation_status')}" if revision.get("activation_status") else None,
        ]
        self._append_bullet_section(lines, "Provenance", provenance_rows)
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _append_bullet_section(lines: list[str], title: str, values: object) -> None:
        items = [
            str(item).strip()
            for item in (values if isinstance(values, list) else [])
            if str(item).strip()
        ]
        if not items:
            return
        lines.append(f"## {title}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    def _build_portable_meta(self, *, document: dict, format: str) -> dict:
        return {
            "artifact_version": document.get("artifact_version"),
            "artifact_type": document.get("artifact_type"),
            "format": format,
            "skill_code": self._skill_code_from_document(document),
            "exported_at": document.get("exported_at"),
            "origin": self._as_dict(document.get("origin")),
            "imported_at": datetime.now(UTC).isoformat(),
            "document": document,
        }

    @staticmethod
    def _find_memory_item(
        session: Session,
        *,
        memory_type: str,
        scope: str,
        key: str,
    ) -> MemoryItem | None:
        return session.scalar(
            select(MemoryItem).where(
                MemoryItem.memory_type == memory_type,
                MemoryItem.scope == scope,
                MemoryItem.key == key,
            )
        )

    @staticmethod
    def _skill_code_from_document(document: dict) -> str | None:
        skill = document.get("skill") if isinstance(document.get("skill"), dict) else {}
        revision = document.get("revision") if isinstance(document.get("revision"), dict) else {}
        candidate = document.get("candidate") if isinstance(document.get("candidate"), dict) else {}
        for value in (
            skill.get("code"),
            revision.get("skill_code"),
            candidate.get("target_skill_code"),
        ):
            normalized = str(value or "").strip()
            if normalized:
                return normalized
        return None

    @staticmethod
    def _humanize_skill_code(value: str) -> str:
        parts = [part for part in re.split(r"[_\-\s]+", str(value or "").strip()) if part]
        if not parts:
            return "Portable Skill"
        return " ".join(part.capitalize() for part in parts)

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
        return normalized or "skill"

    @staticmethod
    def _bounded_string(value: str, *, limit: int) -> str:
        return str(value or "").strip()[: max(int(limit), 1)]

    @staticmethod
    def _as_dict(value: object) -> dict:
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _json_ready(cls, value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): cls._json_ready(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_ready(item) for item in value]
        return value

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _first_non_empty(*values: object) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_int(*values: object) -> int | None:
        for value in values:
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
        return None
