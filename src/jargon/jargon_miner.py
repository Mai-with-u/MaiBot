import time
import json
import asyncio
from typing import List, Dict, Optional, Any
from json_repair import repair_json
from peewee import fn

from src.common.logger import get_logger
from src.common.database.database_model import Jargon
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from src.chat.message_receive.chat_stream import get_chat_manager
from src.plugin_system.apis import llm_api
from src.chat.utils.chat_message_builder import (
    build_anonymous_messages,
    get_raw_msg_by_timestamp_with_chat_inclusive,
    get_raw_msg_before_timestamp_with_chat,
    build_readable_messages_with_list,
)
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager


logger = get_logger("jargon")


def _contains_bot_self_name(content: str) -> bool:
    """
    判断词条是否包含机器人的昵称或别名
    """
    if not content:
        return False

    bot_config = getattr(global_config, "bot", None)
    if not bot_config:
        return False

    target = content.strip().lower()
    nickname = str(getattr(bot_config, "nickname", "") or "").strip().lower()
    alias_names = [str(alias or "").strip().lower() for alias in getattr(bot_config, "alias_names", []) or []]

    candidates = [name for name in [nickname, *alias_names] if name]

    return any(name in target for name in candidates if target)


def _init_prompt() -> None:
    prompt_str = """
**聊天内容，其中的SELF是你自己的发言**
{chat_str}

请从上面这段聊天内容中提取"可能是黑话"的候选项（黑话/俚语/网络缩写/口头禅）。
- 必须为对话中真实出现过的短词或短语
- 必须是你无法理解含义的词语，没有明确含义的词语
- 请不要选择有明确含义，或者含义清晰的词语
- 排除：人名、@、表情包/图片中的内容、纯标点、常规功能词（如的、了、呢、啊等）
- 每个词条长度建议 2-8 个字符（不强制），尽量短小
- 合并重复项，去重

黑话必须为以下几种类型：
- 由字母构成的，汉语拼音首字母的简写词，例如：nb、yyds、xswl
- 英文词语的缩写，用英文字母概括一个词汇或含义，例如：CPU、GPU、API
- 中文词语的缩写，用几个汉字概括一个词汇或含义，例如：社死、内卷

以 JSON 数组输出，元素为对象（严格按以下结构）：
[
  {{"content": "词条", "raw_content": "包含该词条的完整对话上下文原文"}},
  {{"content": "词条2", "raw_content": "包含该词条的完整对话上下文原文"}}
]

现在请输出：
"""
    Prompt(prompt_str, "extract_jargon_prompt")


def _init_inference_prompts() -> None:
    """初始化含义推断相关的prompt"""
    # Prompt 1: 基于raw_content和content推断
    prompt1_str = """
**词条内容**
{content}
**词条出现的上下文（raw_content）其中的SELF是你自己的发言**
{raw_content_list}

请根据以上词条内容和上下文，推断这个词条的含义。
- 如果这是一个黑话、俚语或网络用语，请推断其含义
- 如果含义明确（常规词汇），也请说明
- 如果上下文信息不足，无法推断含义，请设置 no_info 为 true

以 JSON 格式输出：
{{
  "meaning": "详细含义说明（包含使用场景、来源、具体解释等）",
  "no_info": false
}}
注意：如果信息不足无法推断，请设置 "no_info": true，此时 meaning 可以为空字符串
"""
    Prompt(prompt1_str, "jargon_inference_with_context_prompt")

    # Prompt 2: 仅基于content推断
    prompt2_str = """
**词条内容**
{content}

请仅根据这个词条本身，推断其含义。
- 如果这是一个黑话、俚语或网络用语，请推断其含义
- 如果含义明确（常规词汇），也请说明

以 JSON 格式输出：
{{
  "meaning": "详细含义说明（包含使用场景、来源、具体解释等）"
}}
"""
    Prompt(prompt2_str, "jargon_inference_content_only_prompt")

    # Prompt 3: 比较两个推断结果
    prompt3_str = """
**推断结果1（基于上下文）**
{inference1}

**推断结果2（仅基于词条）**
{inference2}

请比较这两个推断结果，判断它们是否相同或类似。
- 如果两个推断结果的"含义"相同或类似，说明这个词条不是黑话（含义明确）
- 如果两个推断结果有差异，说明这个词条可能是黑话（需要上下文才能理解）

以 JSON 格式输出：
{{
  "is_similar": true/false,
  "reason": "判断理由"
}}
"""
    Prompt(prompt3_str, "jargon_compare_inference_prompt")


