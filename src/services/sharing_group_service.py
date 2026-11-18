"""相通组唯一数据来源。运行时读取快照，只展开当前会话直接所属的组。"""

from threading import RLock
from time import time
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, update
from sqlmodel import select

import json

from src.common.database.database import get_db_session
from src.common.database.sharing_models import (
    SharingGroup,
    SharingGroupCapability,
    SharingGroupMember,
    SharingGroupMigration,
    SharingGroupRule,
)

CAPABILITIES = {"expression": "表达", "jargon": "黑话", "memory": "记忆"}
COLORS = ("#39845A", "#397FAE", "#B97132", "#9863A5", "#B45C6E", "#438E91")
_lock = RLock()
_snapshot: Optional[List[Dict[str, Any]]] = None


class MemberRule(BaseModel):
    platform: str = Field(min_length=1, max_length=100)
    item_id: str = Field(min_length=1, max_length=255)
    rule_type: Literal["group", "private", "all"] = "group"


class GroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    note: str = Field(default="", max_length=4000)
    enabled: bool = True
    revision: Optional[int] = Field(default=None, ge=1)
    member_ids: List[str] = Field(default_factory=list)
    rules: List[MemberRule] = Field(default_factory=list)
    capabilities: Dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_group(self) -> "GroupInput":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("相通组名称不能为空")
        if set(self.capabilities) - CAPABILITIES.keys():
            raise ValueError("未知的共享能力")
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("成员不能重复")
        keys = [(rule.platform, rule.item_id, rule.rule_type) for rule in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("成员规则不能重复")
        return self


def _rule_ids(rule: Dict[str, str]) -> Set[str]:
    from src.common.utils.utils_config import ChatConfigUtils

    if rule["rule_type"] == "all":
        return set().union(*(_rule_ids({**rule, "rule_type": kind}) for kind in ("group", "private")))
    return ChatConfigUtils.get_target_session_ids_with_wildcards(rule)


def _load_snapshot() -> None:
    global _snapshot
    with get_db_session(auto_commit=False) as session:
        groups = session.exec(select(SharingGroup).order_by(SharingGroup.created_at, SharingGroup.id)).all()
        members = session.exec(select(SharingGroupMember)).all()
        rules = session.exec(select(SharingGroupRule)).all()
        capabilities = session.exec(select(SharingGroupCapability)).all()
        result = []
        for group in groups:
            data = group.model_dump()
            data["member_ids"] = [member.session_id for member in members if member.group_id == group.id]
            data["rules"] = [
                {"platform": rule.platform, "item_id": rule.item_id, "rule_type": rule.rule_type}
                for rule in rules
                if rule.group_id == group.id
            ]
            data["capabilities"] = {item.capability: item.enabled for item in capabilities if item.group_id == group.id}
            data["resolved_ids"] = sorted(set(data["member_ids"]).union(*(_rule_ids(rule) for rule in data["rules"])))
            result.append(data)
    _snapshot = result


def refresh() -> None:
    """外部事务更新成员关系后重新加载共享范围。"""
    with _lock:
        _load_snapshot()


def initialize() -> None:
    """由宿主在线程池初始化；整个旧配置导入及完成标记一次事务提交。"""
    from src.config.config import global_config

    with _lock:
        if _snapshot is not None:
            return
        with get_db_session() as session:
            # SQLite 写锁使并行启动的宿主也只能导入一次。
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if session.get(SharingGroupMigration, "config-v1-complete") is None:
                sources = {
                    "expression": global_config.expression.expression_groups,
                    "jargon": global_config.jargon.jargon_groups,
                    "memory": global_config.a_memorix.shared_memory_groups,
                }
                serial = 0
                for capability, old_groups in sources.items():
                    for index, old_group in enumerate(old_groups):
                        group_id = str(uuid4())
                        now = time()
                        session.add(
                            SharingGroup(
                                id=group_id,
                                name=f"{CAPABILITIES[capability]}相通 {index + 1}",
                                color=COLORS[serial % len(COLORS)],
                                created_at=now,
                                updated_at=now,
                            )
                        )
                        session.flush()
                        session.add(SharingGroupCapability(group_id=group_id, capability=capability))
                        seen = set()
                        for target in old_group.targets:
                            # 全平台全目标的旧表达/黑话配置原本就是全局范围。
                            rule_type = (
                                "all"
                                if capability != "memory" and (target.platform, target.item_id) == ("*", "*")
                                else target.rule_type
                            )
                            key = (target.platform, target.item_id, rule_type)
                            if key in seen or not target.platform or not target.item_id:
                                continue
                            seen.add(key)
                            session.add(
                                SharingGroupRule(
                                    id=str(uuid4()),
                                    group_id=group_id,
                                    platform=target.platform,
                                    item_id=target.item_id,
                                    rule_type=rule_type,
                                )
                            )
                        session.add(
                            SharingGroupMigration(
                                source=f"{capability}:{index}",
                                group_id=group_id,
                                payload=old_group.model_dump_json(),
                                migrated_at=now,
                            )
                        )
                        serial += 1
                if global_config.a_memorix.global_memory_sharing_enabled:
                    now = time()
                    group_id = str(uuid4())
                    session.add(
                        SharingGroup(
                            id=group_id,
                            name="全部聊天的记忆",
                            color=COLORS[serial % len(COLORS)],
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    session.flush()
                    session.add(SharingGroupCapability(group_id=group_id, capability="memory"))
                    session.add(
                        SharingGroupRule(id=str(uuid4()), group_id=group_id, platform="*", item_id="*", rule_type="all")
                    )
                    session.add(
                        SharingGroupMigration(
                            source="memory:global", group_id=group_id, payload="true", migrated_at=now
                        )
                    )
                session.add(SharingGroupMigration(source="config-v1-complete", payload="{}", migrated_at=time()))
        _load_snapshot()


def list_groups() -> List[Dict[str, Any]]:
    """同步管理端调用，可初始化数据库；返回副本，避免调用方修改运行时快照。"""
    initialize()
    return json.loads(json.dumps(_snapshot))


def resolved_members(group: Dict[str, Any]) -> Set[str]:
    """快照包含数据库内成员，新增的运行中聊天流按规则动态匹配，不阻塞事件循环。"""
    from src.chat.message_receive.chat_manager import chat_manager

    ids = set(group["resolved_ids"])
    for chat in tuple(chat_manager.sessions.values()):
        for rule in group["rules"]:
            target = chat.group_id if chat.is_group_session else chat.user_id
            kind = "group" if chat.is_group_session else "private"
            if rule["rule_type"] not in ("all", kind):
                continue
            if rule["platform"] in ("*", chat.platform) and rule["item_id"] in ("*", target):
                ids.add(chat.session_id)
    return ids


def resolve_scope(session_id: Optional[str], capability: str) -> Tuple[Set[str], bool]:
    result = {session_id} if session_id else set()
    if not session_id:
        return result, False
    if _snapshot is None:
        raise RuntimeError("相通组服务尚未初始化")
    global_scope = False
    for group in _snapshot:
        if not group["enabled"] or not group["capabilities"].get(capability, False):
            continue
        all_chats = any(rule == {"platform": "*", "item_id": "*", "rule_type": "all"} for rule in group["rules"])
        members = resolved_members(group)
        # 判断的是原始请求会话，不能用不断扩大的 result 判断下一组。
        if session_id in members or all_chats:
            result.update(members)
            global_scope |= all_chats
    return result, global_scope


def save_group(data: GroupInput, group_id: Optional[str] = None) -> Dict[str, Any]:
    from src.chat.message_receive.chat_manager import chat_manager

    initialize()
    with _lock:
        current = next((group for group in _snapshot if group["id"] == group_id), None)
        if group_id is not None and current is None:
            raise KeyError(group_id)
        existing_ids = set(current["member_ids"]) if current else set()
        for session_id in set(data.member_ids) - existing_ids:
            if chat_manager.get_existing_session_by_session_id(session_id) is None:
                raise ValueError(f"聊天流不存在：{session_id}")
        now = time()
        with get_db_session() as session:
            if group_id is None:
                group_id = str(uuid4())
                session.add(
                    SharingGroup(
                        id=group_id,
                        name=data.name,
                        color=data.color,
                        note=data.note,
                        enabled=data.enabled,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.flush()
            else:
                statement = (
                    update(SharingGroup)
                    .where(SharingGroup.id == group_id, SharingGroup.revision == data.revision)
                    .values(
                        name=data.name,
                        color=data.color,
                        note=data.note,
                        enabled=data.enabled,
                        updated_at=now,
                        revision=SharingGroup.revision + 1,
                    )
                )
                if session.execute(statement).rowcount != 1:
                    raise ValueError("相通组已被修改，请刷新后重试")
            for model in (SharingGroupMember, SharingGroupRule, SharingGroupCapability):
                session.execute(delete(model).where(model.group_id == group_id))
            for session_id in data.member_ids:
                session.add(SharingGroupMember(group_id=group_id, session_id=session_id, created_at=now))
            for rule in data.rules:
                session.add(SharingGroupRule(id=str(uuid4()), group_id=group_id, **rule.model_dump()))
            for capability, enabled in data.capabilities.items():
                session.add(SharingGroupCapability(group_id=group_id, capability=capability, enabled=enabled))
        _load_snapshot()
        return next(group for group in list_groups() if group["id"] == group_id)


def delete_group(group_id: str, revision: int) -> None:
    initialize()
    with _lock:
        with get_db_session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            group = session.get(SharingGroup, group_id)
            if group is None:
                raise KeyError(group_id)
            if group.revision != revision:
                raise ValueError("相通组已被修改，请刷新后重试")
            for model in (SharingGroupMember, SharingGroupRule, SharingGroupCapability):
                session.execute(delete(model).where(model.group_id == group_id))
            session.delete(group)
        _load_snapshot()
