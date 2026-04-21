"""chat conversations

Revision ID: 20260419_0016
Revises: 20260418_0015
Create Date: 2026-04-19 18:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260419_0016"
down_revision: Union[str, None] = "20260418_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "chat_conversations" not in existing_tables:
        op.create_table(
            "chat_conversations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("topic", sa.String(length=40), nullable=False, server_default="general"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("linked_ticker", sa.String(length=12), nullable=True),
            sa.Column("linked_hypothesis_id", sa.Integer(), nullable=True),
            sa.Column("linked_strategy_id", sa.Integer(), nullable=True),
            sa.Column("preferred_llm", sa.String(length=40), nullable=False, server_default="gemini-2.5-flash"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["linked_hypothesis_id"], ["hypotheses.id"]),
            sa.ForeignKeyConstraint(["linked_strategy_id"], ["strategies.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "chat_messages" not in existing_tables:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("message_type", sa.String(length=40), nullable=False, server_default="chat"),
            sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("actions_taken", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    conversation_indexes = {item["name"] for item in inspector.get_indexes("chat_conversations")}
    if "ix_chat_conversations_id" not in conversation_indexes:
        op.create_index("ix_chat_conversations_id", "chat_conversations", ["id"])
    if "ix_chat_conversations_topic" not in conversation_indexes:
        op.create_index("ix_chat_conversations_topic", "chat_conversations", ["topic"])
    if "ix_chat_conversations_status" not in conversation_indexes:
        op.create_index("ix_chat_conversations_status", "chat_conversations", ["status"])
    if "ix_chat_conversations_linked_ticker" not in conversation_indexes:
        op.create_index("ix_chat_conversations_linked_ticker", "chat_conversations", ["linked_ticker"])
    if "ix_chat_conversations_linked_hypothesis_id" not in conversation_indexes:
        op.create_index("ix_chat_conversations_linked_hypothesis_id", "chat_conversations", ["linked_hypothesis_id"])
    if "ix_chat_conversations_linked_strategy_id" not in conversation_indexes:
        op.create_index("ix_chat_conversations_linked_strategy_id", "chat_conversations", ["linked_strategy_id"])

    message_indexes = {item["name"] for item in inspector.get_indexes("chat_messages")}
    if "ix_chat_messages_id" not in message_indexes:
        op.create_index("ix_chat_messages_id", "chat_messages", ["id"])
    if "ix_chat_messages_conversation_id" not in message_indexes:
        op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])
    if "ix_chat_messages_role" not in message_indexes:
        op.create_index("ix_chat_messages_role", "chat_messages", ["role"])
    if "ix_chat_messages_message_type" not in message_indexes:
        op.create_index("ix_chat_messages_message_type", "chat_messages", ["message_type"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_message_type", table_name="chat_messages")
    op.drop_index("ix_chat_messages_role", table_name="chat_messages")
    op.drop_index("ix_chat_messages_conversation_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_id", table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index("ix_chat_conversations_linked_strategy_id", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_linked_hypothesis_id", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_linked_ticker", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_status", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_topic", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_id", table_name="chat_conversations")
    op.drop_table("chat_conversations")