_init_prompt()
_init_inference_prompts()


async def _enrich_raw_content_if_needed(
    content: str,
    raw_content_list: List[str],
    chat_id: str,
    messages: List[Any],
    extraction_start_time: float,
    extraction_end_time: float,
) -> List[str]:
    """
    检查raw_content是否只包含黑话本身，如果是，则获取该消息的前三条消息作为原始内容

    Args:
        content: 黑话内容
        raw_content_list: 原始raw_content列表
        chat_id: 聊天ID
        messages: 当前时间窗口内的消息列表
        extraction_start_time: 提取开始时间
        extraction_end_time: 提取结束时间

    Returns:
        处理后的raw_content列表
    """
    enriched_list = []

    for raw_content in raw_content_list:
        # 检查raw_content是否只包含黑话本身（去除空白字符后比较）
        raw_content_clean = raw_content.strip()
        content_clean = content.strip()

        # 如果raw_content只包含黑话本身（可能有一些标点或空白），则尝试获取上下文
        # 去除所有空白字符后比较，确保只包含黑话本身
        raw_content_normalized = raw_content_clean.replace(" ", "").replace("\n", "").replace("\t", "")
        content_normalized = content_clean.replace(" ", "").replace("\n", "").replace("\t", "")

        if raw_content_normalized == content_normalized:
            # 在消息列表中查找只包含该黑话的消息（去除空白后比较）
            target_message = None
            for msg in messages:
                msg_content = (msg.processed_plain_text or msg.display_message or "").strip()
                msg_content_normalized = msg_content.replace(" ", "").replace("\n", "").replace("\t", "")
                # 检查消息内容是否只包含黑话本身（去除空白后完全匹配）
                if msg_content_normalized == content_normalized:
                    target_message = msg
                    break

            if target_message and target_message.time:
                # 获取该消息的前三条消息
                try:
                    previous_messages = get_raw_msg_before_timestamp_with_chat(
                        chat_id=chat_id, timestamp=target_message.time, limit=3
                    )

                    if previous_messages:
                        # 将前三条消息和当前消息一起格式化
                        context_messages = previous_messages + [target_message]
                        # 按时间排序
                        context_messages.sort(key=lambda x: x.time or 0)

                        # 格式化为可读消息
                        formatted_context, _ = await build_readable_messages_with_list(
                            context_messages,
                            replace_bot_name=True,
                            timestamp_mode="relative",
                            truncate=False,
                        )

                        if formatted_context.strip():
                            enriched_list.append(formatted_context.strip())
                            logger.warning(f"为黑话 {content} 补充了上下文消息")
                        else:
                            # 如果格式化失败，使用原始raw_content
                            enriched_list.append(raw_content)
                    else:
                        # 没有找到前三条消息，使用原始raw_content
                        enriched_list.append(raw_content)
                except Exception as e:
                    logger.warning(f"获取黑话 {content} 的上下文消息失败: {e}")
                    # 出错时使用原始raw_content
                    enriched_list.append(raw_content)
            else:
                # 没有找到包含黑话的消息，使用原始raw_content
                enriched_list.append(raw_content)
        else:
            # raw_content包含更多内容，直接使用
            enriched_list.append(raw_content)

    return enriched_list


def _should_infer_meaning(jargon_obj: Jargon) -> bool:
    """
    判断是否需要进行含义推断
    在 count 达到 3,6, 10, 20, 40, 60, 100 时进行推断
    并且count必须大于last_inference_count，避免重启后重复判定
    如果is_complete为True，不再进行推断
    """
    # 如果已完成所有推断，不再推断
    if jargon_obj.is_complete:
        return False

    count = jargon_obj.count or 0
    last_inference = jargon_obj.last_inference_count or 0

    # 阈值列表：3,6, 10, 20, 40, 60, 100
    thresholds = [3, 6, 10, 20, 40, 60, 100]

    if count < thresholds[0]:
        return False

    # 如果count没有超过上次判定值，不需要判定
    if count <= last_inference:
        return False

    # 找到第一个大于last_inference的阈值
    next_threshold = None
    for threshold in thresholds:
        if threshold > last_inference:
            next_threshold = threshold
            break

    # 如果没有找到下一个阈值，说明已经超过100，不应该再推断
    if next_threshold is None:
        return False

    # 检查count是否达到或超过这个阈值
    return count >= next_threshold


