"""本地聊天室路由 - WebUI 与麦麦直接对话

支持两种模式：
1. WebUI 模式：使用 WebUI 平台独立身份聊天
2. 虚拟身份模式：使用真实平台用户的身份，在虚拟群聊中与麦麦对话
"""

import time
import uuid
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, Cookie, Header
from pydantic import BaseModel

from src.common.logger import get_logger
from src.common.database.database_model import Messages, PersonInfo
from src.config.config import global_config
from src.chat.message_receive.bot import chat_bot
from src.webui.core import verify_auth_token_from_cookie_or_header, get_token_manager
from src.webui.routers.websocket.auth import verify_ws_token

logger = get_logger("webui.chat")

router = APIRouter(prefix="/api/chat", tags=["LocalChat"])


def require_auth(
    maibot_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> bool:
    """认证依赖：验证用户是否已登录"""
    return verify_auth_token_from_cookie_or_header(maibot_session, authorization)


# WebUI 聊天的虚拟群组 ID
WEBUI_CHAT_GROUP_ID = "webui_local_chat"
WEBUI_CHAT_PLATFORM = "webui"

# 虚拟身份模式的群 ID 前缀
VIRTUAL_GROUP_ID_PREFIX = "webui_virtual_group_"

# 固定的 WebUI 用户 ID 前缀
WEBUI_USER_ID_PREFIX = "webui_user_"


class VirtualIdentityConfig(BaseModel):
    """虚拟身份配置"""

    enabled: bool = False  # 是否启用虚拟身份模式
    platform: Optional[str] = None  # 目标平台（如 qq, discord 等）
    person_id: Optional[str] = None  # PersonInfo 的 person_id
    user_id: Optional[str] = None  # 原始平台用户 ID
    user_nickname: Optional[str] = None  # 用户昵称
    group_id: Optional[str] = None  # 虚拟群 ID（自动生成或用户指定）
    group_name: Optional[str] = None  # 虚拟群名（用户自定义）


class ChatHistoryMessage(BaseModel):
    """聊天历史消息"""

    id: str
    type: str  # 'user' | 'bot' | 'system'
    content: str
    timestamp: float
    sender_name: str
    sender_id: Optional[str] = None
    is_bot: bool = False


class ChatHistoryManager:
    """聊天历史管理器 - 使用 SQLite 数据库存储"""

    def __init__(self, max_messages: int = 200):
        self.max_messages = max_messages

    def _message_to_dict(self, msg: Messages, group_id: Optional[str] = None) -> Dict[str, Any]:
        """将数据库消息转换为前端格式

        Args:
            msg: 数据库消息对象
            group_id: 群 ID，用于判断是否是虚拟群
        """
        # 判断是否是机器人消息
        user_id = msg.user_id or ""

        # 对于虚拟群，通过比较机器人 QQ 账号来判断
        # 对于普通 WebUI 群，检查 user_id 是否以 webui_ 开头
        if group_id and group_id.startswith(VIRTUAL_GROUP_ID_PREFIX):
            # 虚拟群：user_id 等于机器人 QQ 账号的是机器人消息
            bot_qq = str(global_config.bot.qq_account)
            is_bot = user_id == bot_qq
        else:
            # 普通 WebUI 群：不以 webui_ 开头的是机器人消息
            is_bot = not user_id.startswith("webui_") and not user_id.startswith(WEBUI_USER_ID_PREFIX)

        return {
            "id": msg.message_id,
            "type": "bot" if is_bot else "user",
            "content": msg.processed_plain_text or msg.display_message or "",
            "timestamp": msg.time,
            "sender_name": msg.user_nickname or (global_config.bot.nickname if is_bot else "未知用户"),
            "sender_id": "bot" if is_bot else user_id,
            "is_bot": is_bot,
        }

    def get_history(self, limit: int = 50, group_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """从数据库获取最近的历史记录

        Args:
            limit: 获取的消息数量
            group_id: 群 ID，默认为 WEBUI_CHAT_GROUP_ID
        """
        target_group_id = group_id if group_id else WEBUI_CHAT_GROUP_ID
        try:
            # 查询指定群的消息，按时间排序
            messages = (
                Messages.select()
                .where(Messages.chat_info_group_id == target_group_id)
                .order_by(Messages.time.desc())
                .limit(limit)
            )

            # 转换为列表并反转（使最旧的消息在前）
            # 传递 group_id 以便正确判断虚拟群中的机器人消息
            result = [self._message_to_dict(msg, target_group_id) for msg in messages]
            result.reverse()

            logger.debug(f"从数据库加载了 {len(result)} 条聊天记录 (group_id={target_group_id})")
            return result
        except Exception as e:
            logger.error(f"从数据库加载聊天记录失败: {e}")
            return []

    def clear_history(self, group_id: Optional[str] = None) -> int:
        """清空聊天历史记录

        Args:
            group_id: 群 ID，默认清空 WebUI 默认聊天室
        """
        target_group_id = group_id if group_id else WEBUI_CHAT_GROUP_ID
        try:
            deleted = Messages.delete().where(Messages.chat_info_group_id == target_group_id).execute()
            logger.info(f"已清空 {deleted} 条聊天记录 (group_id={target_group_id})")
            return deleted
        except Exception as e:
            logger.error(f"清空聊天记录失败: {e}")
            return 0


# 全局聊天历史管理器
chat_history = ChatHistoryManager()


# 存储 WebSocket 连接
class ChatConnectionManager:
    """聊天连接管理器"""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.user_sessions: Dict[str, str] = {}  # user_id -> session_id 映射

    async def connect(self, websocket: WebSocket, session_id: str, user_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        self.user_sessions[user_id] = session_id
        logger.info(f"WebUI 聊天会话已连接: session={session_id}, user={user_id}")

    def disconnect(self, session_id: str, user_id: str):
        if session_id in self.active_connections:
            del self.active_connections[session_id]
        if user_id in self.user_sessions and self.user_sessions[user_id] == session_id:
            del self.user_sessions[user_id]
        logger.info(f"WebUI 聊天会话已断开: session={session_id}")

    async def send_message(self, session_id: str, message: dict):
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_json(message)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")

    async def broadcast(self, message: dict):
        """广播消息给所有连接"""
        for session_id in list(self.active_connections.keys()):
            await self.send_message(session_id, message)


chat_manager = ChatConnectionManager()


def create_message_data(
    content: str,
    user_id: str,
    user_name: str,
    message_id: Optional[str] = None,
    is_at_bot: bool = True,
    virtual_config: Optional[VirtualIdentityConfig] = None,
) -> Dict[str, Any]:
    """创建符合麦麦消息格式的消息数据

    Args:
        content: 消息内容
        user_id: 用户 ID
        user_name: 用户昵称
        message_id: 消息 ID（可选，自动生成）
        is_at_bot: 是否 @ 机器人
        virtual_config: 虚拟身份配置（可选，启用后使用真实平台身份）
    """
    if message_id is None:
        message_id = str(uuid.uuid4())

    # 确定使用的平台、群信息和用户信息
    if virtual_config and virtual_config.enabled:
        # 虚拟身份模式：使用真实平台身份
        platform = virtual_config.platform or WEBUI_CHAT_PLATFORM
        group_id = virtual_config.group_id or f"{VIRTUAL_GROUP_ID_PREFIX}{uuid.uuid4().hex[:8]}"
        group_name = virtual_config.group_name or "WebUI虚拟群聊"
        actual_user_id = virtual_config.user_id or user_id
        actual_user_name = virtual_config.user_nickname or user_name
    else:
        # 标准 WebUI 模式
        platform = WEBUI_CHAT_PLATFORM
        group_id = WEBUI_CHAT_GROUP_ID
        group_name = "WebUI本地聊天室"
        actual_user_id = user_id
        actual_user_name = user_name

    return {
        "message_info": {
            "platform": platform,
            "message_id": message_id,
            "time": time.time(),
            "group_info": {
                "group_id": group_id,
                "group_name": group_name,
                "platform": platform,
            },
            "user_info": {
                "user_id": actual_user_id,
                "user_nickname": actual_user_name,
                "user_cardname": actual_user_name,
                "platform": platform,
            },
            "additional_config": {
                "at_bot": is_at_bot,
            },
        },
        "message_segment": {
            "type": "seglist",
            "data": [
                {
                    "type": "text",
                    "data": content,
                },
                {
                    "type": "mention_bot",
                    "data": "1.0",
                },
            ],
        },
        "raw_message": content,
        "processed_plain_text": content,
    }


@router.get("/history")
async def get_chat_history(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: Optional[str] = Query(default=None),  # 保留参数兼容性，但不用于过滤
    group_id: Optional[str] = Query(default=None),  # 可选：指定群 ID 获取历史
    _auth: bool = Depends(require_auth),
):
    """获取聊天历史记录

    所有 WebUI 用户共享同一个聊天室，因此返回所有历史记录
    如果指定了 group_id，则获取该虚拟群的历史记录
    """
    target_group_id = group_id if group_id else WEBUI_CHAT_GROUP_ID
    history = chat_history.get_history(limit, target_group_id)
    return {
        "success": True,
        "messages": history,
        "total": len(history),
    }


@router.get("/platforms")
async def get_available_platforms(_auth: bool = Depends(require_auth)):
    """获取可用平台列表

    从 PersonInfo 表中获取所有已知的平台
    """
    try:
        from peewee import fn

        # 查询所有不同的平台
        platforms = (
            PersonInfo.select(PersonInfo.platform, fn.COUNT(PersonInfo.id).alias("count"))
            .group_by(PersonInfo.platform)
            .order_by(fn.COUNT(PersonInfo.id).desc())
        )

        result = []
        for p in platforms:
            if p.platform:  # 排除空平台
                result.append({"platform": p.platform, "count": p.count})

        return {"success": True, "platforms": result}
    except Exception as e:
        logger.error(f"获取平台列表失败: {e}")
        return {"success": False, "error": str(e), "platforms": []}


@router.get("/persons")
async def get_persons_by_platform(
    platform: str = Query(..., description="平台名称"),
    search: Optional[str] = Query(default=None, description="搜索关键词"),
    limit: int = Query(default=50, ge=1, le=200),
    _auth: bool = Depends(require_auth),
):
    """获取指定平台的用户列表

    Args:
        platform: 平台名称（如 qq, discord 等）
        search: 搜索关键词（匹配昵称、用户名、user_id）
        limit: 返回数量限制
    """
    try:
        # 构建查询
        query = PersonInfo.select().where(PersonInfo.platform == platform)

        # 搜索过滤
        if search:
            query = query.where(
                (PersonInfo.person_name.contains(search))
                | (PersonInfo.nickname.contains(search))
                | (PersonInfo.user_id.contains(search))
            )

        # 按最后交互时间排序，优先显示活跃用户
        from peewee import Case

        query = query.order_by(Case(None, [(PersonInfo.last_know.is_null(), 1)], 0), PersonInfo.last_know.desc())
        query = query.limit(limit)

        result = []
        for person in query:
            result.append(
                {
                    "person_id": person.person_id,
                    "user_id": person.user_id,
                    "person_name": person.person_name,
                    "nickname": person.nickname,
                    "is_known": person.is_known,
                    "platform": person.platform,
                    "display_name": person.person_name or person.nickname or person.user_id,
                }
            )

        return {"success": True, "persons": result, "total": len(result)}
    except Exception as e:
        logger.error(f"获取用户列表失败: {e}")
        return {"success": False, "error": str(e), "persons": []}


@router.delete("/history")
async def clear_chat_history(group_id: Optional[str] = Query(default=None), _auth: bool = Depends(require_auth)):
    """清空聊天历史记录

    Args:
        group_id: 可选，指定要清空的群 ID，默认清空 WebUI 默认聊天室
    """
    deleted = chat_history.clear_history(group_id)
    return {
        "success": True,
        "message": f"已清空 {deleted} 条聊天记录",
    }


@router.websocket("/ws")
async def websocket_chat(
    websocket: WebSocket,
    user_id: Optional[str] = Query(default=None),
    user_name: Optional[str] = Query(default="WebUI用户"),
    platform: Optional[str] = Query(default=None),
    person_id: Optional[str] = Query(default=None),
    group_name: Optional[str] = Query(default=None),
    group_id: Optional[str] = Query(default=None),  # 前端传递的稳定 group_id
    token: Optional[str] = Query(default=None),  # 认证 token
):
    """WebSocket 聊天端点

    Args:
        user_id: 用户唯一标识（由前端生成并持久化）
        user_name: 用户显示昵称（可修改）
        platform: 虚拟身份模式的平台（可选）
        person_id: 虚拟身份模式的用户 person_id（可选）
        group_name: 虚拟身份模式的群名（可选）
        group_id: 虚拟身份模式的群 ID（可选，由前端生成并持久化）
        token: 认证 token（可选，也可从 Cookie 获取）

    虚拟身份模式可通过 URL 参数直接配置，或通过消息中的 set_virtual_identity 配置

    支持三种认证方式（按优先级）：
    1. query 参数 token（推荐，通过 /api/webui/ws-token 获取临时 token）
    2. Cookie 中的 maibot_session
    3. 直接使用 session token（兼容）

    示例：ws://host/api/chat/ws?token=xxx
    """
    is_authenticated = False

    # 方式 1: 尝试验证临时 WebSocket token（推荐方式）
    if token and verify_ws_token(token):
        is_authenticated = True
        logger.debug("聊天 WebSocket 使用临时 token 认证成功")

    # 方式 2: 尝试从 Cookie 获取 session token
    if not is_authenticated:
        cookie_token = websocket.cookies.get("maibot_session")
        if cookie_token:
            token_manager = get_token_manager()
            if token_manager.verify_token(cookie_token):
                is_authenticated = True
                logger.debug("聊天 WebSocket 使用 Cookie 认证成功")

    # 方式 3: 尝试直接验证 query 参数作为 session token（兼容旧方式）
    if not is_authenticated and token:
        token_manager = get_token_manager()
        if token_manager.verify_token(token):
            is_authenticated = True
            logger.debug("聊天 WebSocket 使用 session token 认证成功")

    if not is_authenticated:
        logger.warning("聊天 WebSocket 连接被拒绝：认证失败")
        await websocket.close(code=4001, reason="认证失败，请重新登录")
        return

    # 生成会话 ID（每次连接都是新的）
    session_id = str(uuid.uuid4())

    # 如果没有提供 user_id，生成一个新的
    if not user_id:
        user_id = f"{WEBUI_USER_ID_PREFIX}{uuid.uuid4().hex[:16]}"
    elif not user_id.startswith(WEBUI_USER_ID_PREFIX):
        # 确保 user_id 有正确的前缀
        user_id = f"{WEBUI_USER_ID_PREFIX}{user_id}"

    # 当前会话的虚拟身份配置（可通过消息动态更新）
    current_virtual_config: Optional[VirtualIdentityConfig] = None

    # 如果 URL 参数中提供了虚拟身份信息，自动配置
    if platform and person_id:
        try:
            person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)
            if person:
                # 使用前端传递的 group_id，如果没有则生成一个稳定的
                virtual_group_id = group_id or f"{VIRTUAL_GROUP_ID_PREFIX}{platform}_{person.user_id}"
                current_virtual_config = VirtualIdentityConfig(
                    enabled=True,
                    platform=person.platform,
                    person_id=person.person_id,
                    user_id=person.user_id,
                    user_nickname=person.person_name or person.nickname or person.user_id,
                    group_id=virtual_group_id,
                    group_name=group_name or "WebUI虚拟群聊",
                )
                logger.info(
                    f"虚拟身份模式已通过 URL 参数激活: {current_virtual_config.user_nickname} @ {current_virtual_config.platform}, group_id={virtual_group_id}"
                )
        except Exception as e:
            logger.warning(f"通过 URL 参数配置虚拟身份失败: {e}")

    await chat_manager.connect(websocket, session_id, user_id)

    try:
        # 构建会话信息
        session_info_data = {
            "type": "session_info",
            "session_id": session_id,
            "user_id": user_id,
            "user_name": user_name,
            "bot_name": global_config.bot.nickname,
        }

        # 如果有虚拟身份配置，添加到会话信息中
        if current_virtual_config and current_virtual_config.enabled:
            session_info_data["virtual_mode"] = True
            session_info_data["group_id"] = current_virtual_config.group_id
            session_info_data["virtual_identity"] = {
                "platform": current_virtual_config.platform,
                "user_id": current_virtual_config.user_id,
                "user_nickname": current_virtual_config.user_nickname,
                "group_name": current_virtual_config.group_name,
            }

        # 发送会话信息（包含用户 ID，前端需要保存）
        await chat_manager.send_message(session_id, session_info_data)

        # 发送历史记录（根据模式选择不同的群）
        if current_virtual_config and current_virtual_config.enabled:
            history = chat_history.get_history(50, current_virtual_config.group_id)
        else:
            history = chat_history.get_history(50)
        if history:
            await chat_manager.send_message(
                session_id,
                {
                    "type": "history",
                    "messages": history,
                },
            )

        # 发送欢迎消息（不保存到历史）
        if current_virtual_config and current_virtual_config.enabled:
            welcome_msg = f"已以 {current_virtual_config.user_nickname} 的身份连接到「{current_virtual_config.group_name}」，开始与 {global_config.bot.nickname} 对话吧！"
        else:
            welcome_msg = f"已连接到本地聊天室，可以开始与 {global_config.bot.nickname} 对话了！"

        await chat_manager.send_message(
            session_id,
            {
                "type": "system",
                "content": welcome_msg,
                "timestamp": time.time(),
            },
        )

        while True:
            data = await websocket.receive_json()

            if data.get("type") == "message":
                content = data.get("content", "").strip()
                if not content:
                    continue

                # 用户可以更新昵称
                current_user_name = data.get("user_name", user_name)

                message_id = str(uuid.uuid4())
                timestamp = time.time()

                # 确定发送者信息（根据是否使用虚拟身份）
                if current_virtual_config and current_virtual_config.enabled:
                    sender_name = current_virtual_config.user_nickname or current_user_name
                    sender_user_id = current_virtual_config.user_id or user_id
                else:
                    sender_name = current_user_name
                    sender_user_id = user_id

                # 广播用户消息给所有连接（包括发送者）
                # 注意：用户消息会在 chat_bot.message_process 中自动保存到数据库
                await chat_manager.broadcast(
                    {
                        "type": "user_message",
                        "content": content,
                        "message_id": message_id,
                        "timestamp": timestamp,
                        "sender": {
                            "name": sender_name,
                            "user_id": sender_user_id,
                            "is_bot": False,
                        },
                        "virtual_mode": current_virtual_config.enabled if current_virtual_config else False,
                    }
                )

                # 创建麦麦消息格式
                message_data = create_message_data(
                    content=content,
                    user_id=user_id,
                    user_name=current_user_name,
                    message_id=message_id,
                    is_at_bot=True,
                    virtual_config=current_virtual_config,
                )

                try:
                    # 显示正在输入状态
                    await chat_manager.broadcast(
                        {
                            "type": "typing",
                            "is_typing": True,
                        }
                    )

                    # 调用麦麦的消息处理
                    await chat_bot.message_process(message_data)

                except Exception as e:
                    logger.error(f"处理消息时出错: {e}")
                    await chat_manager.send_message(
                        session_id,
                        {
                            "type": "error",
                            "content": f"处理消息时出错: {str(e)}",
                            "timestamp": time.time(),
                        },
                    )
                finally:
                    await chat_manager.broadcast(
                        {
                            "type": "typing",
                            "is_typing": False,
                        }
                    )

            elif data.get("type") == "ping":
                await chat_manager.send_message(
                    session_id,
                    {
                        "type": "pong",
                        "timestamp": time.time(),
                    },
                )

            elif data.get("type") == "update_nickname":
                # 允许用户更新昵称
                if new_name := data.get("user_name", "").strip():
                    current_user_name = new_name
                    await chat_manager.send_message(
                        session_id,
                        {
                            "type": "nickname_updated",
                            "user_name": current_user_name,
                            "timestamp": time.time(),
                        },
                    )

            elif data.get("type") == "set_virtual_identity":
                # 设置或更新虚拟身份配置
                virtual_data = data.get("config", {})
                if virtual_data.get("enabled"):
                    # 验证必要字段
                    if not virtual_data.get("platform") or not virtual_data.get("person_id"):
                        await chat_manager.send_message(
                            session_id,
                            {
                                "type": "error",
                                "content": "虚拟身份配置缺少必要字段: platform 和 person_id",
                                "timestamp": time.time(),
                            },
                        )
                        continue

                    # 获取用户信息
                    try:
                        person = PersonInfo.get_or_none(PersonInfo.person_id == virtual_data.get("person_id"))
                        if not person:
                            await chat_manager.send_message(
                                session_id,
                                {
                                    "type": "error",
                                    "content": f"找不到用户: {virtual_data.get('person_id')}",
                                    "timestamp": time.time(),
                                },
                            )
                            continue

                        # 生成虚拟群 ID
                        custom_group_id = virtual_data.get("group_id")
                        if custom_group_id:
                            group_id = f"{VIRTUAL_GROUP_ID_PREFIX}{custom_group_id}"
                        else:
                            group_id = f"{VIRTUAL_GROUP_ID_PREFIX}{session_id[:8]}"

                        current_virtual_config = VirtualIdentityConfig(
                            enabled=True,
                            platform=person.platform,
                            person_id=person.person_id,
                            user_id=person.user_id,
                            user_nickname=person.person_name or person.nickname or person.user_id,
                            group_id=group_id,
                            group_name=virtual_data.get("group_name", "WebUI虚拟群聊"),
                        )

                        # 发送虚拟身份已激活的消息
                        await chat_manager.send_message(
                            session_id,
                            {
                                "type": "virtual_identity_set",
                                "config": {
                                    "enabled": True,
                                    "platform": current_virtual_config.platform,
                                    "user_id": current_virtual_config.user_id,
                                    "user_nickname": current_virtual_config.user_nickname,
                                    "group_id": current_virtual_config.group_id,
                                    "group_name": current_virtual_config.group_name,
                                },
                                "timestamp": time.time(),
                            },
                        )

                        # 加载虚拟群的历史记录
                        virtual_history = chat_history.get_history(50, current_virtual_config.group_id)
                        await chat_manager.send_message(
                            session_id,
                            {
                                "type": "history",
                                "messages": virtual_history,
                                "group_id": current_virtual_config.group_id,
                            },
                        )

                        # 发送系统消息
                        await chat_manager.send_message(
                            session_id,
                            {
                                "type": "system",
                                "content": f"已切换到虚拟身份模式：以 {current_virtual_config.user_nickname} 的身份在「{current_virtual_config.group_name}」与 {global_config.bot.nickname} 对话",
                                "timestamp": time.time(),
                            },
                        )

                    except Exception as e:
                        logger.error(f"设置虚拟身份失败: {e}")
                        await chat_manager.send_message(
                            session_id,
                            {
                                "type": "error",
                                "content": f"设置虚拟身份失败: {str(e)}",
                                "timestamp": time.time(),
                            },
                        )
                else:
                    # 禁用虚拟身份模式
                    current_virtual_config = None
                    await chat_manager.send_message(
                        session_id,
                        {
                            "type": "virtual_identity_set",
                            "config": {"enabled": False},
                            "timestamp": time.time(),
                        },
                    )

                    # 重新加载默认聊天室历史
                    default_history = chat_history.get_history(50, WEBUI_CHAT_GROUP_ID)
                    await chat_manager.send_message(
                        session_id,
                        {
                            "type": "history",
                            "messages": default_history,
                            "group_id": WEBUI_CHAT_GROUP_ID,
                        },
                    )

                    await chat_manager.send_message(
                        session_id,
                        {
                            "type": "system",
                            "content": "已切换回 WebUI 独立用户模式",
                            "timestamp": time.time(),
                        },
                    )

    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: session={session_id}, user={user_id}")
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
    finally:
        chat_manager.disconnect(session_id, user_id)


@router.get("/info")
async def get_chat_info(_auth: bool = Depends(require_auth)):
    """获取聊天室信息"""
    return {
        "bot_name": global_config.bot.nickname,
        "platform": WEBUI_CHAT_PLATFORM,
        "group_id": WEBUI_CHAT_GROUP_ID,
        "active_sessions": len(chat_manager.active_connections),
    }


def get_webui_chat_broadcaster() -> tuple:
    """获取 WebUI 聊天广播器，供外部模块使用

    Returns:
        (chat_manager, WEBUI_CHAT_PLATFORM) 元组
    """
    return (chat_manager, WEBUI_CHAT_PLATFORM)
