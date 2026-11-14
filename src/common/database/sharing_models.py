"""独立相通组的持久化模型。成员与规则分开，能力可按标识扩展。"""

from typing import Optional

from sqlmodel import Field, SQLModel


class SharingGroup(SQLModel, table=True):
    __tablename__ = "sharing_groups"
    id: str = Field(primary_key=True)
    name: str
    color: str
    note: str = ""
    enabled: bool = True
    revision: int = 1
    created_at: float
    updated_at: float


class SharingGroupMember(SQLModel, table=True):
    __tablename__ = "sharing_group_members"
    group_id: str = Field(primary_key=True, foreign_key="sharing_groups.id")
    session_id: str = Field(primary_key=True, index=True)
    created_at: float


class SharingGroupCapability(SQLModel, table=True):
    __tablename__ = "sharing_group_capabilities"
    group_id: str = Field(primary_key=True, foreign_key="sharing_groups.id")
    capability: str = Field(primary_key=True)
    enabled: bool = True


class SharingGroupRule(SQLModel, table=True):
    __tablename__ = "sharing_group_rules"
    id: str = Field(primary_key=True)
    group_id: str = Field(foreign_key="sharing_groups.id", index=True)
    platform: str
    item_id: str
    rule_type: str


class SharingGroupMigration(SQLModel, table=True):
    __tablename__ = "sharing_group_migrations"
    source: str = Field(primary_key=True)
    group_id: Optional[str] = None
    payload: str
    migrated_at: float