class JargonMiner:
    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.last_learning_time: float = time.time()
        # 频率控制，可按需调整
        self.min_messages_for_learning: int = 10
        self.min_learning_interval: float = 20

        self.llm = LLMRequest(
            model_set=model_config.model_task_config.utils,
            request_type="jargon.extract",
        )

        # 初始化stream_name作为类属性，避免重复提取
        chat_manager = get_chat_manager()
        stream_name = chat_manager.get_stream_name(self.chat_id)
        self.stream_name = stream_name if stream_name else self.chat_id

    async def _infer_meaning_by_id(self, jargon_id: int) -> None:
        """通过ID加载对象并推断"""
        try:
            jargon_obj = Jargon.get_by_id(jargon_id)
            # 再次检查is_complete，因为可能在异步任务执行时已被标记为完成
            if jargon_obj.is_complete:
                logger.debug(f"jargon {jargon_obj.content} 已完成所有推断，跳过")
                return
            await self.infer_meaning(jargon_obj)
        except Exception as e:
            logger.error(f"通过ID推断jargon失败: {e}")

    async def infer_meaning(self, jargon_obj: Jargon) -> None:
        """
        对jargon进行含义推断
        """
        try:
            content = jargon_obj.content
            raw_content_str = jargon_obj.raw_content or ""

            # 解析raw_content列表
            raw_content_list = []
            if raw_content_str:
                try:
                    raw_content_list = (
                        json.loads(raw_content_str) if isinstance(raw_content_str, str) else raw_content_str
                    )
                    if not isinstance(raw_content_list, list):
                        raw_content_list = [raw_content_list] if raw_content_list else []
                except (json.JSONDecodeError, TypeError):
                    raw_content_list = [raw_content_str] if raw_content_str else []

            if not raw_content_list:
                logger.warning(f"jargon {content} 没有raw_content，跳过推断")
                return

            # 步骤1: 基于raw_content和content推断
            raw_content_text = "\n".join(raw_content_list)
            prompt1 = await global_prompt_manager.format_prompt(
                "jargon_inference_with_context_prompt",
                content=content,
                raw_content_list=raw_content_text,
            )

            response1, _ = await self.llm.generate_response_async(prompt1, temperature=0.3)
            if not response1:
                logger.warning(f"jargon {content} 推断1失败：无响应")
                return

            # 解析推断1结果
            inference1 = None
            try:
                resp1 = response1.strip()
                if resp1.startswith("{") and resp1.endswith("}"):
                    inference1 = json.loads(resp1)
                else:
                    repaired = repair_json(resp1)
                    inference1 = json.loads(repaired) if isinstance(repaired, str) else repaired
                if not isinstance(inference1, dict):
                    logger.warning(f"jargon {content} 推断1结果格式错误")
                    return
            except Exception as e:
                logger.error(f"jargon {content} 推断1解析失败: {e}")
                return

            # 检查推断1是否表示信息不足无法推断
            no_info = inference1.get("no_info", False)
            meaning1 = inference1.get("meaning", "").strip()
            if no_info or not meaning1:
                logger.info(f"jargon {content} 推断1表示信息不足无法推断，放弃本次推断，待下次更新")
                # 更新最后一次判定的count值，避免在同一阈值重复尝试
                jargon_obj.last_inference_count = jargon_obj.count or 0
                jargon_obj.save()
                return

            # 步骤2: 仅基于content推断
            prompt2 = await global_prompt_manager.format_prompt(
                "jargon_inference_content_only_prompt",
                content=content,
            )

            response2, _ = await self.llm.generate_response_async(prompt2, temperature=0.3)
            if not response2:
                logger.warning(f"jargon {content} 推断2失败：无响应")
                return

            # 解析推断2结果
            inference2 = None
            try:
                resp2 = response2.strip()
                if resp2.startswith("{") and resp2.endswith("}"):
                    inference2 = json.loads(resp2)
                else:
                    repaired = repair_json(resp2)
                    inference2 = json.loads(repaired) if isinstance(repaired, str) else repaired
                if not isinstance(inference2, dict):
                    logger.warning(f"jargon {content} 推断2结果格式错误")
                    return
            except Exception as e:
                logger.error(f"jargon {content} 推断2解析失败: {e}")
                return

            # logger.info(f"jargon {content} 推断2提示词: {prompt2}")
            # logger.info(f"jargon {content} 推断2结果: {response2}")
            # logger.info(f"jargon {content} 推断1提示词: {prompt1}")
            # logger.info(f"jargon {content} 推断1结果: {response1}")

            if global_config.debug.show_jargon_prompt:
                logger.info(f"jargon {content} 推断2提示词: {prompt2}")
                logger.info(f"jargon {content} 推断2结果: {response2}")
                logger.info(f"jargon {content} 推断1提示词: {prompt1}")
                logger.info(f"jargon {content} 推断1结果: {response1}")
            else:
                logger.debug(f"jargon {content} 推断2提示词: {prompt2}")
                logger.debug(f"jargon {content} 推断2结果: {response2}")
                logger.debug(f"jargon {content} 推断1提示词: {prompt1}")
                logger.debug(f"jargon {content} 推断1结果: {response1}")

            # 步骤3: 比较两个推断结果
            prompt3 = await global_prompt_manager.format_prompt(
                "jargon_compare_inference_prompt",
                inference1=json.dumps(inference1, ensure_ascii=False),
                inference2=json.dumps(inference2, ensure_ascii=False),
            )

            if global_config.debug.show_jargon_prompt:
                logger.info(f"jargon {content} 比较提示词: {prompt3}")

            response3, _ = await self.llm.generate_response_async(prompt3, temperature=0.3)
            if not response3:
                logger.warning(f"jargon {content} 比较失败：无响应")
                return

            # 解析比较结果
            comparison = None
            try:
                resp3 = response3.strip()
                if resp3.startswith("{") and resp3.endswith("}"):
                    comparison = json.loads(resp3)
                else:
                    repaired = repair_json(resp3)
                    comparison = json.loads(repaired) if isinstance(repaired, str) else repaired
                if not isinstance(comparison, dict):
                    logger.warning(f"jargon {content} 比较结果格式错误")
                    return
            except Exception as e:
                logger.error(f"jargon {content} 比较解析失败: {e}")
                return

            # 判断是否为黑话
            is_similar = comparison.get("is_similar", False)
            is_jargon = not is_similar  # 如果相似，说明不是黑话；如果有差异，说明是黑话

            # 更新数据库记录
            jargon_obj.is_jargon = is_jargon
            if is_jargon:
                # 是黑话，使用推断1的结果（基于上下文，更准确）
                jargon_obj.meaning = inference1.get("meaning", "")
            else:
                # 不是黑话，也记录含义（使用推断2的结果，因为含义明确）
                jargon_obj.meaning = inference2.get("meaning", "")

            # 更新最后一次判定的count值，避免重启后重复判定
            jargon_obj.last_inference_count = jargon_obj.count or 0

            # 如果count>=100，标记为完成，不再进行推断
            if (jargon_obj.count or 0) >= 100:
                jargon_obj.is_complete = True

            jargon_obj.save()
            logger.debug(
                f"jargon {content} 推断完成: is_jargon={is_jargon}, meaning={jargon_obj.meaning}, last_inference_count={jargon_obj.last_inference_count}, is_complete={jargon_obj.is_complete}"
            )

            # 固定输出推断结果，格式化为可读形式
            if is_jargon:
                # 是黑话，输出格式：[聊天名]xxx的含义是 xxxxxxxxxxx
                meaning = jargon_obj.meaning or "无详细说明"
                is_global = jargon_obj.is_global
                if is_global:
                    logger.info(f"[黑话]{content}的含义是 {meaning}")
                else:
                    logger.info(f"[{self.stream_name}]{content}的含义是 {meaning}")
            else:
                # 不是黑话，输出格式：[聊天名]xxx 不是黑话
                logger.info(f"[{self.stream_name}]{content} 不是黑话")

        except Exception as e:
            logger.error(f"jargon推断失败: {e}")
            import traceback

            traceback.print_exc()

    def should_trigger(self) -> bool:
        # 冷却时间检查
        if time.time() - self.last_learning_time < self.min_learning_interval:
            return False

        # 拉取最近消息数量是否足够
        recent_messages = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_learning_time,
            timestamp_end=time.time(),
        )
        return bool(recent_messages and len(recent_messages) >= self.min_messages_for_learning)

    async def run_once(self) -> None:
        try:
            if not self.should_trigger():
                return

            chat_stream = get_chat_manager().get_stream(self.chat_id)
            if not chat_stream:
                return

            # 记录本次提取的时间窗口，避免重复提取
            extraction_start_time = self.last_learning_time
            extraction_end_time = time.time()

            # 拉取学习窗口内的消息
            messages = get_raw_msg_by_timestamp_with_chat_inclusive(
                chat_id=self.chat_id,
                timestamp_start=extraction_start_time,
                timestamp_end=extraction_end_time,
                limit=20,
            )
            if not messages:
                return

            chat_str: str = await build_anonymous_messages(messages)
            if not chat_str.strip():
                return

            prompt: str = await global_prompt_manager.format_prompt(
                "extract_jargon_prompt",
                chat_str=chat_str,
            )

            response, _ = await self.llm.generate_response_async(prompt, temperature=0.2)
            if not response:
                return

            if global_config.debug.show_jargon_prompt:
                logger.info(f"jargon提取提示词: {prompt}")
                logger.info(f"jargon提取结果: {response}")

            # 解析为JSON
            entries: List[dict] = []
            try:
                resp = response.strip()
                parsed = None
                if resp.startswith("[") and resp.endswith("]"):
                    parsed = json.loads(resp)
                else:
                    repaired = repair_json(resp)
                    if isinstance(repaired, str):
                        parsed = json.loads(repaired)
                    else:
                        parsed = repaired

                if isinstance(parsed, dict):
                    parsed = [parsed]

                if not isinstance(parsed, list):
                    return

                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    content = str(item.get("content", "")).strip()
                    raw_content_value = item.get("raw_content", "")

                    # 处理raw_content：可能是字符串或列表
                    raw_content_list = []
                    if isinstance(raw_content_value, list):
                        raw_content_list = [str(rc).strip() for rc in raw_content_value if str(rc).strip()]
                        # 去重
                        raw_content_list = list(dict.fromkeys(raw_content_list))
                    elif isinstance(raw_content_value, str):
                        raw_content_str = raw_content_value.strip()
                        if raw_content_str:
                            raw_content_list = [raw_content_str]

                    if content and raw_content_list:
                        if _contains_bot_self_name(content):
                            logger.debug(f"解析阶段跳过包含机器人昵称/别名的词条: {content}")
                            continue
                        entries.append({"content": content, "raw_content": raw_content_list})
            except Exception as e:
                logger.error(f"解析jargon JSON失败: {e}; 原始: {response}")
                return

            if not entries:
                return

            # 去重并写入DB（按 chat_id + content 去重）
            # 使用content作为去重键
            seen = set()
            uniq_entries = []
            for entry in entries:
                content_key = entry["content"]
                if content_key not in seen:
                    seen.add(content_key)
                    uniq_entries.append(entry)

            saved = 0
            updated = 0
            for entry in uniq_entries:
                content = entry["content"]
                raw_content_list = entry["raw_content"]  # 已经是列表

                # 检查并补充raw_content：如果只包含黑话本身，则获取前三条消息作为上下文
                raw_content_list = await _enrich_raw_content_if_needed(
                    content=content,
                    raw_content_list=raw_content_list,
                    chat_id=self.chat_id,
                    messages=messages,
                    extraction_start_time=extraction_start_time,
                    extraction_end_time=extraction_end_time,
                )

                try:
                    # 根据all_global配置决定查询逻辑
                    if global_config.jargon.all_global:
                        # 开启all_global：无视chat_id，查询所有content匹配的记录（所有记录都是全局的）
                        query = Jargon.select().where(Jargon.content == content)
                    else:
                        # 关闭all_global：只查询chat_id匹配的记录（不考虑is_global）
                        query = Jargon.select().where((Jargon.chat_id == self.chat_id) & (Jargon.content == content))

                    if query.exists():
                        obj = query.get()
                        try:
                            obj.count = (obj.count or 0) + 1
                        except Exception:
                            obj.count = 1

                        # 合并raw_content列表：读取现有列表，追加新值，去重
                        existing_raw_content = []
                        if obj.raw_content:
                            try:
                                existing_raw_content = (
                                    json.loads(obj.raw_content) if isinstance(obj.raw_content, str) else obj.raw_content
                                )
                                if not isinstance(existing_raw_content, list):
                                    existing_raw_content = [existing_raw_content] if existing_raw_content else []
                            except (json.JSONDecodeError, TypeError):
                                existing_raw_content = [obj.raw_content] if obj.raw_content else []

                        # 合并并去重
                        merged_list = list(dict.fromkeys(existing_raw_content + raw_content_list))
                        obj.raw_content = json.dumps(merged_list, ensure_ascii=False)

                        # 开启all_global时，确保记录标记为is_global=True
                        if global_config.jargon.all_global:
                            obj.is_global = True
                        # 关闭all_global时，保持原有is_global不变（不修改）

                        obj.save()

                        # 检查是否需要推断（达到阈值且超过上次判定值）
                        if _should_infer_meaning(obj):
                            # 异步触发推断，不阻塞主流程
                            # 重新加载对象以确保数据最新
                            jargon_id = obj.id
                            asyncio.create_task(self._infer_meaning_by_id(jargon_id))

                        updated += 1
                    else:
                        # 没找到匹配记录，创建新记录
                        if global_config.jargon.all_global:
                            # 开启all_global：新记录默认为is_global=True
                            is_global_new = True
                        else:
                            # 关闭all_global：新记录is_global=False
                            is_global_new = False

                        Jargon.create(
                            content=content,
                            raw_content=json.dumps(raw_content_list, ensure_ascii=False),
                            chat_id=self.chat_id,
                            is_global=is_global_new,
                            count=1,
                        )
                        saved += 1
                except Exception as e:
                    logger.error(f"保存jargon失败: chat_id={self.chat_id}, content={content}, err={e}")
                    continue

            # 固定输出提取的jargon结果，格式化为可读形式（只要有提取结果就输出）
            if uniq_entries:
                # 收集所有提取的jargon内容
                jargon_list = [entry["content"] for entry in uniq_entries]
                jargon_str = ",".join(jargon_list)

                # 输出格式化的结果（使用logger.info会自动应用jargon模块的颜色）
                logger.info(f"[{self.stream_name}]疑似黑话: {jargon_str}")

                # 更新为本次提取的结束时间，确保不会重复提取相同的消息窗口
                self.last_learning_time = extraction_end_time

            if saved or updated:
                logger.info(f"jargon写入: 新增 {saved} 条，更新 {updated} 条，chat_id={self.chat_id}")
        except Exception as e:
            logger.error(f"JargonMiner 运行失败: {e}")


