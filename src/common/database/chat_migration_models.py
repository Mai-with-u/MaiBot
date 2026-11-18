"""聊天迁移任务及复制来源账本。"""

from sqlalchemy import Column, Index, Integer, String, Table, Text, UniqueConstraint
from sqlmodel import SQLModel


metadata = SQLModel.metadata
jobs = Table(
    "chat_migration_jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("source", String, nullable=False),
    Column("target", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("payload", Text, nullable=False),
    Column("error", Text, nullable=False, default=""),
)
copies = Table(
    "chat_migration_copies",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("table_name", String, nullable=False),
    Column("origin", String, nullable=False),
    Column("target", String, nullable=False),
    Column("target_key", Text, nullable=False),
    Column("amounts", Text, nullable=False, default="{}", server_default="{}"),
    UniqueConstraint("table_name", "origin", "target"),
    Index("ix_chat_migration_copies_table_key", "table_name", "target_key"),
)
