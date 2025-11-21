"""
聊天内容概括器
用于累积、打包和压缩聊天记录
"""

import asyncio
import json
import time
from typing import List, Optional, Set
from dataclasses import dataclass

from src.common.logger import get_logger
from src.common.data_models.database_data_model import DatabaseMessages
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest
from src.plugin_system.apis import message_api
from src.chat.utils.chat_message_builder import build_readable_messages
from src.person_info.person_info import Person
from src.chat.message_receive.chat_stream import get_chat_manager

logger = get_logger("chat_history_summarizer")


@dataclass
class MessageBatch:
    """消息批次"""

    messages: List[DatabaseMessages]
    start_time: float
    end_time: float
    is_preparing: bool = False  # 是否处于准备结束模式


class ChatHistorySummarizer:
    """聊天内容概括器"""

    def __init__(self, chat_id: str, check_interval: int = 60):
        """
        初始化聊天内容概括器

        Args:
            chat_id: 聊天ID
            check_interval: 定期检查间隔（秒），默认60秒
        """
        self.chat_id = chat_id
        self._chat_display_name = self._get_chat_display_name()
        self.log_prefix = f"[{self._chat_display_name}]"

        # 记录时间点，用于计算新消息
        self.last_check_time = time.time()

        # 当前累积的消息批次
        self.current_batch: Optional[MessageBatch] = None

        # LLM请求器，用于压缩聊天内容
        self.summarizer_llm = LLMRequest(
            model_set=model_config.model_task_config.utils, request_type="chat_history_summarizer"
        )

        # 后台循环相关
        self.check_interval = check_interval  # 检查间隔（秒）
        self._periodic_task: Optional[asyncio.Task] = None
        self._running = False

    def _get_chat_display_name(self) -> str:
        """获取聊天显示名称"""
        try:
            chat_name = get_chat_manager().get_stream_name(self.chat_id)
            if chat_name:
                return chat_name
            # 如果获取失败，使用简化的chat_id显示
            if len(self.chat_id) > 20:
                return f"{self.chat_id[:8]}..."
            return self.chat_id
        except Exception:
            # 如果获取失败，使用简化的chat_id显示
            if len(self.chat_id) > 20:
                return f"{self.chat_id[:8]}..."
            return self.chat_id

    async def process(self, current_time: Optional[float] = None):
        """
        处理聊天内容概括

        Args:
            current_time: 当前时间戳，如果为None则使用time.time()
        """
        if current_time is None:
            current_time = time.time()

        try:
            # 获取从上次检查时间到当前时间的新消息
            new_messages = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=self.last_check_time,
                end_time=current_time,
                limit=0,
                limit_mode="latest",
                filter_mai=False,  # 不过滤bot消息，因为需要检查bot是否发言
                filter_command=False,
            )

            if not new_messages:
                # 没有新消息，检查是否需要打包
                if self.current_batch and self.current_batch.messages:
                    await self._check_and_package(current_time)
                self.last_check_time = current_time
                return

            logger.debug(
                f"{self.log_prefix} 开始处理聊天概括，时间窗口: {self.last_check_time:.2f} -> {current_time:.2f}"
            )

            # 有新消息，更新最后检查时间
            self.last_check_time = current_time

            # 如果有当前批次，添加新消息
            if self.current_batch:
                before_count = len(self.current_batch.messages)
                self.current_batch.messages.extend(new_messages)
                self.current_batch.end_time = current_time
                logger.info(f"{self.log_prefix} 更新聊天话题: {before_count} -> {len(self.current_batch.messages)} 条消息")
            else:
                # 创建新批次
                self.current_batch = MessageBatch(
                    messages=new_messages,
                    start_time=new_messages[0].time if new_messages else current_time,
                    end_time=current_time,
                )
                logger.info(f"{self.log_prefix} 新建聊天话题: {len(new_messages)} 条消息")

            # 检查是否需要打包
            await self._check_and_package(current_time)

        except Exception as e:
            logger.error(f"{self.log_prefix} 处理聊天内容概括时出错: {e}")
            import traceback

            traceback.print_exc()

    async def _check_and_package(self, current_time: float):
        """检查是否需要打包"""
        if not self.current_batch or not self.current_batch.messages:
            return

        messages = self.current_batch.messages
        message_count = len(messages)
        last_message_time = messages[-1].time if messages else current_time
        time_since_last_message = current_time - last_message_time

        # 格式化时间差显示
        if time_since_last_message < 60:
            time_str = f"{time_since_last_message:.1f}秒"
        elif time_since_last_message < 3600:
            time_str = f"{time_since_last_message / 60:.1f}分钟"
        else:
            time_str = f"{time_since_last_message / 3600:.1f}小时"

        preparing_status = "是" if self.current_batch.is_preparing else "否"

        logger.info(
            f"{self.log_prefix} 批次状态检查 | 消息数: {message_count} | 距最后消息: {time_str} | 准备结束模式: {preparing_status}"
        )

        # 检查打包条件
        should_package = False

        # 条件1: 消息长度超过120，直接打包
        if message_count >= 120:
            should_package = True
            logger.info(f"{self.log_prefix} 触发打包条件: 消息数量达到 {message_count} 条（阈值: 120条）")

        # 条件2: 最后一条消息的时间和当前时间差>600秒，直接打包
        elif time_since_last_message > 600:
            should_package = True
            logger.info(f"{self.log_prefix} 触发打包条件: 距最后消息 {time_str}（阈值: 10分钟）")

        # 条件3: 消息长度超过100，进入准备结束模式
        elif message_count > 100:
            if not self.current_batch.is_preparing:
                self.current_batch.is_preparing = True
                logger.info(f"{self.log_prefix} 消息数量 {message_count} 条超过阈值（100条），进入准备结束模式")

            # 在准备结束模式下，如果最后一条消息的时间和当前时间差>10秒，就打包
            if time_since_last_message > 10:
                should_package = True
                logger.info(f"{self.log_prefix} 触发打包条件: 准备结束模式下，距最后消息 {time_str}（阈值: 10秒）")

        if should_package:
            await self._package_and_store()

    async def _package_and_store(self):
        """打包并存储聊天记录"""
        if not self.current_batch or not self.current_batch.messages:
            return

        messages = self.current_batch.messages
        start_time = self.current_batch.start_time
        end_time = self.current_batch.end_time

        logger.info(
            f"{self.log_prefix} 开始打包批次 | 消息数: {len(messages)} | 时间范围: {start_time:.2f} - {end_time:.2f}"
        )

        # 检查是否有bot发言
        # 第一条消息前推600s到最后一条消息的时间内
        check_start_time = max(start_time - 600, 0)
        check_end_time = end_time

        # 使用包含边界的时间范围查询
        bot_messages = message_api.get_messages_by_time_in_chat_inclusive(
            chat_id=self.chat_id,
            start_time=check_start_time,
            end_time=check_end_time,
            limit=0,
            limit_mode="latest",
            filter_mai=False,
            filter_command=False,
        )

        # 检查是否有bot的发言
        has_bot_message = False
        bot_user_id = str(global_config.bot.qq_account)
        for msg in bot_messages:
            if msg.user_info.user_id == bot_user_id:
                has_bot_message = True
                break

        if not has_bot_message:
            logger.info(
                f"{self.log_prefix} 批次内无Bot发言，丢弃批次 | 检查时间范围: {check_start_time:.2f} - {check_end_time:.2f}"
            )
            self.current_batch = None
            return

        # 有bot发言，进行压缩和存储
        try:
            # 构建对话原文
            original_text = build_readable_messages(
                messages=messages,
                replace_bot_name=True,
                timestamp_mode="normal_no_YMD",
                read_mark=0.0,
                truncate=False,
                show_actions=False,
            )

            # 获取参与的所有人的昵称
            participants_set: Set[str] = set()
            for msg in messages:
                # 使用 msg.user_platform（扁平化字段）或 msg.user_info.platform
                platform = (
                    getattr(msg, "user_platform", None)
                    or (msg.user_info.platform if msg.user_info else None)
                    or msg.chat_info.platform
                )
                person = Person(platform=platform, user_id=msg.user_info.user_id)
                person_name = person.person_name
                if person_name:
                    participants_set.add(person_name)
            participants = list(participants_set)
            logger.info(f"{self.log_prefix} 批次参与者: {', '.join(participants) if participants else '未知'}")

            # 使用LLM压缩聊天内容
            success, theme, keywords, summary = await self._compress_with_llm(original_text)

            if not success:
                logger.warning(f"{self.log_prefix} LLM压缩失败，不存储到数据库 | 消息数: {len(messages)}")
                # 清空当前批次，避免重复处理
                self.current_batch = None
                return

            logger.info(
                f"{self.log_prefix} LLM压缩完成 | 主题: {theme} | 关键词数: {len(keywords)} | 概括长度: {len(summary)} 字"
            )

            # 存储到数据库
            await self._store_to_database(
                start_time=start_time,
                end_time=end_time,
                original_text=original_text,
                participants=participants,
                theme=theme,
                keywords=keywords,
                summary=summary,
            )

            logger.info(f"{self.log_prefix} 成功打包并存储聊天记录 | 消息数: {len(messages)} | 主题: {theme}")

            # 清空当前批次
            self.current_batch = None

        except Exception as e:
            logger.error(f"{self.log_prefix} 打包和存储聊天记录时出错: {e}")
            import traceback

            traceback.print_exc()
            # 出错时也清空批次，避免重复处理
            self.current_batch = None

    async def _compress_with_llm(self, original_text: str) -> tuple[bool, str, List[str], str]:
        """
        使用LLM压缩聊天内容

        Returns:
            tuple[bool, str, List[str], str]: (是否成功, 主题, 关键词列表, 概括)
        """
        prompt = f"""请对以下聊天记录进行概括，提取以下信息：

1. 主题：这段对话的主要内容，一个简短的标题（不超过20字）
2. 关键词：这段对话的关键词，用列表形式返回（3-10个关键词）
3. 概括：对这段话的平文本概括（50-200字）

请以JSON格式返回，格式如下：
{{
    "theme": "主题",
    "keywords": ["关键词1", "关键词2", ...],
    "summary": "概括内容"
}}

聊天记录：
{original_text}

请直接返回JSON，不要包含其他内容。"""

        try:
            response, _ = await self.summarizer_llm.generate_response_async(
                prompt=prompt,
                temperature=0.3,
                max_tokens=500,
            )

            # 解析JSON响应
            import re

            # 移除可能的markdown代码块标记
            json_str = response.strip()
            json_str = re.sub(r"^```json\s*", "", json_str, flags=re.MULTILINE)
            json_str = re.sub(r"^```\s*", "", json_str, flags=re.MULTILINE)
            json_str = json_str.strip()

            # 尝试找到JSON对象的开始和结束位置
            # 查找第一个 { 和最后一个匹配的 }
            start_idx = json_str.find("{")
            if start_idx == -1:
                raise ValueError("未找到JSON对象开始标记")

            # 从后往前查找最后一个 }
            end_idx = json_str.rfind("}")
            if end_idx == -1 or end_idx <= start_idx:
                raise ValueError("未找到JSON对象结束标记")

            # 提取JSON字符串
            json_str = json_str[start_idx : end_idx + 1]

            # 尝试解析JSON
            try:
                result = json.loads(json_str)
            except json.JSONDecodeError:
                # 如果解析失败，尝试修复字符串值中的中文引号
                # 简单方法：将字符串值中的中文引号替换为转义的英文引号
                # 使用状态机方法：遍历字符串，在字符串值内部替换中文引号
                fixed_chars = []
                in_string = False
                escape_next = False
                i = 0
                while i < len(json_str):
                    char = json_str[i]
                    if escape_next:
                        fixed_chars.append(char)
                        escape_next = False
                    elif char == "\\":
                        fixed_chars.append(char)
                        escape_next = True
                    elif char == '"' and not escape_next:
                        fixed_chars.append(char)
                        in_string = not in_string
                    elif in_string and (char == '"' or char == '"'):
                        # 在字符串值内部，将中文引号替换为转义的英文引号
                        fixed_chars.append('\\"')
                    else:
                        fixed_chars.append(char)
                    i += 1

                json_str = "".join(fixed_chars)
                # 再次尝试解析
                result = json.loads(json_str)

            theme = result.get("theme", "未命名对话")
            keywords = result.get("keywords", [])
            summary = result.get("summary", "无概括")

            # 确保keywords是列表
            if isinstance(keywords, str):
                keywords = [keywords]

            return True, theme, keywords, summary

        except Exception as e:
            logger.error(f"{self.log_prefix} LLM压缩聊天内容时出错: {e}")
            logger.error(f"{self.log_prefix} LLM响应: {response if 'response' in locals() else 'N/A'}")
            # 返回失败标志和默认值
            return False, "未命名对话", [], "压缩失败，无法生成概括"

    async def _store_to_database(
        self,
        start_time: float,
        end_time: float,
        original_text: str,
        participants: List[str],
        theme: str,
        keywords: List[str],
        summary: str,
    ):
        """存储到数据库"""
        try:
            from src.common.database.database_model import ChatHistory
            from src.plugin_system.apis import database_api

            # 准备数据
            data = {
                "chat_id": self.chat_id,
                "start_time": start_time,
                "end_time": end_time,
                "original_text": original_text,
                "participants": json.dumps(participants, ensure_ascii=False),
                "theme": theme,
                "keywords": json.dumps(keywords, ensure_ascii=False),
                "summary": summary,
                "count": 0,
            }

            # 使用db_save存储（使用start_time和chat_id作为唯一标识）
            # 由于可能有多条记录，我们使用组合键，但peewee不支持，所以使用start_time作为唯一标识
            # 但为了避免冲突，我们使用组合键：chat_id + start_time
            # 由于peewee不支持组合键，我们直接创建新记录（不提供key_field和key_value）
            saved_record = await database_api.db_save(
                ChatHistory,
                data=data,
            )

            if saved_record:
                logger.debug(f"{self.log_prefix} 成功存储聊天历史记录到数据库")
            else:
                logger.warning(f"{self.log_prefix} 存储聊天历史记录到数据库失败")

        except Exception as e:
            logger.error(f"{self.log_prefix} 存储到数据库时出错: {e}")
            import traceback

            traceback.print_exc()
            raise

    async def start(self):
        """启动后台定期检查循环"""
        if self._running:
            logger.warning(f"{self.log_prefix} 后台循环已在运行，无需重复启动")
            return

        self._running = True
        self._periodic_task = asyncio.create_task(self._periodic_check_loop())
        logger.info(f"{self.log_prefix} 已启动后台定期检查循环 | 检查间隔: {self.check_interval}秒")

    async def stop(self):
        """停止后台定期检查循环"""
        self._running = False
        if self._periodic_task:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
            self._periodic_task = None
        logger.info(f"{self.log_prefix} 已停止后台定期检查循环")

    async def _periodic_check_loop(self):
        """后台定期检查循环"""
        try:
            while self._running:
                # 执行一次检查
                await self.process()

                # 等待指定间隔后再次检查
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} 后台检查循环被取消")
            raise
        except Exception as e:
            logger.error(f"{self.log_prefix} 后台检查循环出错: {e}")
            import traceback

            traceback.print_exc()
            self._running = False