class JargonMinerManager:
    def __init__(self) -> None:
        self._miners: dict[str, JargonMiner] = {}

    def get_miner(self, chat_id: str) -> JargonMiner:
        if chat_id not in self._miners:
            self._miners[chat_id] = JargonMiner(chat_id)
        return self._miners[chat_id]


miner_manager = JargonMinerManager()


async def extract_and_store_jargon(chat_id: str) -> None:
    miner = miner_manager.get_miner(chat_id)
    await miner.run_once()


def search_jargon(
    keyword: str, chat_id: Optional[str] = None, limit: int = 10, case_sensitive: bool = False, fuzzy: bool = True
) -> List[Dict[str, str]]:
    """
    搜索jargon，支持大小写不敏感和模糊搜索

    Args:
        keyword: 搜索关键词
        chat_id: 可选的聊天ID
            - 如果开启了all_global：此参数被忽略，查询所有is_global=True的记录
            - 如果关闭了all_global：如果提供则优先搜索该聊天或global的jargon
        limit: 返回结果数量限制，默认10
        case_sensitive: 是否大小写敏感，默认False（不敏感）
        fuzzy: 是否模糊搜索，默认True（使用LIKE匹配）

    Returns:
        List[Dict[str, str]]: 包含content, meaning的字典列表
    """
    if not keyword or not keyword.strip():
        return []

    keyword = keyword.strip()

    # 构建查询
    query = Jargon.select(Jargon.content, Jargon.meaning)

    # 构建搜索条件
    if case_sensitive:
        # 大小写敏感
        if fuzzy:
            # 模糊搜索
            search_condition = Jargon.content.contains(keyword)
        else:
            # 精确匹配
            search_condition = Jargon.content == keyword
    else:
        # 大小写不敏感
        if fuzzy:
            # 模糊搜索（使用LOWER函数）
            search_condition = fn.LOWER(Jargon.content).contains(keyword.lower())
        else:
            # 精确匹配（使用LOWER函数）
            search_condition = fn.LOWER(Jargon.content) == keyword.lower()

    query = query.where(search_condition)

    # 根据all_global配置决定查询逻辑
    if global_config.jargon.all_global:
        # 开启all_global：所有记录都是全局的，查询所有is_global=True的记录（无视chat_id）
        query = query.where(Jargon.is_global)
    else:
        # 关闭all_global：如果提供了chat_id，优先搜索该聊天或global的jargon
        if chat_id:
            query = query.where((Jargon.chat_id == chat_id) | Jargon.is_global)

    # 只返回有meaning的记录
    query = query.where((Jargon.meaning.is_null(False)) & (Jargon.meaning != ""))

    # 按count降序排序，优先返回出现频率高的
    query = query.order_by(Jargon.count.desc())

    # 限制结果数量
    query = query.limit(limit)

    # 执行查询并返回结果
    results = []
    for jargon in query:
        results.append({"content": jargon.content or "", "meaning": jargon.meaning or ""})

    return results


