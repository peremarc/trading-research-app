from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    topic: Mapped[str] = mapped_column(String(40), default="general", index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    linked_ticker: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    linked_hypothesis_id: Mapped[int | None] = mapped_column(ForeignKey("hypotheses.id"), nullable=True, index=True)
    linked_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    preferred_llm: Mapped[str] = mapped_column(String(40), default="gemini-2.5-flash")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )
