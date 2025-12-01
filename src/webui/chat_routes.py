"""本地聊天室路由 - WebUI 与麦麦直接对话"""

import time
import uuid
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel

from src.common.logger import get_logger
from src.common.database.database_model import Messages
from src.config.config import global_config
from src.chat.message_receive.bot import chat_bot

logger = get_logger("webui.chat")

router = APIRouter(prefix="/api/chat", tags=["LocalChat"])

# WebUI 聊天的虚拟群组 ID
WEBUI_CHAT_GROUP_ID = "webui_local_chat"
WEBUI_CHAT_PLATFORM = "webui"

# 固定的 WebUI 用户 ID 前缀
WEBUI_USER_ID_PREFIX = "webui_user_"


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

    def _message_to_dict(self, msg: Messages) -> Dict[str, Any]:
        """将数据库消息转换为前端格式"""
        # 判断是否是机器人消息
        # WebUI 用户的 user_id 以 "webui_" 开头，其他都是机器人消息
        user_id = msg.user_id or ""
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

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """从数据库获取最近的历史记录"""
        try:
            # 查询 WebUI 平台的消息，按时间排序
            messages = (
                Messages.select()
                .where(Messages.chat_info_group_id == WEBUI_CHAT_GROUP_ID)
                .order_by(Messages.time.desc())
                .limit(limit)
            )

            # 转换为列表并反转（使最旧的消息在前）
            result = [self._message_to_dict(msg) for msg in messages]
            result.reverse()

            logger.debug(f"从数据库加载了 {len(result)} 条聊天记录")
            return result
        except Exception as e:
            logger.error(f"从数据库加载聊天记录失败: {e}")
            return []

    def clear_history(self) -> int:
        """清空 WebUI 聊天历史记录"""
        try:
            deleted = Messages.delete().where(Messages.chat_info_group_id == WEBUI_CHAT_GROUP_ID).execute()
            logger.info(f"已清空 {deleted} 条 WebUI 聊天记录")
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
    content: str, user_id: str, user_name: str, message_id: Optional[str] = None, is_at_bot: bool = True
) -> Dict[str, Any]:
    """创建符合麦麦消息格式的消息数据"""
    if message_id is None:
        message_id = str(uuid.uuid4())

    return {
        "message_info": {
            "platform": WEBUI_CHAT_PLATFORM,
            "message_id": message_id,
            "time": time.time(),
            "group_info": {
                "group_id": WEBUI_CHAT_GROUP_ID,
                "group_name": "WebUI本地聊天室",
                "platform": WEBUI_CHAT_PLATFORM,
            },
            "user_info": {
                "user_id": user_id,
                "user_nickname": user_name,
                "user_cardname": user_name,
                "platform": WEBUI_CHAT_PLATFORM,
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
):
    """获取聊天历史记录

    所有 WebUI 用户共享同一个聊天室，因此返回所有历史记录
    """
    history = chat_history.get_history(limit)
    return {
        "success": True,
        "messages": history,
        "total": len(history),
    }


@router.delete("/history")
async def clear_chat_history():
    """清空聊天历史记录"""
    deleted = chat_history.clear_history()
    return {
        "success": True,
        "message": f"已清空 {deleted} 条聊天记录",
    }


@router.websocket("/ws")
async def websocket_chat(
    websocket: WebSocket,
    user_id: Optional[str] = Query(default=None),
    user_name: Optional[str] = Query(default="WebUI用户"),
):
    """WebSocket 聊天端点

    Args:
        user_id: 用户唯一标识（由前端生成并持久化）
        user_name: 用户显示昵称（可修改）
    """
    # 生成会话 ID（每次连接都是新的）
    session_id = str(uuid.uuid4())

    # 如果没有提供 user_id，生成一个新的
    if not user_id:
        user_id = f"{WEBUI_USER_ID_PREFIX}{uuid.uuid4().hex[:16]}"
    elif not user_id.startswith(WEBUI_USER_ID_PREFIX):
        # 确保 user_id 有正确的前缀
        user_id = f"{WEBUI_USER_ID_PREFIX}{user_id}"

    await chat_manager.connect(websocket, session_id, user_id)

    try:
        # 发送会话信息（包含用户 ID，前端需要保存）
        await chat_manager.send_message(
            session_id,
            {
                "type": "session_info",
                "session_id": session_id,
                "user_id": user_id,
                "user_name": user_name,
                "bot_name": global_config.bot.nickname,
            },
        )

        # 发送历史记录
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
        await chat_manager.send_message(
            session_id,
            {
                "type": "system",
                "content": f"已连接到本地聊天室，可以开始与 {global_config.bot.nickname} 对话了！",
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

                # 广播用户消息给所有连接（包括发送者）
                # 注意：用户消息会在 chat_bot.message_process 中自动保存到数据库
                await chat_manager.broadcast(
                    {
                        "type": "user_message",
                        "content": content,
                        "message_id": message_id,
                        "timestamp": timestamp,
                        "sender": {
                            "name": current_user_name,
                            "user_id": user_id,
                            "is_bot": False,
                        },
                    }
                )

                # 创建麦麦消息格式
                message_data = create_message_data(
                    content=content,
                    user_id=user_id,
                    user_name=current_user_name,
                    message_id=message_id,
                    is_at_bot=True,
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

    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: session={session_id}, user={user_id}")
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
    finally:
        chat_manager.disconnect(session_id, user_id)


@router.get("/info")
async def get_chat_info():
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