async def store_jargon_from_answer(jargon_keyword: str, answer: str, chat_id: str) -> None:
    """将黑话存入jargon系统

    Args:
        jargon_keyword: 黑话关键词
        answer: 答案内容（将概括为raw_content）
        chat_id: 聊天ID
    """
    try:
        # 概括答案为简短的raw_content
        summary_prompt = f"""请将以下答案概括为一句简短的话（不超过50字），作为黑话"{jargon_keyword}"的使用示例：

答案：{answer}

只输出概括后的内容，不要输出其他内容："""

        success, summary, _, _ = await llm_api.generate_with_model(
            summary_prompt,
            model_config=model_config.model_task_config.utils_small,
            request_type="memory.summarize_jargon",
        )

        logger.info(f"概括答案提示: {summary_prompt}")
        logger.info(f"概括答案: {summary}")

        if not success:
            logger.warning(f"概括答案失败，使用原始答案: {summary}")
            summary = answer[:100]  # 截取前100字符作为备用

        raw_content = summary.strip()[:200]  # 限制长度

        # 检查是否已存在
        if global_config.jargon.all_global:
            query = Jargon.select().where(Jargon.content == jargon_keyword)
        else:
            query = Jargon.select().where((Jargon.chat_id == chat_id) & (Jargon.content == jargon_keyword))

        if query.exists():
            # 更新现有记录
            obj = query.get()
            obj.count = (obj.count or 0) + 1

            # 合并raw_content列表
            existing_raw_content = []
            if obj.raw_content:
                try:
                    existing_raw_content = (
                        json.loads(obj.raw_content) if isinstance(obj.raw_content, str) else obj.raw_content
                    )
                    if not isinstance(existing_raw_content, list):
                        existing_raw_content = [existing_raw_content] if existing_raw_content else []
                except (json.JSONDecodeError, TypeError):
                    existing_raw_content = [obj.raw_content] if obj.raw_content else []

            # 合并并去重
            merged_list = list(dict.fromkeys(existing_raw_content + [raw_content]))
            obj.raw_content = json.dumps(merged_list, ensure_ascii=False)

            if global_config.jargon.all_global:
                obj.is_global = True

            obj.save()
            logger.info(f"更新jargon记录: {jargon_keyword}")
        else:
            # 创建新记录
            is_global_new = True if global_config.jargon.all_global else False
            Jargon.create(
                content=jargon_keyword,
                raw_content=json.dumps([raw_content], ensure_ascii=False),
                chat_id=chat_id,
                is_global=is_global_new,
                count=1,
            )
            logger.info(f"创建新jargon记录: {jargon_keyword}")

    except Exception as e:
        logger.error(f"存储jargon失败: {e}")
