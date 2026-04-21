from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import unicodedata

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.memory import MemoryItem
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.learning.agent import AIDecisionError
from app.domains.learning.macro import MacroContextService
from app.domains.learning.schemas import (
    ChatConversationCreate,
    ChatConversationDetailRead,
    ChatConversationRead,
    ChatConversationTurnResponse,
    ChatConversationUpdate,
    ChatLLMPresetRead,
    ChatMessageCreate,
    ChatMessageRead,
    MemoryItemCreate,
)
from app.domains.learning.services import BotChatService, MemoryService
from app.domains.learning.world_state import MarketStateService
from app.domains.market.schemas import ResearchTaskCreate
from app.domains.market.services import CalendarService, MarketDataService, NewsService, ResearchService
from app.providers.llm import (
    LLMProviderError,
    LLMProviderSpec,
    build_json_decision_provider,
    normalize_provider_name,
    provider_is_ready,
)
from app.providers.calendar import CalendarProviderError
from app.providers.news import NewsProviderError


ALLOWED_CHAT_STATUSES = {"active", "archived", "pinned"}
CHAT_LLM_FALLBACK_PROVIDER = "local_rules"
STOPWORD_TICKERS = {
    "EL",
    "LA",
    "LOS",
    "LAS",
    "DEL",
    "POR",
    "PARA",
    "CON",
    "SIN",
    "QUE",
    "COMO",
    "PERO",
    "THIS",
    "THAT",
}


@dataclass(frozen=True)
class ChatLLMPresetSpec:
    key: str
    label: str
    provider: str
    model: str | None
    reasoning_effort: str | None
    api_key: str | None
    api_base: str | None
    codex_model: str | None
    ready: bool
    availability_error: str | None = None


