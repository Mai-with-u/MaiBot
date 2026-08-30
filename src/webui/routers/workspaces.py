"""Workspace 子系统管理 API。"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import ChatSession, WorkspaceToolPolicy
from src.webui.dependencies import require_auth
from src.workspaces import workspace_service

router = APIRouter(prefix="/workspaces", tags=["Workspaces"], dependencies=[Depends(require_auth)])


class MemorySpaceItem(BaseModel):
    id: str
    name: str
    description: str
    space_type: str
    enabled: bool
    policy_revision: int


class WorkspaceItem(BaseModel):
    id: str
    name: str
    description: str
    memory_space_id: str
    memory_space_name: str
    persona_profile_id: Optional[str]
    is_default: bool
    enabled: bool
    inherit_global_tools: bool
    inherit_global_plugins: bool
    policy_revision: int
    member_count: int
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    success: bool = True
    data: list[WorkspaceItem]
    memory_spaces: list[MemorySpaceItem]


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    memory_mode: str = "private"
    memory_space_id: str = ""
    inherit_global_tools: bool = True
    inherit_global_plugins: bool = True


class WorkspaceUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=2000)
    memory_space_id: Optional[str] = None
    enabled: Optional[bool] = None
    inherit_global_tools: Optional[bool] = None
    inherit_global_plugins: Optional[bool] = None


class WorkspaceMutationResponse(BaseModel):
    success: bool = True
    data: WorkspaceItem


class WorkspaceAssignRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1)


class WorkspaceAssignResponse(BaseModel):
    success: bool = True
    assigned_count: int


class WorkspaceMemberItem(BaseModel):
    session_id: str
    display_name: str
    platform: str
    account_id: str
    chat_type: str
    target_id: str
    last_active_timestamp: Optional[datetime]


class WorkspaceMembersResponse(BaseModel):
    success: bool = True
    data: list[WorkspaceMemberItem]


class AvailableChatItem(WorkspaceMemberItem):
    workspace_id: str
    workspace_name: str
    explicitly_assigned: bool


class AvailableChatsResponse(BaseModel):
    success: bool = True
    data: list[AvailableChatItem]


class ToolPolicyItem(BaseModel):
    tool_name: str
    effect: str


class ToolPolicyListResponse(BaseModel):
    success: bool = True
    inherit_global_tools: bool
    data: list[ToolPolicyItem]


class ToolPolicyRequest(BaseModel):
    effect: str


def _workspace_item(workspace, memory_space_names: dict[str, str], counts: dict[str, int]) -> WorkspaceItem:
    return WorkspaceItem(
        id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        memory_space_id=workspace.memory_space_id,
        memory_space_name=memory_space_names.get(workspace.memory_space_id, workspace.memory_space_id),
        persona_profile_id=workspace.persona_profile_id,
        is_default=workspace.is_default,
        enabled=workspace.enabled,
        inherit_global_tools=workspace.inherit_global_tools,
        inherit_global_plugins=workspace.inherit_global_plugins,
        policy_revision=workspace.policy_revision,
        member_count=counts.get(workspace.id, 0),
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _display_name(chat: ChatSession) -> str:
    if chat.group_id:
        return chat.group_name or f"群聊 {chat.group_id}"
    nickname = chat.user_cardname or chat.user_nickname or chat.user_id
    return f"{nickname}的私聊" if nickname else chat.session_id


def _member_item(chat: ChatSession) -> WorkspaceMemberItem:
    target_id = chat.group_id or chat.user_id or ""
    return WorkspaceMemberItem(
        session_id=chat.session_id,
        display_name=_display_name(chat),
        platform=chat.platform,
        account_id=chat.account_id or "",
        chat_type="group" if chat.group_id else "private",
        target_id=target_id,
        last_active_timestamp=chat.last_active_timestamp,
    )


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces() -> WorkspaceListResponse:
    spaces = workspace_service.list_memory_spaces()
    memory_space_names = {item.id: item.name for item in spaces}
    counts = workspace_service.get_membership_counts()
    return WorkspaceListResponse(
        data=[_workspace_item(item, memory_space_names, counts) for item in workspace_service.list_workspaces()],
        memory_spaces=[
            MemorySpaceItem(
                id=item.id,
                name=item.name,
                description=item.description,
                space_type=item.space_type,
                enabled=item.enabled,
                policy_revision=item.policy_revision,
            )
            for item in spaces
        ],
    )


@router.post("", response_model=WorkspaceMutationResponse)
async def create_workspace(request: WorkspaceCreateRequest) -> WorkspaceMutationResponse:
    try:
        workspace = workspace_service.create_workspace(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    spaces = {item.id: item.name for item in workspace_service.list_memory_spaces()}
    return WorkspaceMutationResponse(data=_workspace_item(workspace, spaces, workspace_service.get_membership_counts()))


@router.patch("/{workspace_id}", response_model=WorkspaceMutationResponse)
async def update_workspace(workspace_id: str, request: WorkspaceUpdateRequest) -> WorkspaceMutationResponse:
    try:
        workspace = workspace_service.update_workspace(workspace_id, **request.model_dump(exclude_unset=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    spaces = {item.id: item.name for item in workspace_service.list_memory_spaces()}
    return WorkspaceMutationResponse(data=_workspace_item(workspace, spaces, workspace_service.get_membership_counts()))


@router.get("/{workspace_id}/members", response_model=WorkspaceMembersResponse)
async def list_workspace_members(workspace_id: str) -> WorkspaceMembersResponse:
    if workspace_service.get_workspace(workspace_id) is None:
        raise HTTPException(status_code=404, detail="子系统不存在")
    return WorkspaceMembersResponse(data=[_member_item(chat) for _, chat in workspace_service.list_members(workspace_id)])


@router.post("/{workspace_id}/members", response_model=WorkspaceAssignResponse)
async def assign_workspace_members(workspace_id: str, request: WorkspaceAssignRequest) -> WorkspaceAssignResponse:
    try:
        count = workspace_service.assign_sessions(workspace_id, request.session_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkspaceAssignResponse(assigned_count=count)


@router.delete("/members/{session_id}", response_model=WorkspaceAssignResponse)
async def unassign_workspace_member(session_id: str) -> WorkspaceAssignResponse:
    removed = workspace_service.unassign_session(session_id)
    return WorkspaceAssignResponse(assigned_count=1 if removed else 0)


@router.get("/chats/available", response_model=AvailableChatsResponse)
async def list_available_chats() -> AvailableChatsResponse:
    workspaces = {item.id: item.name for item in workspace_service.list_workspaces()}
    with get_db_session() as session:
        chats = session.exec(select(ChatSession).order_by(col(ChatSession.last_active_timestamp).desc())).all()
    data: list[AvailableChatItem] = []
    for chat in chats:
        context = workspace_service.resolve_context(chat.session_id)
        base = _member_item(chat)
        explicitly_assigned = any(
            member.session_id == chat.session_id
            for member, _ in workspace_service.list_members(context.workspace_id)
        )
        data.append(
            AvailableChatItem(
                **base.model_dump(),
                workspace_id=context.workspace_id,
                workspace_name=workspaces.get(context.workspace_id, context.workspace_name),
                explicitly_assigned=explicitly_assigned,
            )
        )
    return AvailableChatsResponse(data=data)


@router.get("/{workspace_id}/tools", response_model=ToolPolicyListResponse)
async def list_workspace_tool_policies(workspace_id: str) -> ToolPolicyListResponse:
    workspace = workspace_service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="子系统不存在")
    with get_db_session() as session:
        policies = session.exec(
            select(WorkspaceToolPolicy)
            .where(WorkspaceToolPolicy.workspace_id == workspace_id)
            .order_by(WorkspaceToolPolicy.tool_name)
        ).all()
    return ToolPolicyListResponse(
        inherit_global_tools=workspace.inherit_global_tools,
        data=[ToolPolicyItem(tool_name=item.tool_name, effect=item.effect) for item in policies],
    )


@router.put("/{workspace_id}/tools/{tool_name}", response_model=ToolPolicyItem)
async def set_workspace_tool_policy(
    workspace_id: str,
    tool_name: str,
    request: ToolPolicyRequest,
) -> ToolPolicyItem:
    try:
        policy = workspace_service.set_tool_policy(workspace_id, tool_name, request.effect)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ToolPolicyItem(tool_name=policy.tool_name, effect=policy.effect)


@router.delete("/{workspace_id}/tools/{tool_name}")
async def delete_workspace_tool_policy(workspace_id: str, tool_name: str) -> dict[str, bool]:
    return {"success": workspace_service.remove_tool_policy(workspace_id, tool_name)}
