"""新增独立相通组；旧配置数据在宿主加载配置后事务性导入。"""

from src.common.database.sharing_models import (
    SharingGroup,
    SharingGroupCapability,
    SharingGroupMember,
    SharingGroupMigration,
    SharingGroupRule,
)

from .models import MigrationExecutionContext


def migrate_v48_to_v49(context: MigrationExecutionContext) -> None:
    for model in (SharingGroup, SharingGroupMember, SharingGroupCapability, SharingGroupRule, SharingGroupMigration):
        model.__table__.create(context.connection, checkfirst=True)
