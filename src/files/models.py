from typing import Optional

from sqlalchemy import Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class FileAsset(SQLModel, table=True):
    __tablename__ = "file_assets"

    file_ref: str = Field(primary_key=True, max_length=64)
    name: str
    mime_type: str = "application/octet-stream"
    size: int = 0
    sha256: str = ""
    origin: str = "remote"
    state: str = "remote"
    source: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    source_key: Optional[str] = Field(default=None, unique=True)
    error: str = ""
    created_at: float
    accessed_at: float
    reserved_bytes: int = 0
    leased_until: float = 0
    open_readers: int = 0
    transfer_id: str = ""
    transfer_started: float = 0


class FileBinding(SQLModel, table=True):
    __tablename__ = "file_bindings"
    __table_args__ = (UniqueConstraint("file_ref", "session_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    file_ref: str = Field(foreign_key="file_assets.file_ref", index=True)
    session_id: str = Field(index=True)
    message_id: str = ""
    created_at: float


class FileParse(SQLModel, table=True):
    __tablename__ = "file_parses"

    file_ref: str = Field(primary_key=True, foreign_key="file_assets.file_ref")
    parser_version: str = "text-v1"
    encoding: str = ""
    state: str = "pending"
    error: str = ""
    updated_at: float