class ChatLLMPresetService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def list_presets(self) -> list[ChatLLMPresetRead]:
        return [
            ChatLLMPresetRead(
                key=spec.key,
                label=spec.label,
                provider=spec.provider,
                model=spec.model,
                reasoning_effort=spec.reasoning_effort,
                ready=spec.ready,
                availability_error=spec.availability_error,
            )
            for spec in self._build_specs().values()
        ]

    def resolve(self, key: str | None) -> ChatLLMPresetSpec:
        preset_key = (key or self.default_preset_key()).strip()
        spec = self._build_specs().get(preset_key)
        if spec is None:
            raise ValueError(f"Unsupported chat LLM preset '{preset_key}'.")
        return spec

    def default_preset_key(self) -> str:
        return self._default_preset_key()

    def render_reply(
        self,
        *,
        preset_key: str | None,
        draft_reply: str,
        conversation_title: str,
        user_message: str,
        assistant_context: dict,
        thread_messages: list[dict] | None = None,
    ) -> tuple[str, dict]:
        spec = self.resolve(preset_key)
        if not spec.ready:
            return draft_reply, {
                "requested_llm": spec.key,
                "used_provider": CHAT_LLM_FALLBACK_PROVIDER,
                "used_model": "deterministic_draft",
                "reasoning_effort": None,
                "fallback_used": True,
                "provider_error": spec.availability_error,
            }

        provider = self._build_provider(spec)
        if provider is None:
            return draft_reply, {
                "requested_llm": spec.key,
                "used_provider": CHAT_LLM_FALLBACK_PROVIDER,
                "used_model": "deterministic_draft",
                "reasoning_effort": None,
                "fallback_used": True,
                "provider_error": spec.availability_error or "Chat preset is not configured correctly.",
            }

        system_prompt = (
            "Eres un analista de trading disciplinado que conversa con un operador. "
            "Responde en espanol claro y sobrio. Puedes discrepar, matizar o degradar una idea a research. "
            "No inventes datos, no anadas tickers, noticias ni riesgos no presentes en el contexto. "
            "No prometas ejecucion automatica. Si falta evidencia, dilo. "
            'Devuelve solo JSON con la clave "reply".'
        )
        history_lines = []
        for item in thread_messages or []:
            role = "Usuario" if item.get("role") == "user" else "Bot"
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            history_lines.append(f"{role}: {content[:400]}")
        history_block = "\n".join(history_lines[-6:]) if history_lines else "Sin historial previo."
        user_prompt = (
            f"Conversacion: {conversation_title}\n"
            f"Historial reciente:\n{history_block}\n\n"
            f"Ultimo mensaje del usuario: {user_message}\n\n"
            f"Contexto estructurado del sistema: {assistant_context}\n\n"
            f"Borrador factual del sistema:\n{draft_reply}\n\n"
            "Responde directamente al usuario usando el contexto y el historial. "
            "No te limites a parafrasear el borrador si puedes razonar mejor con los hechos dados."
        )
        schema = {
            "type": "object",
            "properties": {
                "reply": {"type": "string"},
            },
            "required": ["reply"],
        }
        try:
            payload = provider.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_json_schema=schema,
            )
            reply = str(payload.get("reply") or "").strip()
        except (AIDecisionError, LLMProviderError) as exc:
            return draft_reply, {
                "requested_llm": spec.key,
                "used_provider": CHAT_LLM_FALLBACK_PROVIDER,
                "used_model": "deterministic_draft",
                "reasoning_effort": None,
                "fallback_used": True,
                "provider_error": str(exc),
            }
        if not reply:
            return draft_reply, {
                "requested_llm": spec.key,
                "used_provider": CHAT_LLM_FALLBACK_PROVIDER,
                "used_model": "deterministic_draft",
                "reasoning_effort": None,
                "fallback_used": True,
                "provider_error": "The selected chat model returned an empty reply.",
            }
        return reply, {
            "requested_llm": spec.key,
            "used_provider": normalize_provider_name(spec.provider),
            "used_model": spec.model,
            "reasoning_effort": spec.reasoning_effort,
            "fallback_used": False,
            "provider_error": None,
        }

    def _build_provider(self, spec: ChatLLMPresetSpec):
        return build_json_decision_provider(
            LLMProviderSpec(
                provider=spec.provider,
                model=spec.model,
                api_key=spec.api_key,
                api_base=spec.api_base,
                temperature=self.settings.ai_temperature,
                max_output_tokens=self.settings.ai_max_output_tokens,
                request_timeout_seconds=self.settings.ai_request_timeout_seconds,
                reasoning_effort=spec.reasoning_effort,
                codex_model=spec.codex_model,
            )
        )

    def _default_preset_key(self) -> str:
        explicit = (self.settings.chat_llm_default or "").strip()
        if explicit:
            return explicit
        runtime_provider = normalize_provider_name(self.settings.llm_provider or self.settings.ai_primary_provider)
        if runtime_provider == "codex_gateway":
            return "codex-gateway"
        return "gemini-2.5-flash"

    def _build_specs(self) -> dict[str, ChatLLMPresetSpec]:
        gemini_key = self._first_non_empty(
            self.settings.gemini_api_key,
            self.settings.gemini_api_key_free1,
            self.settings.gemini_api_key_free2,
        )
        qwen_model = self._first_non_empty(self.settings.chat_qwen_model, self.settings.ai_fallback_model)
        qwen_api_base = self._first_non_empty(self.settings.chat_qwen_api_base, self.settings.ai_fallback_api_base)
        qwen_api_key = self._first_non_empty(self.settings.chat_qwen_api_key, self.settings.ai_fallback_api_key)
        gpt54_api_base = self._first_non_empty(self.settings.chat_gpt54_api_base)
        gpt54_api_key = self._first_non_empty(self.settings.chat_gpt54_api_key)

        specs = {
            "gemini-2.5-flash": ChatLLMPresetSpec(
                key="gemini-2.5-flash",
                label="Gemini 2.5 Flash",
                provider="gemini",
                model="gemini-2.5-flash",
                reasoning_effort=None,
                api_key=gemini_key,
                api_base=None,
                codex_model=None,
                ready=provider_is_ready(
                    LLMProviderSpec(provider="gemini", model="gemini-2.5-flash", api_key=gemini_key)
                ),
                availability_error=None if gemini_key else "Gemini is not configured for chat.",
            ),
            "qwen2.5": ChatLLMPresetSpec(
                key="qwen2.5",
                label="Qwen 2.5",
                provider="openai_compatible",
                model=qwen_model,
                reasoning_effort=None,
                api_key=qwen_api_key,
                api_base=qwen_api_base,
                codex_model=None,
                ready=provider_is_ready(
                    LLMProviderSpec(
                        provider="openai_compatible",
                        model=qwen_model,
                        api_key=qwen_api_key,
                        api_base=qwen_api_base,
                    )
                ),
                availability_error=(
                    None
                    if qwen_model and qwen_api_base
                    else "Qwen is not configured for chat."
                ),
            ),
            "gpt-5.4 xhigh": ChatLLMPresetSpec(
                key="gpt-5.4 xhigh",
                label="GPT-5.4 xhigh",
                provider="openai_compatible",
                model=self._first_non_empty(self.settings.chat_gpt54_model, "gpt-5.4"),
                reasoning_effort=self._first_non_empty(self.settings.chat_gpt54_reasoning_effort, "xhigh"),
                api_key=gpt54_api_key,
                api_base=gpt54_api_base,
                codex_model=None,
                ready=provider_is_ready(
                    LLMProviderSpec(
                        provider="openai_compatible",
                        model=self._first_non_empty(self.settings.chat_gpt54_model, "gpt-5.4"),
                        api_key=gpt54_api_key,
                        api_base=gpt54_api_base,
                        reasoning_effort=self._first_non_empty(self.settings.chat_gpt54_reasoning_effort, "xhigh"),
                    )
                ),
                availability_error=(
                    None
                    if gpt54_api_base and self.settings.chat_gpt54_model
                    else "GPT-5.4 is not configured for chat."
                ),
            ),
            "codex-gateway": ChatLLMPresetSpec(
                key="codex-gateway",
                label="Codex Gateway",
                provider="codex_gateway",
                model=self._first_non_empty(self.settings.codex_gateway_model_label, "gpt-5.3-codex-spark"),
                reasoning_effort=None,
                api_key=self._first_non_empty(self.settings.codex_gateway_api_key),
                api_base=self._first_non_empty(self.settings.codex_gateway_base_url),
                codex_model=self._first_non_empty(self.settings.codex_gateway_codex_model),
                ready=provider_is_ready(
                    LLMProviderSpec(
                        provider="codex_gateway",
                        model=self._first_non_empty(self.settings.codex_gateway_model_label, "gpt-5.3-codex-spark"),
                        api_key=self._first_non_empty(self.settings.codex_gateway_api_key),
                        api_base=self._first_non_empty(self.settings.codex_gateway_base_url),
                    )
                ),
                availability_error=(
                    None
                    if self._first_non_empty(self.settings.codex_gateway_base_url)
                    else "Codex Gateway is not configured for chat."
                ),
            ),
        }
        return specs

    @staticmethod
    def _first_non_empty(*values: str | None) -> str | None:
        for value in values:
            normalized = (value or "").strip()
            if normalized:
                return normalized
        return None


