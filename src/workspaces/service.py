"""Workspace 子系统数据访问、会话归属解析与策略计算。"""

from datetime import datetime
from typing import Iterable, Optional
from uuid import uuid4

import json

from sqlmodel import col, func, select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    ChatSession,
    MemorySpace,
    MemorySpaceACL,
    PersonaProfile,
    Workspace,
    WorkspaceAuditLog,
    WorkspaceMembership,
    WorkspacePluginPolicy,
    WorkspaceSelector,
    WorkspaceToolPolicy,
)
from src.common.logger import get_logger

from .context import PersonaOverlay, WorkspaceContext

logger = get_logger("workspace")

DEFAULT_WORKSPACE_ID = "workspace-default"
PUBLIC_MEMORY_SPACE_ID = "memory-space-public"


class WorkspaceService:
    """管理工作区，并将真实 ChatSession 解析为唯一主工作区。"""

    def ensure_defaults(self) -> tuple[Workspace, MemorySpace]:
        """幂等建立兼容现有行为的默认工作区和公共记忆空间。"""

        now = datetime.now()
        with get_db_session() as session:
            memory_space = session.get(MemorySpace, PUBLIC_MEMORY_SPACE_ID)
            if memory_space is None:
                memory_space = MemorySpace(
                    id=PUBLIC_MEMORY_SPACE_ID,
                    name="公共记忆库",
                    description="兼容现有 MaiBot 记忆行为的默认公共空间",
                    space_type="public",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(memory_space)
                session.flush()

            workspace = session.get(Workspace, DEFAULT_WORKSPACE_ID)
            if workspace is None:
                workspace = Workspace(
                    id=DEFAULT_WORKSPACE_ID,
                    name="默认子系统",
                    description="所有未显式分配聊天的兼容工作区",
                    memory_space_id=memory_space.id,
                    is_default=True,
                    enabled=True,
                    inherit_global_tools=True,
                    inherit_global_plugins=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(workspace)
            return workspace, memory_space

    def list_workspaces(self) -> list[Workspace]:
        self.ensure_defaults()
        with get_db_session() as session:
            return list(session.exec(select(Workspace).order_by(col(Workspace.is_default).desc(), Workspace.name)).all())

    def list_memory_spaces(self) -> list[MemorySpace]:
        self.ensure_defaults()
        with get_db_session() as session:
            return list(session.exec(select(MemorySpace).order_by(MemorySpace.name)).all())

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        self.ensure_defaults()
        with get_db_session() as session:
            return session.get(Workspace, workspace_id)

    def get_membership_counts(self) -> dict[str, int]:
        with get_db_session() as session:
            rows = session.exec(
                select(WorkspaceMembership.workspace_id, func.count(WorkspaceMembership.id)).group_by(
                    WorkspaceMembership.workspace_id
                )
            ).all()
        return {str(workspace_id): int(count) for workspace_id, count in rows}

    def create_workspace(
        self,
        *,
        name: str,
        description: str = "",
        memory_mode: str = "private",
        memory_space_id: str = "",
        inherit_global_tools: bool = True,
        inherit_global_plugins: bool = True,
    ) -> Workspace:
        """创建工作区；默认同时建立独立逻辑记忆空间。"""

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("子系统名称不能为空")
        if memory_mode not in {"private", "public", "existing"}:
            raise ValueError("memory_mode 必须为 private、public 或 existing")

        self.ensure_defaults()
        now = datetime.now()
        workspace_id = f"workspace-{uuid4().hex}"
        with get_db_session() as session:
            existing = session.exec(select(Workspace).where(Workspace.name == normalized_name)).first()
            if existing is not None:
                raise ValueError(f"子系统名称已存在：{normalized_name}")

            selected_space_id = memory_space_id.strip()
            if memory_mode == "public":
                selected_space_id = PUBLIC_MEMORY_SPACE_ID
            elif memory_mode == "private":
                selected_space_id = f"memory-space-{uuid4().hex}"
                memory_space = MemorySpace(
                    id=selected_space_id,
                    name=f"{normalized_name}记忆库",
                    description=f"{normalized_name} 的独立逻辑记忆空间",
                    space_type="private",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(memory_space)
            elif not selected_space_id or session.get(MemorySpace, selected_space_id) is None:
                raise ValueError("指定的记忆空间不存在")

            workspace = Workspace(
                id=workspace_id,
                name=normalized_name,
                description=description.strip(),
                memory_space_id=selected_space_id,
                enabled=True,
                inherit_global_tools=inherit_global_tools,
                inherit_global_plugins=inherit_global_plugins,
                created_at=now,
                updated_at=now,
            )
            session.add(workspace)
            session.add(
                WorkspaceAuditLog(
                    workspace_id=workspace_id,
                    action="workspace.create",
                    actor="webui",
                    details_json=json.dumps({"memory_mode": memory_mode}, ensure_ascii=False),
                    created_at=now,
                )
            )
            session.flush()
            return workspace

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        memory_space_id: Optional[str] = None,
        enabled: Optional[bool] = None,
        inherit_global_tools: Optional[bool] = None,
        inherit_global_plugins: Optional[bool] = None,
    ) -> Workspace:
        now = datetime.now()
        with get_db_session() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is None:
                raise LookupError("子系统不存在")
            if name is not None:
                normalized_name = name.strip()
                if not normalized_name:
                    raise ValueError("子系统名称不能为空")
                duplicate = session.exec(
                    select(Workspace).where(Workspace.name == normalized_name, Workspace.id != workspace_id)
                ).first()
                if duplicate is not None:
                    raise ValueError(f"子系统名称已存在：{normalized_name}")
                workspace.name = normalized_name
            if description is not None:
                workspace.description = description.strip()
            if memory_space_id is not None:
                if session.get(MemorySpace, memory_space_id) is None:
                    raise ValueError("指定的记忆空间不存在")
                workspace.memory_space_id = memory_space_id
            if enabled is not None:
                if workspace.is_default and not enabled:
                    raise ValueError("默认子系统不能禁用")
                workspace.enabled = enabled
            if inherit_global_tools is not None:
                workspace.inherit_global_tools = inherit_global_tools
            if inherit_global_plugins is not None:
                workspace.inherit_global_plugins = inherit_global_plugins
            workspace.policy_revision += 1
            workspace.updated_at = now
            session.add(workspace)
            session.add(
                WorkspaceAuditLog(
                    workspace_id=workspace.id,
                    action="workspace.update",
                    actor="webui",
                    details_json="{}",
                    created_at=now,
                )
            )
            session.flush()
            return workspace

    def assign_sessions(self, workspace_id: str, session_ids: Iterable[str]) -> int:
        """把已存在的真实聊天流原子地改派到指定工作区。"""

        normalized_ids = tuple(dict.fromkeys(item.strip() for item in session_ids if item.strip()))
        if not normalized_ids:
            return 0
        now = datetime.now()
        with get_db_session() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is None or not workspace.enabled:
                raise LookupError("目标子系统不存在或已禁用")
            existing_sessions = set(
                session.exec(select(ChatSession.session_id).where(col(ChatSession.session_id).in_(normalized_ids))).all()
            )
            missing = sorted(set(normalized_ids) - existing_sessions)
            if missing:
                raise ValueError(f"以下聊天流不存在：{', '.join(missing)}")

            memberships = session.exec(
                select(WorkspaceMembership).where(col(WorkspaceMembership.session_id).in_(normalized_ids))
            ).all()
            by_session_id = {item.session_id: item for item in memberships}
            for session_id in normalized_ids:
                membership = by_session_id.get(session_id)
                if membership is None:
                    membership = WorkspaceMembership(
                        workspace_id=workspace_id,
                        session_id=session_id,
                        assigned_by="manual",
                        created_at=now,
                        updated_at=now,
                    )
                else:
                    membership.workspace_id = workspace_id
                    membership.assigned_by = "manual"
                    membership.updated_at = now
                session.add(membership)
            workspace.policy_revision += 1
            workspace.updated_at = now
            session.add(workspace)
            return len(normalized_ids)

    def unassign_session(self, session_id: str) -> bool:
        with get_db_session() as session:
            membership = session.exec(
                select(WorkspaceMembership).where(WorkspaceMembership.session_id == session_id)
            ).first()
            if membership is None:
                return False
            session.delete(membership)
            return True

    def list_members(self, workspace_id: str) -> list[tuple[WorkspaceMembership, ChatSession]]:
        with get_db_session() as session:
            rows = session.exec(
                select(WorkspaceMembership, ChatSession)
                .join(ChatSession, WorkspaceMembership.session_id == ChatSession.session_id)
                .where(WorkspaceMembership.workspace_id == workspace_id)
                .order_by(col(ChatSession.last_active_timestamp).desc())
            ).all()
            return list(rows)

    def set_tool_policy(self, workspace_id: str, tool_name: str, effect: str) -> WorkspaceToolPolicy:
        if effect not in {"allow", "deny"}:
            raise ValueError("工具策略必须为 allow 或 deny")
        normalized_tool_name = tool_name.strip()
        if not normalized_tool_name:
            raise ValueError("工具名称不能为空")
        now = datetime.now()
        with get_db_session() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is None:
                raise LookupError("子系统不存在")
            policy = session.exec(
                select(WorkspaceToolPolicy).where(
                    WorkspaceToolPolicy.workspace_id == workspace_id,
                    WorkspaceToolPolicy.tool_name == normalized_tool_name,
                )
            ).first()
            if policy is None:
                policy = WorkspaceToolPolicy(
                    workspace_id=workspace_id,
                    tool_name=normalized_tool_name,
                    effect=effect,
                    created_at=now,
                    updated_at=now,
                )
            else:
                policy.effect = effect
                policy.updated_at = now
            session.add(policy)
            workspace.policy_revision += 1
            workspace.updated_at = now
            session.add(workspace)
            session.flush()
            return policy

    def remove_tool_policy(self, workspace_id: str, tool_name: str) -> bool:
        with get_db_session() as session:
            policy = session.exec(
                select(WorkspaceToolPolicy).where(
                    WorkspaceToolPolicy.workspace_id == workspace_id,
                    WorkspaceToolPolicy.tool_name == tool_name,
                )
            ).first()
            if policy is None:
                return False
            session.delete(policy)
            workspace = session.get(Workspace, workspace_id)
            if workspace is not None:
                workspace.policy_revision += 1
                workspace.updated_at = datetime.now()
                session.add(workspace)
            return True

    def resolve_context(self, session_id: str) -> WorkspaceContext:
        """按精确成员、动态选择器、默认工作区的顺序解析策略。"""

        self.ensure_defaults()
        with get_db_session() as session:
            workspace = self._resolve_workspace(session, session_id)
            tool_policies = session.exec(
                select(WorkspaceToolPolicy).where(WorkspaceToolPolicy.workspace_id == workspace.id)
            ).all()
            allowed_tools = frozenset(item.tool_name for item in tool_policies if item.effect == "allow")
            denied_tools = frozenset(item.tool_name for item in tool_policies if item.effect == "deny")
            persona = self._resolve_persona(session, workspace.persona_profile_id)
            return WorkspaceContext(
                workspace_id=workspace.id,
                workspace_name=workspace.name,
                memory_space_id=workspace.memory_space_id,
                policy_revision=workspace.policy_revision,
                inherit_global_tools=workspace.inherit_global_tools,
                inherit_global_plugins=workspace.inherit_global_plugins,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                persona=persona,
            )

    def resolve_readable_memory_space_ids(self, owner_space_id: str) -> tuple[str, ...]:
        """执行 read_from + expose_to 双向握手，返回可检索空间集合。"""

        with get_db_session() as session:
            outbound = session.exec(
                select(MemorySpaceACL).where(
                    MemorySpaceACL.owner_space_id == owner_space_id,
                    MemorySpaceACL.can_read_from_peer == True,  # noqa: E712
                )
            ).all()
            if not outbound:
                return (owner_space_id,)
            peer_ids = [item.peer_space_id for item in outbound]
            inbound = session.exec(
                select(MemorySpaceACL).where(
                    col(MemorySpaceACL.owner_space_id).in_(peer_ids),
                    MemorySpaceACL.peer_space_id == owner_space_id,
                    MemorySpaceACL.expose_to_peer == True,  # noqa: E712
                )
            ).all()
            exposed_peer_ids = {item.owner_space_id for item in inbound}
            return tuple(dict.fromkeys([owner_space_id, *[item for item in peer_ids if item in exposed_peer_ids]]))

    @staticmethod
    def _resolve_persona(session, persona_profile_id: Optional[str]) -> PersonaOverlay:
        if not persona_profile_id:
            return PersonaOverlay()
        profile = session.get(PersonaProfile, persona_profile_id)
        if profile is None:
            return PersonaOverlay()
        alias_names = json.loads(profile.alias_names_json)
        if not isinstance(alias_names, list) or not all(isinstance(item, str) for item in alias_names):
            raise ValueError(f"人设 {profile.id} 的 alias_names_json 格式无效")
        return PersonaOverlay(
            profile_id=profile.id,
            nickname=profile.nickname,
            alias_names=tuple(alias_names),
            personality=profile.personality,
            behavior_style=profile.behavior_style,
            reply_style=profile.reply_style,
            group_chat_prompt=profile.group_chat_prompt,
            private_chat_prompt=profile.private_chat_prompt,
            multiple_reply_style=profile.multiple_reply_style,
            emotion_trait=profile.emotion_trait,
        )

    @staticmethod
    def _resolve_workspace(session, session_id: str) -> Workspace:
        membership = session.exec(
            select(WorkspaceMembership).where(WorkspaceMembership.session_id == session_id)
        ).first()
        if membership is not None:
            workspace = session.get(Workspace, membership.workspace_id)
            if workspace is not None and workspace.enabled:
                return workspace

        chat = session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
        if chat is not None:
            selectors = session.exec(
                select(WorkspaceSelector)
                .where(WorkspaceSelector.enabled == True)  # noqa: E712
                .order_by(col(WorkspaceSelector.priority).desc(), WorkspaceSelector.id)
            ).all()
            for selector in selectors:
                if WorkspaceService._selector_matches(selector, chat):
                    workspace = session.get(Workspace, selector.workspace_id)
                    if workspace is not None and workspace.enabled:
                        return workspace

        default_workspace = session.exec(
            select(Workspace).where(Workspace.is_default == True, Workspace.enabled == True)  # noqa: E712
        ).first()
        if default_workspace is None:
            raise RuntimeError("未找到可用的默认子系统")
        return default_workspace

    @staticmethod
    def _selector_matches(selector: WorkspaceSelector, chat: ChatSession) -> bool:
        if selector.platform and selector.platform != chat.platform:
            return False
        if selector.account_id and selector.account_id != (chat.account_id or ""):
            return False
        if selector.chat_type == "group" and not chat.group_id:
            return False
        if selector.chat_type == "private" and chat.group_id:
            return False
        target_id = chat.group_id if chat.group_id else chat.user_id
        return not selector.target_id or selector.target_id == (target_id or "")


workspace_service = WorkspaceService()
