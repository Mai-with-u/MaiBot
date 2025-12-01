"""
聊天内容概括器
用于累积、打包和压缩聊天记录
"""

import asyncio
import json
import time
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from json_repair import repair_json

from src.common.logger import get_logger
from src.common.data_models.database_data_model import DatabaseMessages
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest
from src.plugin_system.apis import message_api
from src.chat.utils.chat_message_builder import build_readable_messages
from src.person_info.person_info import Person
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager

logger = get_logger("chat_history_summarizer")

HIPPO_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "hippo_memorizer"


def init_prompt():
    """初始化提示词模板"""

    topic_analysis_prompt = """
【历史话题标题列表】（仅标题，不含具体内容）：
{history_topics_block}

【本次聊天记录】（每条消息前有编号，用于后续引用）：
{messages_block}

请完成以下任务：
**识别话题**
1. 识别【本次聊天记录】中正在进行的一个或多个话题；
2. 判断【历史话题标题列表】中的话题是否在【本次聊天记录】中出现，如果出现，则直接使用该历史话题标题字符串；

**选取消息**
1. 对于每个话题（新话题或历史话题），从上述带编号的消息中选出与该话题强相关的消息编号列表；
2. 每个话题用一句话清晰地描述正在发生的事件，必须包含时间（大致即可）、人物、主要事件和主题，保证精准且有区分度； 

请先输出一段简短思考，说明有什么话题，哪些是不包含在历史话题中的，哪些是包含在历史话题中的，并说明为什么；
然后严格以 JSON 格式输出【本次聊天记录】中涉及的话题，格式如下：
[
  {{
    "topic": "话题",
    "message_indices": [1, 2, 5]
  }},
  ...
]
"""
    Prompt(topic_analysis_prompt, "hippo_topic_analysis_prompt")

    topic_summary_prompt = """
请基于以下话题，对聊天记录片段进行概括，提取以下信息：

**话题**：{topic}

**要求**：
1. 关键词：提取与话题相关的关键词，用列表形式返回（3-10个关键词）
2. 概括：对这段话的平文本概括（50-200字），要求：
   - 仔细地转述发生的事件和聊天内容；
   - 可以适当摘取聊天记录中的原文；
   - 重点突出事件的发展过程和结果；
   - 围绕话题这个中心进行概括。
3. 关键信息：提取话题中的关键信息点，用列表形式返回（3-8个关键信息点），每个关键信息点应该简洁明了。

请以JSON格式返回，格式如下：
{{
    "keywords": ["关键词1", "关键词2", ...],
    "summary": "概括内容",
    "key_point": ["关键信息1", "关键信息2", ...]
}}

聊天记录：
{original_text}

请直接返回JSON，不要包含其他内容。
"""
    Prompt(topic_summary_prompt, "hippo_topic_summary_prompt")


@dataclass
class MessageBatch:
    """消息批次（用于触发话题检查的原始消息累积）"""

    messages: List[DatabaseMessages]
    start_time: float
    end_time: float