class ChatConversationService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        market_data_service: MarketDataService | None = None,
        news_service: NewsService | None = None,
        calendar_service: CalendarService | None = None,
        research_service: ResearchService | None = None,
        memory_service: MemoryService | None = None,
        macro_context_service: MacroContextService | None = None,
        market_state_service: MarketStateService | None = None,
        llm_presets: ChatLLMPresetService | None = None,
        legacy_chat_service: BotChatService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.market_data_service = market_data_service or MarketDataService()
        self.news_service = news_service or NewsService()
        self.calendar_service = calendar_service or CalendarService()
        self.research_service = research_service or ResearchService()
        self.memory_service = memory_service or MemoryService()
        self.macro_context_service = macro_context_service or MacroContextService()
        self.market_state_service = market_state_service or MarketStateService(settings=self.settings)
        self.llm_presets = llm_presets or ChatLLMPresetService(settings=self.settings)
        self.legacy_chat_service = legacy_chat_service or BotChatService(
            settings=self.settings,
            research_service=self.research_service,
            news_service=self.news_service,
            calendar_service=self.calendar_service,
            macro_context_service=self.macro_context_service,
            market_state_service=self.market_state_service,
        )

    def list_presets(self) -> list[ChatLLMPresetRead]:
        return self.llm_presets.list_presets()

    def list_conversations(
        self,
        session: Session,
        *,
        include_archived: bool = False,
        limit: int = 60,
    ) -> list[ChatConversation]:
        self._ensure_storage(session)
        statement = select(ChatConversation)
        if not include_archived:
            statement = statement.where(ChatConversation.status != "archived")
        statement = statement.order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc()).limit(limit)
        return list(session.scalars(statement).all())

    def create_conversation(self, session: Session, payload: ChatConversationCreate) -> ChatConversation:
        self._ensure_storage(session)
        preferred_llm = (payload.preferred_llm or self.llm_presets.default_preset_key()).strip()
        self.llm_presets.resolve(preferred_llm)
        conversation = ChatConversation(
            title=(payload.title or "Nueva conversación").strip() or "Nueva conversación",
            topic=(payload.topic or "general").strip() or "general",
            status="active",
            labels=self._normalize_labels(payload.labels),
            linked_ticker=self._normalize_ticker(payload.linked_ticker),
            linked_hypothesis_id=payload.linked_hypothesis_id,
            linked_strategy_id=payload.linked_strategy_id,
            preferred_llm=preferred_llm,
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return conversation

    def get_conversation(self, session: Session, conversation_id: int) -> ChatConversation:
        self._ensure_storage(session)
        conversation = session.get(ChatConversation, conversation_id)
        if conversation is None:
            raise ValueError("Conversation not found.")
        return conversation

    def update_conversation(self, session: Session, conversation_id: int, payload: ChatConversationUpdate) -> ChatConversation:
        conversation = self.get_conversation(session, conversation_id)
        if payload.title is not None:
            conversation.title = payload.title.strip() or conversation.title
        if payload.labels is not None:
            conversation.labels = self._normalize_labels(payload.labels)
        if payload.summary is not None:
            conversation.summary = payload.summary.strip() or None
        if payload.preferred_llm is not None:
            resolved = self.llm_presets.resolve(payload.preferred_llm)
            conversation.preferred_llm = resolved.key
        if payload.status is not None:
            normalized_status = payload.status.strip().lower()
            if normalized_status not in ALLOWED_CHAT_STATUSES:
                raise ValueError(f"Unsupported conversation status '{payload.status}'.")
            conversation.status = normalized_status
            conversation.archived_at = datetime.now(timezone.utc) if normalized_status == "archived" else None
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return conversation

    def archive_conversation(self, session: Session, conversation_id: int) -> ChatConversation:
        return self.update_conversation(
            session,
            conversation_id,
            ChatConversationUpdate(status="archived"),
        )

    def get_conversation_detail(self, session: Session, conversation_id: int) -> ChatConversationDetailRead:
        conversation = self.get_conversation(session, conversation_id)
        messages = list(
            session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.id.asc())
            ).all()
        )
        detail = ChatConversationDetailRead.model_validate(conversation)
        detail.messages = [ChatMessageRead.model_validate(item) for item in messages]
        return detail

    def add_message(
        self,
        session: Session,
        conversation_id: int,
        payload: ChatMessageCreate,
    ) -> ChatConversationTurnResponse:
        self._ensure_storage(session)
        conversation = self.get_conversation(session, conversation_id)
        if conversation.status == "archived":
            raise ValueError("Archived conversations cannot receive new messages.")

        requested_llm = (payload.llm_preset or conversation.preferred_llm).strip()
        self.llm_presets.resolve(requested_llm)
        tickers = self._extract_tickers(payload.content)
        intent = self._detect_intent(payload.content, tickers=tickers)
        contribution_kind = self._classify_contribution(payload.content, tickers=tickers)
        user_context = {
            "intent": intent,
            "topic": self._topic_from_intent(intent),
            "tickers": tickers,
            "classification": contribution_kind,
            "requested_llm": requested_llm,
        }

        user_message = ChatMessage(
            conversation_id=conversation.id,
            role="user",
            content=payload.content.strip(),
            message_type=payload.message_type.strip() or "chat",
            context=user_context,
            actions_taken=[],
        )
        session.add(user_message)
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.commit()
        session.refresh(user_message)

        draft = self._build_draft_reply(
            session,
            conversation=conversation,
            user_message=user_message,
            intent=intent,
            contribution_kind=contribution_kind,
            tickers=tickers,
        )
        actions_taken = self._apply_controlled_actions(
            session,
            conversation=conversation,
            user_message=user_message,
            contribution_kind=contribution_kind,
            draft=draft,
        )
        thread_messages = self._recent_thread_messages(session, conversation.id, limit=6)
        assistant_context = {
            "intent": intent,
            "topic": draft["topic"],
            "tickers": tickers,
            "classification": contribution_kind,
            "suggested_action": draft["suggested_action"],
            "confidence": draft["confidence"],
            "linked_entities": self._linked_entities_from_actions(actions_taken),
            "actions_taken": actions_taken,
            "analysis": draft["analysis"],
        }
        rendered_reply, llm_meta = self.llm_presets.render_reply(
            preset_key=requested_llm,
            draft_reply=draft["reply"],
            conversation_title=conversation.title,
            user_message=user_message.content,
            assistant_context=assistant_context,
            thread_messages=thread_messages,
        )
        assistant_context.update(llm_meta)
        assistant_context["preset_available"] = not llm_meta.get("fallback_used", False)
        final_reply = rendered_reply
        if llm_meta.get("fallback_used") and llm_meta.get("provider_error"):
            final_reply = (
                f"Aviso: {llm_meta['provider_error']}\n\n"
                f"{draft['reply']}"
            )

        assistant_message = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=final_reply,
            message_type=draft["message_type"],
            context=assistant_context,
            actions_taken=actions_taken,
        )
        session.add(assistant_message)
        conversation.topic = draft["topic"]
        conversation.summary = draft["summary"]
        conversation.labels = self._merge_labels(conversation.labels, draft["labels"])
        if conversation.linked_ticker is None and tickers:
            conversation.linked_ticker = tickers[0]
        if conversation.title == "Nueva conversación":
            conversation.title = draft["title"]
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.commit()
        session.refresh(assistant_message)
        session.refresh(conversation)

        return ChatConversationTurnResponse(
            conversation=ChatConversationRead.model_validate(conversation),
            user_message=ChatMessageRead.model_validate(user_message),
            assistant_message=ChatMessageRead.model_validate(assistant_message),
        )

    def _build_draft_reply(
        self,
        session: Session,
        *,
        conversation: ChatConversation,
        user_message: ChatMessage,
        intent: str,
        contribution_kind: str,
        tickers: list[str],
    ) -> dict:
        if intent == "ticker_review" and tickers:
            return self._build_ticker_review(session, tickers[0], user_message.content)
        if intent == "investment_idea_discussion":
            return self._build_investment_idea_discussion(
                session,
                conversation=conversation,
                user_message=user_message.content,
                contribution_kind=contribution_kind,
                tickers=tickers,
            )

        legacy = self.legacy_chat_service.reply(session, user_message.content)
        return {
            "topic": legacy.topic,
            "message_type": "chat",
            "title": self._suggest_title(legacy.topic, tickers, user_message.content),
            "summary": legacy.reply[:320],
            "labels": [legacy.topic, *tickers],
            "suggested_action": "none",
            "confidence": 0.5,
            "analysis": legacy.context,
            "reply": legacy.reply,
        }

    def _build_ticker_review(self, session: Session, ticker: str, user_message: str) -> dict:
        snapshot = self.market_data_service.get_snapshot(ticker)
        overview = self.market_data_service.get_market_overview(ticker)
        market_state = self.market_state_service.get_latest_snapshot(session)
        open_positions = list(
            session.scalars(select(Position).where(Position.ticker == ticker, Position.status == "open")).all()
        )
        watchlists = list(
            session.scalars(
                select(Watchlist)
                .join(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
                .where(WatchlistItem.ticker == ticker)
            ).all()
        )
        memories = list(
            session.scalars(
                select(MemoryItem)
                .where(
                    or_(
                        MemoryItem.scope == f"ticker:{ticker}",
                        MemoryItem.content.like(f"%{ticker}%"),
                    )
                )
                .order_by(MemoryItem.created_at.desc())
                .limit(4)
            ).all()
        )
        try:
            news = self.news_service.list_news_for_ticker(ticker, max_results=3)
        except NewsProviderError:
            news = []
        try:
            calendar_events = self.calendar_service.list_ticker_events(ticker, days_ahead=21)
        except CalendarProviderError:
            calendar_events = []

        price = float(snapshot.price)
        bias = "neutral"
        confidence = 0.54
        evidence: list[str] = []
        risks: list[str] = []
        invalidation = f"Pérdida de la estructura diaria y cierre sostenido por debajo de SMA50 en {ticker}."
        recommended_action = "watch"

        if price > snapshot.sma_20 > snapshot.sma_50 and price > snapshot.sma_200 and snapshot.rsi_14 >= 52:
            bias = "bullish"
            confidence = 0.72
            evidence.append(
                f"precio {price:.2f} por encima de SMA20/SMA50/SMA200 con RSI {snapshot.rsi_14:.1f}"
            )
            evidence.append(f"volumen relativo {snapshot.relative_volume:.2f}x")
            recommended_action = "candidate_for_strategy" if snapshot.relative_volume >= 1.0 else "watch"
        elif price < snapshot.sma_50 < snapshot.sma_200 and snapshot.rsi_14 <= 45:
            bias = "bearish"
            confidence = 0.68
            evidence.append(
                f"precio {price:.2f} por debajo de SMA50/SMA200 con RSI {snapshot.rsi_14:.1f}"
            )
            risks.append("la estructura diaria sigue bajista; no es un long limpio")
            recommended_action = "reject"
        elif abs(price - snapshot.sma_50) / max(snapshot.sma_50, 0.01) < 0.015:
            bias = "too_early"
            confidence = 0.5
            evidence.append("el precio está muy cerca de una zona bisagra diaria")
            risks.append("todavía no hay expansión clara ni fallo claro de estructura")
            recommended_action = "research"
        else:
            bias = "insufficient_evidence"
            confidence = 0.42
            evidence.append("las señales diarias no forman todavía una tesis limpia")
            recommended_action = "research"

        options_sentiment = overview.get("options_sentiment", {}) if isinstance(overview, dict) else {}
        put_call_ratio = options_sentiment.get("put_call_ratio")
        if put_call_ratio is not None:
            evidence.append(f"put/call {put_call_ratio}")
        if calendar_events:
            risks.append(f"evento corporativo cercano: {calendar_events[0].title} {calendar_events[0].event_date}")
        if open_positions:
            risks.append("ya existe exposición abierta en este ticker")
        if watchlists:
            evidence.append(f"ya está en watchlists activas: {', '.join(item.name for item in watchlists[:2])}")
        if market_state is not None:
            evidence.append(f"market state: {market_state.regime_label}")
        if memories:
            evidence.append("existe memoria previa del sistema para este ticker")

        thesis = {
            "bullish": f"Mi sesgo en {ticker} es bullish mientras la estructura diaria siga ordenada y el contexto no se deteriore.",
            "neutral": f"Mi sesgo en {ticker} es neutral; hay señales mixtas y prefiero confirmación adicional.",
            "bearish": f"Mi sesgo en {ticker} es bearish para nuevas entradas largas; la estructura actual no compensa.",
            "too_early": f"Mi sesgo en {ticker} es too_early; hay una posible idea, pero el timing todavía no está maduro.",
            "insufficient_evidence": f"Mi sesgo en {ticker} es insufficient_evidence; faltan pruebas para una tesis operable.",
        }[bias]

        contraargument = risks[0] if risks else "no veo un contraargumento dominante más allá de ejecución y contexto."
        action_line = {
            "watch": "watch",
            "research": "research",
            "reject": "reject",
            "candidate_for_strategy": "candidate_for_strategy",
        }[recommended_action]
        reply = (
            f"Sesgo actual: {bias}.\n\n"
            f"Tesis: {thesis}\n\n"
            f"Evidencias a favor:\n- " + "\n- ".join(evidence[:4]) + "\n\n"
            f"Riesgos o contraargumentos:\n- " + "\n- ".join((risks or [contraargument])[:4]) + "\n\n"
            f"Invalidación: {invalidation}\n"
            f"Decisión operativa: {action_line}."
        )
        return {
            "topic": "ticker_review",
            "message_type": "ticker_review",
            "title": self._suggest_title("ticker_review", [ticker], user_message),
            "summary": f"{ticker}: {bias} con decisión {action_line}.",
            "labels": ["ticker", ticker, bias],
            "suggested_action": action_line,
            "confidence": confidence,
            "analysis": {
                "bias": bias,
                "ticker": ticker,
                "evidence": evidence[:4],
                "risks": risks[:4],
                "invalidation": invalidation,
                "market_state": market_state.regime_label if market_state is not None else None,
                "open_positions": len(open_positions),
                "watchlists": [item.name for item in watchlists[:3]],
                "memory_count": len(memories),
                "news_titles": [item.title for item in news],
                "calendar_titles": [item.title for item in calendar_events[:3]],
            },
            "reply": reply,
        }

    def _build_investment_idea_discussion(
        self,
        session: Session,
        *,
        conversation: ChatConversation,
        user_message: str,
        contribution_kind: str,
        tickers: list[str],
    ) -> dict:
        market_state = self.market_state_service.get_latest_snapshot(session)
        macro_context = self.macro_context_service.get_context(session, limit=4).model_dump(mode="json")
        classification = contribution_kind
        summary = self._summarize_idea(user_message, tickers=tickers, classification=classification)
        gaps = self._identify_idea_gaps(user_message, tickers=tickers, classification=classification)
        suggested_action = "none"
        confidence = 0.56
        stance = "La idea es razonable, pero no la trataría como lista para ejecución."

        if classification in {"research_request", "ticker_thesis"}:
            suggested_action = "research"
            confidence = 0.64
        elif classification in {"workflow_improvement", "risk_flag", "macro_thesis", "strategy_idea"}:
            suggested_action = "memory"
            confidence = 0.62
        elif classification == "observation":
            suggested_action = "memory"

        if market_state is not None and "risk_off" in (market_state.regime_label or "") and classification == "ticker_thesis":
            stance = "No estoy de acuerdo con tratar esta tesis como operable ahora; el régimen actual exige más pruebas."
            suggested_action = "research"
            confidence = 0.58
        elif classification == "workflow_improvement":
            stance = "Esto encaja más como mejora del sistema que como tesis de inversión."
        elif classification == "risk_flag":
            stance = "Esto merece registrarse como bandera de riesgo, no como trigger direccional."
        elif classification == "ticker_thesis" and tickers:
            stance = (
                f"La tesis sobre {tickers[0]} puede ser interesante, pero la degradaría a research hasta validar "
                "contexto, nivel de invalidación y catalizadores."
            )

        validation_steps = [
            "definir claramente la hipótesis falsable y la invalidación",
            "comparar la idea con el market state y el régimen activo",
            "comprobar catalizadores, eventos y exposición ya abierta",
        ]
        if tickers:
            validation_steps.append(f"revisar estructura diaria y contexto de {tickers[0]}")
        if classification in {"macro_thesis", "risk_flag"}:
            validation_steps.append("medir si el tema afecta watchlists o posiciones ya abiertas")

        reply = (
            f"Clasificación: {classification}.\n\n"
            f"Resumen: {summary}\n\n"
            f"Evaluación crítica: {stance}\n\n"
            f"Huecos detectados:\n- " + "\n- ".join(gaps[:4]) + "\n\n"
            f"Cómo lo validaría:\n- " + "\n- ".join(validation_steps[:4]) + "\n\n"
            f"Promoción sugerida: {suggested_action}."
        )
        return {
            "topic": "investment_idea_discussion",
            "message_type": "idea_discussion",
            "title": self._suggest_title("investment_idea_discussion", tickers, user_message),
            "summary": summary[:320],
            "labels": [classification, *tickers],
            "suggested_action": suggested_action,
            "confidence": confidence,
            "analysis": {
                "classification": classification,
                "tickers": tickers,
                "market_state": market_state.regime_label if market_state is not None else None,
                "macro_regimes": macro_context.get("active_regimes", [])[:3],
                "gaps": gaps[:5],
            },
            "reply": reply,
        }

    def _apply_controlled_actions(
        self,
        session: Session,
        *,
        conversation: ChatConversation,
        user_message: ChatMessage,
        contribution_kind: str,
        draft: dict,
    ) -> list[dict]:
        actions: list[dict] = []
        tickers = list(draft.get("analysis", {}).get("tickers", [])) or self._extract_tickers(user_message.content)
        if draft["suggested_action"] in {"memory", "research"} or contribution_kind in {
            "workflow_improvement",
            "risk_flag",
            "macro_thesis",
            "ticker_thesis",
            "research_request",
            "strategy_idea",
            "observation",
        }:
            memory = self.memory_service.create_item(
                session,
                payload=self._build_memory_payload(
                    conversation=conversation,
                    user_message=user_message,
                    contribution_kind=contribution_kind,
                    draft=draft,
                    tickers=tickers,
                ),
            )
            actions.append(
                {
                    "action": "memory_saved",
                    "memory_id": memory.id,
                    "scope": memory.scope,
                    "importance": memory.importance,
                }
            )

        if draft["suggested_action"] == "research":
            task = self._ensure_research_task(
                session,
                conversation=conversation,
                user_message=user_message,
                contribution_kind=contribution_kind,
                draft=draft,
                tickers=tickers,
            )
            if task is not None:
                actions.append(
                    {
                        "action": "research_task_created",
                        "research_task_id": task.id,
                        "title": task.title,
                    }
                )
        return actions

    def _build_memory_payload(
        self,
        *,
        conversation: ChatConversation,
        user_message: ChatMessage,
        contribution_kind: str,
        draft: dict,
        tickers: list[str],
    ):
        scope = f"ticker:{tickers[0]}" if tickers else "chat:ideas"
        if contribution_kind == "workflow_improvement":
            scope = "workflow:chat"
        elif contribution_kind == "macro_thesis":
            scope = "macro:chat"
        elif contribution_kind == "risk_flag":
            scope = "risk:chat"
        key_seed = f"{conversation.id}:{user_message.id}:{contribution_kind}:{tickers[:1]}"
        key = re.sub(r"[^a-zA-Z0-9:_-]+", "-", key_seed)[:120]
        importance = 0.58
        if contribution_kind in {"risk_flag", "workflow_improvement"}:
            importance = 0.7
        elif contribution_kind in {"research_request", "ticker_thesis"}:
            importance = 0.64
        return MemoryItemCreate(
            memory_type="chat_insight",
            scope=scope,
            key=key or f"chat:{conversation.id}:{user_message.id}",
            content=draft["summary"],
            meta={
                "conversation_id": conversation.id,
                "message_id": user_message.id,
                "classification": contribution_kind,
                "tickers": tickers,
                "topic": draft["topic"],
                "suggested_action": draft["suggested_action"],
            },
            importance=importance,
        )

    def _ensure_research_task(
        self,
        session: Session,
        *,
        conversation: ChatConversation,
        user_message: ChatMessage,
        contribution_kind: str,
        draft: dict,
        tickers: list[str],
    ) -> ResearchTask | None:
        title = self._suggest_research_title(contribution_kind, tickers, user_message.content)
        existing = session.scalars(
            select(ResearchTask).where(
                ResearchTask.title == title,
                ResearchTask.status.in_(["open", "in_progress"]),
            )
        ).first()
        if existing is not None:
            return existing
        return self.research_service.create_task(
            session,
            ResearchTaskCreate(
                task_type="chat_research_followup",
                priority="normal",
                title=title,
                hypothesis=draft["summary"],
                scope={
                    "conversation_id": conversation.id,
                    "message_id": user_message.id,
                    "classification": contribution_kind,
                    "tickers": tickers,
                    "suggested_action": draft["suggested_action"],
                },
            ),
        )

    @staticmethod
    def _linked_entities_from_actions(actions: list[dict]) -> dict:
        linked: dict[str, list[int]] = {}
        for action in actions:
            if "memory_id" in action:
                linked.setdefault("memory_ids", []).append(action["memory_id"])
            if "research_task_id" in action:
                linked.setdefault("research_task_ids", []).append(action["research_task_id"])
        return linked

    @staticmethod
    def _recent_thread_messages(session: Session, conversation_id: int, *, limit: int = 6) -> list[dict]:
        rows = list(
            session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            ).all()
        )
        rows.reverse()
        return [
            {
                "role": item.role,
                "message_type": item.message_type,
                "content": item.content,
            }
            for item in rows
        ]

    @staticmethod
    def _ensure_storage(session: Session) -> None:
        bind = session.get_bind()
        if bind is None:
            return
        try:
            ChatConversation.__table__.create(bind=bind, checkfirst=True)
            ChatMessage.__table__.create(bind=bind, checkfirst=True)
        except OperationalError as exc:
            if "already exists" in str(exc).lower():
                return
            raise

    @staticmethod
    def _normalize_labels(labels: list[str]) -> list[str]:
        return sorted({label.strip().lower() for label in labels if isinstance(label, str) and label.strip()})

    @staticmethod
    def _merge_labels(existing: list[str], incoming: list[str]) -> list[str]:
        return sorted(
            {
                *(label.strip().lower() for label in (existing or []) if isinstance(label, str) and label.strip()),
                *(label.strip().lower() for label in (incoming or []) if isinstance(label, str) and label.strip()),
            }
        )

    @staticmethod
    def _normalize_ticker(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = value.strip().upper()
        return cleaned or None

    def _detect_intent(self, message: str, *, tickers: list[str]) -> str:
        text = self._normalize_text(message)
        if any(token in text for token in ["idea", "tesis", "pienso", "enfoque", "estrategia", "workflow", "riesgo", "research"]):
            return "investment_idea_discussion"
        if tickers and any(token in text for token in ["ticker", "sesgo", "opinas", "review", "revis", "analiza"]):
            return "ticker_review"
        if tickers and ("?" in message or any(token in text for token in ["merece", "buy", "long", "short"])):
            return "ticker_review"
        return "general_chat"

    def _classify_contribution(self, message: str, *, tickers: list[str]) -> str:
        text = self._normalize_text(message)
        if any(token in text for token in ["mejora", "workflow", "flujo", "sistema", "ui", "chat"]):
            return "workflow_improvement"
        if any(token in text for token in ["investiga", "research", "valida", "comprueba", "compruebe"]):
            return "research_request"
        if any(token in text for token in ["riesgo", "warning", "bandera", "cuidado"]):
            return "risk_flag"
        if any(token in text for token in ["macro", "fed", "inflacion", "petroleo", "geopolit"]):
            return "macro_thesis"
        if any(token in text for token in ["estrategia", "setup", "playbook", "reversion", "breakout"]):
            return "strategy_idea"
        if tickers:
            return "ticker_thesis"
        return "observation"

    @staticmethod
    def _topic_from_intent(intent: str) -> str:
        return {
            "ticker_review": "ticker_review",
            "investment_idea_discussion": "idea_discussion",
        }.get(intent, "general")

    @staticmethod
    def _normalize_text(message: str) -> str:
        normalized = unicodedata.normalize("NFKD", message.lower())
        return "".join(char for char in normalized if not unicodedata.combining(char))

    @classmethod
    def _extract_tickers(cls, message: str) -> list[str]:
        tickers: list[str] = []
        seen: set[str] = set()
        for match in re.finditer(r"\$?[A-Za-z]{1,5}\b", message):
            raw = match.group(0)
            cleaned = raw.replace("$", "").strip().upper()
            if not raw.startswith("$") and not raw.isupper():
                continue
            if not cleaned or cleaned in STOPWORD_TICKERS or cleaned in seen:
                continue
            seen.add(cleaned)
            tickers.append(cleaned)
        return tickers[:4]

    @staticmethod
    def _summarize_idea(message: str, *, tickers: list[str], classification: str) -> str:
        cleaned = " ".join(message.strip().split())
        prefix = f"Idea sobre {', '.join(tickers)}. " if tickers else ""
        return prefix + cleaned[:260]

    @staticmethod
    def _identify_idea_gaps(message: str, *, tickers: list[str], classification: str) -> list[str]:
        gaps = []
        normalized = ChatConversationService._normalize_text(message)
        if not tickers and classification in {"ticker_thesis", "research_request"}:
            gaps.append("falta concretar el ticker o el universo afectado")
        if "stop" not in normalized and "inval" not in normalized:
            gaps.append("no aparece una invalidación o nivel que rompa la tesis")
        if "porque" not in normalized and "por que" not in normalized:
            gaps.append("la causalidad o el edge no está argumentado con suficiente precisión")
        if not any(token in normalized for token in ["dato", "volumen", "precio", "regimen", "earnings", "evento"]):
            gaps.append("faltan pruebas observables o catalizadores a comprobar")
        if classification == "workflow_improvement":
            gaps.append("conviene concretar impacto esperado y cómo medir si la mejora funciona")
        return gaps or ["la idea necesita validación cruzada con contexto y resultados previos"]

    @staticmethod
    def _suggest_title(topic: str, tickers: list[str], message: str) -> str:
        if tickers and topic == "ticker_review":
            return f"{tickers[0]} review"
        if tickers and topic == "investment_idea_discussion":
            return f"Idea sobre {tickers[0]}"
        if topic == "macro":
            return "Debate macro"
        words = [word for word in re.split(r"\s+", message.strip()) if word][:6]
        return " ".join(words)[:160] or "Nueva conversación"

    @staticmethod
    def _suggest_research_title(contribution_kind: str, tickers: list[str], message: str) -> str:
        if tickers:
            return f"Validate chat thesis for {tickers[0]}"
        if contribution_kind == "workflow_improvement":
            return "Evaluate chat workflow improvement"
        return f"Validate chat idea: {' '.join(message.strip().split()[:6])}"[:160]