@dataclass
class TopicCacheItem:
    """
    话题缓存项

    Attributes:
        topic: 话题标题（一句话描述时间、人物、事件和主题）
        messages: 与该话题相关的消息字符串列表（已经通过 build 函数转成可读文本）
        participants: 涉及到的发言人昵称集合
        no_update_checks: 连续多少次“检查”没有新增内容
    """

    topic: str
    messages: List[str] = field(default_factory=list)
    participants: Set[str] = field(default_factory=set)
    no_update_checks: int = 0


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

        # 记录上一次话题检查的时间，用于判断是否需要触发检查
        self.last_topic_check_time = time.time()

        # 当前累积的消息批次
        self.current_batch: Optional[MessageBatch] = None

        # 话题缓存：topic_str -> TopicCacheItem
        # 在内存中维护，并通过本地文件实时持久化
        self.topic_cache: Dict[str, TopicCacheItem] = {}
        self._safe_chat_id = self._sanitize_chat_id(self.chat_id)
        self._topic_cache_file = HIPPO_CACHE_DIR / f"{self._safe_chat_id}.json"
        # 注意：批次加载需要异步查询消息，所以在 start() 中调用

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

    def _sanitize_chat_id(self, chat_id: str) -> str:
        """用于生成可作为文件名的 chat_id"""
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", chat_id)

    def _load_topic_cache_from_disk(self):
        """在启动时加载本地话题缓存（同步部分），支持重启后继续"""
        try:
            if not self._topic_cache_file.exists():
                return

            with self._topic_cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)

            self.last_topic_check_time = data.get("last_topic_check_time", self.last_topic_check_time)
            topics_data = data.get("topics", {})
            loaded_count = 0
            for topic, payload in topics_data.items():
                self.topic_cache[topic] = TopicCacheItem(
                    topic=topic,
                    messages=payload.get("messages", []),
                    participants=set(payload.get("participants", [])),
                    no_update_checks=payload.get("no_update_checks", 0),
                )
                loaded_count += 1

            if loaded_count:
                logger.info(f"{self.log_prefix} 已加载 {loaded_count} 个话题缓存，继续追踪")
        except Exception as e:
            logger.error(f"{self.log_prefix} 加载话题缓存失败: {e}")

    async def _load_batch_from_disk(self):
        """在启动时加载聊天批次，支持重启后继续"""
        try:
            if not self._topic_cache_file.exists():
                return

            with self._topic_cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)

            batch_data = data.get("current_batch")
            if not batch_data:
                return

            start_time = batch_data.get("start_time")
            end_time = batch_data.get("end_time")
            if not start_time or not end_time:
                return

            # 根据时间范围重新查询消息
            messages = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=start_time,
                end_time=end_time,
                limit=0,
                limit_mode="latest",
                filter_mai=False,
                filter_command=False,
            )

            if messages:
                self.current_batch = MessageBatch(
                    messages=messages,
                    start_time=start_time,
                    end_time=end_time,
                )
                logger.info(f"{self.log_prefix} 已恢复聊天批次，包含 {len(messages)} 条消息")
        except Exception as e:
            logger.error(f"{self.log_prefix} 加载聊天批次失败: {e}")

    def _persist_topic_cache(self):
        """实时持久化话题缓存和聊天批次，避免重启后丢失"""
        try:
            # 如果既没有话题缓存也没有批次，删除缓存文件
            if not self.topic_cache and not self.current_batch:
                if self._topic_cache_file.exists():
                    self._topic_cache_file.unlink()
                return

            HIPPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "chat_id": self.chat_id,
                "last_topic_check_time": self.last_topic_check_time,
                "topics": {
                    topic: {
                        "messages": item.messages,
                        "participants": list(item.participants),
                        "no_update_checks": item.no_update_checks,
                    }
                    for topic, item in self.topic_cache.items()
                },
            }

            # 保存当前批次的时间范围（如果有）
            if self.current_batch:
                data["current_batch"] = {
                    "start_time": self.current_batch.start_time,
                    "end_time": self.current_batch.end_time,
                }

            with self._topic_cache_file.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"{self.log_prefix} 持久化话题缓存失败: {e}")

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
                # 没有新消息，检查是否需要进行“话题检查”
                if self.current_batch and self.current_batch.messages:
                    await self._check_and_run_topic_check(current_time)
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
                logger.info(f"{self.log_prefix} 更新聊天检查批次: {before_count} -> {len(self.current_batch.messages)} 条消息")
                # 更新批次后持久化
                self._persist_topic_cache()
            else:
                # 创建新批次
                self.current_batch = MessageBatch(
                    messages=new_messages,
                    start_time=new_messages[0].time if new_messages else current_time,
                    end_time=current_time,
                )
                logger.debug(f"{self.log_prefix} 新建聊天检查批次: {len(new_messages)} 条消息")
                # 创建批次后持久化
                self._persist_topic_cache()

            # 检查是否需要触发“话题检查”
            await self._check_and_run_topic_check(current_time)

        except Exception as e:
            logger.error(f"{self.log_prefix} 处理聊天内容概括时出错: {e}")
            import traceback

            traceback.print_exc()

    async def _check_and_run_topic_check(self, current_time: float):
        """
        检查是否需要进行一次“话题检查”

        触发条件：
        - 当前批次消息数 >= 100，或者
        - 距离上一次检查的时间 > 3600 秒（1小时）
        """
        if not self.current_batch or not self.current_batch.messages:
            return

        messages = self.current_batch.messages
        message_count = len(messages)
        time_since_last_check = current_time - self.last_topic_check_time

        # 格式化时间差显示
        if time_since_last_check < 60:
            time_str = f"{time_since_last_check:.1f}秒"
        elif time_since_last_check < 3600:
            time_str = f"{time_since_last_check / 60:.1f}分钟"
        else:
            time_str = f"{time_since_last_check / 3600:.1f}小时"

        logger.debug(
            f"{self.log_prefix} 批次状态检查 | 消息数: {message_count} | 距上次检查: {time_str}"
        )

        # 检查“话题检查”触发条件
        should_check = False

        # 条件1: 消息数量 >= 100，触发一次检查
        if message_count >= 80:
            should_check = True
            logger.info(f"{self.log_prefix} 触发检查条件: 消息数量达到 {message_count} 条（阈值: 100条）")

        # 条件2: 距离上一次检查 > 3600 秒（1小时），触发一次检查
        elif time_since_last_check > 2400:
            should_check = True
            logger.info(f"{self.log_prefix} 触发检查条件: 距上次检查 {time_str}（阈值: 1小时）")

        if should_check:
            await self._run_topic_check_and_update_cache(messages)
            # 本批次已经被处理为话题信息，可以清空
            self.current_batch = None
            # 更新上一次检查时间，并持久化
            self.last_topic_check_time = current_time
            self._persist_topic_cache()

    async def _run_topic_check_and_update_cache(self, messages: List[DatabaseMessages]):
        """
        执行一次“话题检查”：
        1. 首先确认这段消息里是否有 Bot 发言，没有则直接丢弃本次批次；
        2. 将消息编号并转成字符串，构造 LLM Prompt；
        3. 把历史话题标题列表放入 Prompt，要求 LLM：
           - 识别当前聊天中的话题（1 个或多个）；
           - 为每个话题选出相关消息编号；
           - 若话题属于历史话题，则沿用原话题标题；
        4. LLM 返回 JSON：多个 {topic, message_indices}；
        5. 更新本地话题缓存，并根据规则触发“话题打包存储”。
        """
        if not messages:
            return

        start_time = messages[0].time
        end_time = messages[-1].time

        logger.info(
            f"{self.log_prefix} 开始话题检查 | 消息数: {len(messages)} | 时间范围: {start_time:.2f} - {end_time:.2f}"
        )

        # 1. 检查当前批次内是否有 bot 发言（只检查当前批次，不往前推）
        # 原因：我们要记录的是 bot 参与过的对话片段，如果当前批次内 bot 没有发言，
        # 说明 bot 没有参与这段对话，不应该记录
        bot_user_id = str(global_config.bot.qq_account)
        has_bot_message = False
        
        for msg in messages:
            if msg.user_info.user_id == bot_user_id:
                has_bot_message = True
                break

        if not has_bot_message:
            logger.info(
                f"{self.log_prefix} 当前批次内无 Bot 发言，丢弃本次检查 | 时间范围: {start_time:.2f} - {end_time:.2f}"
            )
            return

        # 2. 构造编号后的消息字符串和参与者信息
        numbered_lines, index_to_msg_str, index_to_msg_text, index_to_participants = self._build_numbered_messages_for_llm(messages)

        # 3. 调用 LLM 识别话题，并得到 topic -> indices
        existing_topics = list(self.topic_cache.keys())
        success, topic_to_indices = await self._analyze_topics_with_llm(
            numbered_lines=numbered_lines,
            existing_topics=existing_topics,
        )

        if not success or not topic_to_indices:
            logger.warning(f"{self.log_prefix} 话题识别失败或无有效话题，本次检查忽略")
            # 即使识别失败，也认为是一次“检查”，但不更新 no_update_checks（保持原状）
            return

        # 4. 统计哪些话题在本次检查中有新增内容
        updated_topics: Set[str] = set()

        for topic, indices in topic_to_indices.items():
            if not indices:
                continue

            item = self.topic_cache.get(topic)
            if not item:
                # 新话题
                item = TopicCacheItem(topic=topic)
                self.topic_cache[topic] = item

            # 收集属于该话题的消息文本（不带编号）
            topic_msg_texts: List[str] = []
            new_participants: Set[str] = set()
            for idx in indices:
                msg_text = index_to_msg_text.get(idx)
                if not msg_text:
                    continue
                topic_msg_texts.append(msg_text)
                new_participants.update(index_to_participants.get(idx, set()))

            if not topic_msg_texts:
                continue

            # 将本次检查中属于该话题的所有消息合并为一个字符串（不带编号）
            merged_text = "\n".join(topic_msg_texts)
            item.messages.append(merged_text)
            item.participants.update(new_participants)
            # 本次检查中该话题有更新，重置计数
            item.no_update_checks = 0
            updated_topics.add(topic)

        # 5. 对于本次没有更新的历史话题，no_update_checks + 1
        for topic, item in list(self.topic_cache.items()):
            if topic not in updated_topics:
                item.no_update_checks += 1

        # 6. 检查是否有话题需要打包存储
        topics_to_finalize: List[str] = []
        for topic, item in self.topic_cache.items():
            if item.no_update_checks >= 3:
                logger.info(f"{self.log_prefix} 话题[{topic}] 连续 3 次检查无新增内容，触发打包存储")
                topics_to_finalize.append(topic)
                continue
            if len(item.messages) > 5:
                logger.info(f"{self.log_prefix} 话题[{topic}] 消息条数超过 4，触发打包存储")
                topics_to_finalize.append(topic)

        for topic in topics_to_finalize:
            item = self.topic_cache.get(topic)
            if not item:
                continue
            try:
                await self._finalize_and_store_topic(
                    topic=topic,
                    item=item,
                    # 这里的时间范围尽量覆盖最近一次检查的区间
                    start_time=start_time,
                    end_time=end_time,
                )
            finally:
                # 无论成功与否，都从缓存中删除，避免重复
                self.topic_cache.pop(topic, None)

    def _build_numbered_messages_for_llm(
        self, messages: List[DatabaseMessages]
    ) -> tuple[List[str], Dict[int, str], Dict[int, str], Dict[int, Set[str]]]:
        """
        将消息转为带编号的字符串，供 LLM 选择使用。

        返回:
            numbered_lines: ["1. xxx", "2. yyy", ...]  # 带编号，用于 LLM 选择
            index_to_msg_str: idx -> "idx. xxx"  # 带编号，用于 LLM 选择
            index_to_msg_text: idx -> "xxx"  # 不带编号，用于最终存储
            index_to_participants: idx -> {nickname1, nickname2, ...}
        """
        numbered_lines: List[str] = []
        index_to_msg_str: Dict[int, str] = {}
        index_to_msg_text: Dict[int, str] = {}  # 不带编号的消息文本
        index_to_participants: Dict[int, Set[str]] = {}

        for idx, msg in enumerate(messages, start=1):
            # 使用 build_readable_messages 生成可读文本
            try:
                text = build_readable_messages(
                    messages=[msg],
                    replace_bot_name=True,
                    timestamp_mode="normal_no_YMD",
                    read_mark=0.0,
                    truncate=False,
                    show_actions=False,
                ).strip()
            except Exception:
                # 回退到简单文本
                text = getattr(msg, "processed_plain_text", "") or ""

            # 获取发言人昵称
            participants: Set[str] = set()
            try:
                platform = (
                    getattr(msg, "user_platform", None)
                    or (msg.user_info.platform if msg.user_info else None)
                    or msg.chat_info.platform
                )
                user_id = msg.user_info.user_id if msg.user_info else None
                if platform and user_id:
                    person = Person(platform=platform, user_id=user_id)
                    if person.person_name:
                        participants.add(person.person_name)
            except Exception:
                pass

            # 带编号的字符串（用于 LLM 选择）
            line = f"{idx}. {text}"
            numbered_lines.append(line)
            index_to_msg_str[idx] = line
            # 不带编号的文本（用于最终存储）
            index_to_msg_text[idx] = text
            index_to_participants[idx] = participants

        return numbered_lines, index_to_msg_str, index_to_msg_text, index_to_participants

    async def _analyze_topics_with_llm(
        self,
        numbered_lines: List[str],
        existing_topics: List[str],
    ) -> tuple[bool, Dict[str, List[int]]]:
        """
        使用 LLM 识别本次检查中的话题，并为每个话题选择相关消息编号。

        要求：
        - 话题用一句话清晰描述正在发生的事件，包括时间、人物、主要事件和主题；
        - 可以有 1 个或多个话题；
        - 若某个话题与历史话题列表中的某个话题是同一件事，请直接使用历史话题的字符串；
        - 输出 JSON，格式：
          [
            {
              "topic": "话题标题字符串",
              "message_indices": [1, 2, 5]
            },
            ...
          ]
        """
        if not numbered_lines:
            return False, {}

        history_topics_block = (
            "\n".join(f"- {t}" for t in existing_topics) if existing_topics else "（当前无历史话题）"
        )
        messages_block = "\n".join(numbered_lines)

        prompt = await global_prompt_manager.format_prompt(
            "hippo_topic_analysis_prompt",
            history_topics_block=history_topics_block,
            messages_block=messages_block,
        )

        try:
            response, _ = await self.summarizer_llm.generate_response_async(
                prompt=prompt,
                temperature=0.2,
                max_tokens=800,
            )

            logger.info(f"{self.log_prefix} 话题识别LLM Prompt: {prompt}")
            logger.info(f"{self.log_prefix} 话题识别LLM Response: {response}")

            # 尝试从响应中提取JSON代码块
            json_str = None
            json_pattern = r"```json\s*(.*?)\s*```"
            matches = re.findall(json_pattern, response, re.DOTALL)
            
            if matches:
                # 找到JSON代码块，使用第一个匹配
                json_str = matches[0].strip()
            else:
                # 如果没有找到代码块，尝试查找JSON数组的开始和结束位置
                # 查找第一个 [ 和最后一个 ]
                start_idx = response.find('[')
                end_idx = response.rfind(']')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    json_str = response[start_idx:end_idx + 1].strip()
                else:
                    # 如果还是找不到，尝试直接使用整个响应（移除可能的markdown标记）
                    json_str = response.strip()
                    json_str = re.sub(r"^```json\s*", "", json_str, flags=re.MULTILINE)
                    json_str = re.sub(r"^```\s*", "", json_str, flags=re.MULTILINE)
                    json_str = json_str.strip()

            # 使用json_repair修复可能的JSON错误
            if json_str:
                try:
                    repaired_json = repair_json(json_str)
                    result = json.loads(repaired_json) if isinstance(repaired_json, str) else repaired_json
                except Exception as repair_error:
                    # 如果repair失败，尝试直接解析
                    logger.warning(f"{self.log_prefix} JSON修复失败，尝试直接解析: {repair_error}")
                    result = json.loads(json_str)
            else:
                raise ValueError("无法从响应中提取JSON内容")

            if not isinstance(result, list):
                logger.error(f"{self.log_prefix} 话题识别返回的 JSON 不是列表: {result}")
                return False, {}

            topic_to_indices: Dict[str, List[int]] = {}
            for item in result:
                if not isinstance(item, dict):
                    continue
                topic = item.get("topic")
                indices = item.get("message_indices") or item.get("messages") or []
                if not topic or not isinstance(topic, str):
                    continue
                if isinstance(indices, list):
                    valid_indices: List[int] = []
                    for v in indices:
                        try:
                            iv = int(v)
                            if iv > 0:
                                valid_indices.append(iv)
                        except (TypeError, ValueError):
                            continue
                    if valid_indices:
                        topic_to_indices[topic] = valid_indices

            return True, topic_to_indices

        except Exception as e:
            logger.error(f"{self.log_prefix} 话题识别 LLM 调用或解析失败: {e}")
            logger.error(f"{self.log_prefix} LLM响应: {response if 'response' in locals() else 'N/A'}")
            return False, {}

    async def _finalize_and_store_topic(
        self,
        topic: str,
        item: TopicCacheItem,
        start_time: float,
        end_time: float,
    ):
        """
        对某个话题进行最终打包存储：
        1. 将 messages(list[str]) 拼接为 original_text；
        2. 使用 LLM 对 original_text 进行总结，得到 summary 和 keywords，theme 直接使用话题字符串；
        3. 写入数据库 ChatHistory；
        4. 完成后，调用方会从缓存中删除该话题。
        """
        if not item.messages:
            logger.info(f"{self.log_prefix} 话题[{topic}] 无消息内容，跳过打包")
            return

        original_text = "\n".join(item.messages)

        logger.info(
            f"{self.log_prefix} 开始打包话题[{topic}] | 消息数: {len(item.messages)} | 时间范围: {start_time:.2f} - {end_time:.2f}"
        )

        # 使用 LLM 进行总结（基于话题名）
        success, keywords, summary, key_point = await self._compress_with_llm(original_text, topic)
        if not success:
            logger.warning(f"{self.log_prefix} 话题[{topic}] LLM 概括失败，不写入数据库")
            return

        participants = list(item.participants)

        await self._store_to_database(
            start_time=start_time,
            end_time=end_time,
            original_text=original_text,
            participants=participants,
            theme=topic,  # 主题直接使用话题名
            keywords=keywords,
            summary=summary,
            key_point=key_point,
        )

        logger.info(
            f"{self.log_prefix} 话题[{topic}] 成功打包并存储 | 消息数: {len(item.messages)} | 参与者数: {len(participants)}"
        )

    async def _compress_with_llm(self, original_text: str, topic: str) -> tuple[bool, List[str], str, List[str]]:
        """
        使用LLM压缩聊天内容（用于单个话题的最终总结）

        Args:
            original_text: 聊天记录原文
            topic: 话题名称

        Returns:
            tuple[bool, List[str], str, List[str]]: (是否成功, 关键词列表, 概括, 关键信息列表)
        """
        prompt = await global_prompt_manager.format_prompt(
            "hippo_topic_summary_prompt",
            topic=topic,
            original_text=original_text,
        )

        try:
            response, _ = await self.summarizer_llm.generate_response_async(
                prompt=prompt,
                temperature=0.3,
                max_tokens=500,
            )

            # 解析JSON响应
            json_str = response.strip()
            json_str = re.sub(r"^```json\s*", "", json_str, flags=re.MULTILINE)
            json_str = re.sub(r"^```\s*", "", json_str, flags=re.MULTILINE)
            json_str = json_str.strip()

            # 查找JSON对象的开始与结束
            start_idx = json_str.find("{")
            if start_idx == -1:
                raise ValueError("未找到JSON对象开始标记")

            end_idx = json_str.rfind("}")
            if end_idx == -1 or end_idx <= start_idx:
                logger.warning(f"{self.log_prefix} JSON缺少结束标记，尝试自动修复")
                extracted_json = json_str[start_idx:]
            else:
                extracted_json = json_str[start_idx : end_idx + 1]

            def _parse_with_quote_fix(payload: str) -> Dict[str, Any]:
                fixed_chars: List[str] = []
                in_string = False
                escape_next = False
                i = 0
                while i < len(payload):
                    char = payload[i]
                    if escape_next:
                        fixed_chars.append(char)
                        escape_next = False
                    elif char == "\\":
                        fixed_chars.append(char)
                        escape_next = True
                    elif char == '"' and not escape_next:
                        fixed_chars.append(char)
                        in_string = not in_string
                    elif in_string and char in {"“", "”"}:
                        # 在字符串值内部，将中文引号替换为转义的英文引号
                        fixed_chars.append('\\"')
                    else:
                        fixed_chars.append(char)
                    i += 1

                repaired = "".join(fixed_chars)
                return json.loads(repaired)

            try:
                result = json.loads(extracted_json)
            except json.JSONDecodeError:
                try:
                    repaired_json = repair_json(extracted_json)
                    if isinstance(repaired_json, str):
                        result = json.loads(repaired_json)
                    else:
                        result = repaired_json
                except Exception as repair_error:
                    logger.warning(f"{self.log_prefix} repair_json 失败，使用引号修复: {repair_error}")
                    result = _parse_with_quote_fix(extracted_json)

            keywords = result.get("keywords", [])
            summary = result.get("summary", "无概括")
            key_point = result.get("key_point", [])

            # 确保keywords和key_point是列表
            if isinstance(keywords, str):
                keywords = [keywords]
            if isinstance(key_point, str):
                key_point = [key_point]

            return True, keywords, summary, key_point

        except Exception as e:
            logger.error(f"{self.log_prefix} LLM压缩聊天内容时出错: {e}")
            logger.error(f"{self.log_prefix} LLM响应: {response if 'response' in locals() else 'N/A'}")
            # 返回失败标志和默认值
            return False, [], "压缩失败，无法生成概括", []

    async def _store_to_database(
        self,
        start_time: float,
        end_time: float,
        original_text: str,
        participants: List[str],
        theme: str,
        keywords: List[str],
        summary: str,
        key_point: Optional[List[str]] = None,
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

            # 存储 key_point（如果存在）
            if key_point is not None:
                data["key_point"] = json.dumps(key_point, ensure_ascii=False)

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

        # 加载聊天批次（如果有）
        await self._load_batch_from_disk()

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


init_prompt()

