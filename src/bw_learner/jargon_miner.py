import json
import asyncio
import random
from collections import OrderedDict
from typing import List, Dict, Optional, Any
from json_repair import repair_json
from peewee import fn

from src.common.logger import get_logger
from src.common.database.database_model import Jargon
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config, global_config
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.utils.chat_message_builder import (
    build_readable_messages_with_id,
)
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.bw_learner.learner_utils import (
    is_bot_message,
    build_context_paragraph,
    contains_bot_self_name,
    parse_chat_id_list,
    chat_id_list_contains,
    update_chat_id_list,
)


logger = get_logger("jargon")


def _is_single_char_jargon(content: str) -> bool:
    """
    判断是否是单字黑话（单个汉字、英文或数字）

    Args:
        content: 词条内容

    Returns:
        bool: 如果是单字黑话返回True，否则返回False
    """
    if not content or len(content) != 1:
        return False

    char = content[0]
    # 判断是否是单个汉字、单个英文字母或单个数字
    return (
        "\u4e00" <= char <= "\u9fff"  # 汉字
        or "a" <= char <= "z"  # 小写字母
        or "A" <= char <= "Z"  # 大写字母
        or "0" <= char <= "9"  # 数字
    )


def _init_prompt() -> None:
    prompt_str = """
**聊天内容，其中的{bot_name}的发言内容是你自己的发言，[msg_id] 是消息ID**
{chat_str}

请从上面这段聊天内容中提取"可能是黑话"的候选项（黑话/俚语/网络缩写/口头禅）。
- 必须为对话中真实出现过的短词或短语
- 必须是你无法理解含义的词语，没有明确含义的词语，请不要选择有明确含义，或者含义清晰的词语
- 排除：人名、@、表情包/图片中的内容、纯标点、常规功能词（如的、了、呢、啊等）
- 每个词条长度建议 2-8 个字符（不强制），尽量短小

黑话必须为以下几种类型：
- 由字母构成的，汉语拼音首字母的简写词，例如：nb、yyds、xswl
- 英文词语的缩写，用英文字母概括一个词汇或含义，例如：CPU、GPU、API
- 中文词语的缩写，用几个汉字概括一个词汇或含义，例如：社死、内卷

以 JSON 数组输出，元素为对象（严格按以下结构）：
请你提取出可能的黑话，最多30个黑话，请尽量提取所有
[
  {{"content": "词条", "msg_id": "m12"}},  // msg_id 必须与上方聊天中展示的ID完全一致
  {{"content": "词条2", "msg_id": "m15"}}
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
**词条出现的上下文。其中的{bot_name}的发言内容是你自己的发言**
{raw_content_list}
{previous_meaning_section}

请根据上下文，推断"{content}"这个词条的含义。
- 如果这是一个黑话、俚语或网络用语，请推断其含义
- 如果含义明确（常规词汇），也请说明
- {bot_name} 的发言内容可能包含错误，请不要参考其发言内容
- 如果上下文信息不足，无法推断含义，请设置 no_info 为 true
{previous_meaning_instruction}

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
    thresholds = [2, 4, 8, 12, 24, 60, 100]

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

        self.llm = LLMRequest(
            model_set=model_config.model_task_config.utils,
            request_type="jargon.extract",
        )

        self.llm_inference = LLMRequest(
            model_set=model_config.model_task_config.utils,
            request_type="jargon.inference",
        )

        # 初始化stream_name作为类属性，避免重复提取
        chat_manager = get_chat_manager()
        stream_name = chat_manager.get_stream_name(self.chat_id)
        self.stream_name = stream_name if stream_name else self.chat_id
        self.cache_limit = 50
        self.cache: OrderedDict[str, None] = OrderedDict()

        # 黑话提取锁，防止并发执行
        self._extraction_lock = asyncio.Lock()

    def _add_to_cache(self, content: str) -> None:
        """将提取到的黑话加入缓存，保持LRU语义"""
        if not content:
            return

        key = content.strip()
        if not key:
            return

        # 单字黑话（单个汉字、英文或数字）不记录到缓存
        if _is_single_char_jargon(key):
            return

        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            self.cache[key] = None
            if len(self.cache) > self.cache_limit:
                self.cache.popitem(last=False)

    def _collect_cached_entries(self, messages: List[Any]) -> List[Dict[str, List[str]]]:
        """检查缓存中的黑话是否出现在当前消息窗口，生成对应上下文"""
        if not self.cache or not messages:
            return []

        cached_entries: List[Dict[str, List[str]]] = []
        processed_pairs = set()

        for idx, msg in enumerate(messages):
            msg_text = (
                getattr(msg, "display_message", None) or getattr(msg, "processed_plain_text", None) or ""
            ).strip()
            if not msg_text or is_bot_message(msg):
                continue

            for content in self.cache.keys():
                if not content:
                    continue
                if (content, idx) in processed_pairs:
                    continue
                if content in msg_text:
                    paragraph = build_context_paragraph(messages, idx)
                    if not paragraph:
                        continue
                    cached_entries.append({"content": content, "raw_content": [paragraph]})
                    processed_pairs.add((content, idx))

        return cached_entries

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

            # 获取当前count和上一次的meaning
            current_count = jargon_obj.count or 0
            previous_meaning = jargon_obj.meaning or ""

            # 当count为24, 60时，随机移除一半的raw_content项目
            if current_count in [24, 60] and len(raw_content_list) > 1:
                # 计算要保留的数量（至少保留1个）
                keep_count = max(1, len(raw_content_list) // 2)
                raw_content_list = random.sample(raw_content_list, keep_count)
                logger.info(
                    f"jargon {content} count={current_count}，随机移除后剩余 {len(raw_content_list)} 个raw_content项目"
                )

            # 步骤1: 基于raw_content和content推断
            raw_content_text = "\n".join(raw_content_list)

            # 当count为24, 60, 100时，在prompt中放入上一次推断出的meaning作为参考
            previous_meaning_section = ""
            previous_meaning_instruction = ""
            if current_count in [24, 60, 100] and previous_meaning:
                previous_meaning_section = f"""
**上一次推断的含义（仅供参考）**
{previous_meaning}
"""
                previous_meaning_instruction = (
                    "- 请参考上一次推断的含义，结合新的上下文信息，给出更准确或更新的推断结果"
                )

            prompt1 = await global_prompt_manager.format_prompt(
                "jargon_inference_with_context_prompt",
                content=content,
                bot_name=global_config.bot.nickname,
                raw_content_list=raw_content_text,
                previous_meaning_section=previous_meaning_section,
                previous_meaning_instruction=previous_meaning_instruction,
            )

            response1, _ = await self.llm_inference.generate_response_async(prompt1, temperature=0.3)
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

            response2, _ = await self.llm_inference.generate_response_async(prompt2, temperature=0.3)
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

            response3, _ = await self.llm_inference.generate_response_async(prompt3, temperature=0.3)
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
                # 不是黑话，清空含义，不再存储任何内容
                jargon_obj.meaning = ""

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

    async def run_once(self, messages: List[Any]) -> None:
        """
        运行一次黑话提取

        Args:
            messages: 外部传入的消息列表（必需）
        """
        # 使用异步锁防止并发执行
        async with self._extraction_lock:
            try:
                if not messages:
                    return

                # 按时间排序，确保编号与上下文一致
                messages = sorted(messages, key=lambda msg: msg.time or 0)

                chat_str, message_id_list = build_readable_messages_with_id(
                    messages=messages,
                    replace_bot_name=True,
                    timestamp_mode="relative",
                    truncate=False,
                    show_actions=False,
                    show_pic=True,
                    pic_single=True,
                )
                if not chat_str.strip():
                    return

                msg_id_to_index: Dict[str, int] = {}
                for idx, (msg_id, _msg) in enumerate(message_id_list or []):
                    if not msg_id:
                        continue
                    msg_id_to_index[msg_id] = idx
                if not msg_id_to_index:
                    logger.warning("未能生成消息ID映射，跳过本次提取")
                    return

                prompt: str = await global_prompt_manager.format_prompt(
                    "extract_jargon_prompt",
                    bot_name=global_config.bot.nickname,
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
                        msg_id_value = item.get("msg_id")

                        if not content:
                            continue

                        if contains_bot_self_name(content):
                            logger.info(f"解析阶段跳过包含机器人昵称/别名的词条: {content}")
                            continue

                        msg_id_str = str(msg_id_value or "").strip()
                        if not msg_id_str:
                            logger.warning(f"解析jargon失败：msg_id缺失，content={content}")
                            continue

                        msg_index = msg_id_to_index.get(msg_id_str)
                        if msg_index is None:
                            logger.warning(f"解析jargon失败：msg_id未找到，content={content}, msg_id={msg_id_str}")
                            continue

                        target_msg = messages[msg_index]
                        if is_bot_message(target_msg):
                            logger.info(f"解析阶段跳过引用机器人自身消息的词条: content={content}, msg_id={msg_id_str}")
                            continue

                        context_paragraph = build_context_paragraph(messages, msg_index)
                        if not context_paragraph:
                            logger.warning(f"解析jargon失败：上下文为空，content={content}, msg_id={msg_id_str}")
                            continue

                        entries.append({"content": content, "raw_content": [context_paragraph]})
                    cached_entries = self._collect_cached_entries(messages)
                    if cached_entries:
                        entries.extend(cached_entries)
                except Exception as e:
                    logger.error(f"解析jargon JSON失败: {e}; 原始: {response}")
                    return

                if not entries:
                    return

                # 去重并合并raw_content（按 content 聚合）
                merged_entries: OrderedDict[str, Dict[str, List[str]]] = OrderedDict()
                for entry in entries:
                    content_key = entry["content"]
                    raw_list = entry.get("raw_content", []) or []
                    if content_key in merged_entries:
                        merged_entries[content_key]["raw_content"].extend(raw_list)
                    else:
                        merged_entries[content_key] = {
                            "content": content_key,
                            "raw_content": list(raw_list),
                        }

                uniq_entries = []
                for merged_entry in merged_entries.values():
                    raw_content_list = merged_entry["raw_content"]
                    if raw_content_list:
                        merged_entry["raw_content"] = list(dict.fromkeys(raw_content_list))
                    uniq_entries.append(merged_entry)

                saved = 0
                updated = 0
                for entry in uniq_entries:
                    content = entry["content"]
                    raw_content_list = entry["raw_content"]  # 已经是列表

                    try:
                        # 查询所有content匹配的记录
                        query = Jargon.select().where(Jargon.content == content)

                        # 查找匹配的记录
                        matched_obj = None
                        for obj in query:
                            if global_config.expression.all_global_jargon:
                                # 开启all_global：所有content匹配的记录都可以
                                matched_obj = obj
                                break
                            else:
                                # 关闭all_global：需要检查chat_id列表是否包含目标chat_id
                                chat_id_list = parse_chat_id_list(obj.chat_id)
                                if chat_id_list_contains(chat_id_list, self.chat_id):
                                    matched_obj = obj
                                    break

                        if matched_obj:
                            obj = matched_obj
                            try:
                                obj.count = (obj.count or 0) + 1
                            except Exception:
                                obj.count = 1

                            # 合并raw_content列表：读取现有列表，追加新值，去重
                            existing_raw_content = []
                            if obj.raw_content:
                                try:
                                    existing_raw_content = (
                                        json.loads(obj.raw_content)
                                        if isinstance(obj.raw_content, str)
                                        else obj.raw_content
                                    )
                                    if not isinstance(existing_raw_content, list):
                                        existing_raw_content = [existing_raw_content] if existing_raw_content else []
                                except (json.JSONDecodeError, TypeError):
                                    existing_raw_content = [obj.raw_content] if obj.raw_content else []

                            # 合并并去重
                            merged_list = list(dict.fromkeys(existing_raw_content + raw_content_list))
                            obj.raw_content = json.dumps(merged_list, ensure_ascii=False)

                            # 更新chat_id列表：增加当前chat_id的计数
                            chat_id_list = parse_chat_id_list(obj.chat_id)
                            updated_chat_id_list = update_chat_id_list(chat_id_list, self.chat_id, increment=1)
                            obj.chat_id = json.dumps(updated_chat_id_list, ensure_ascii=False)

                            # 开启all_global时，确保记录标记为is_global=True
                            if global_config.expression.all_global_jargon:
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
                            if global_config.expression.all_global_jargon:
                                # 开启all_global：新记录默认为is_global=True
                                is_global_new = True
                            else:
                                # 关闭all_global：新记录is_global=False
                                is_global_new = False

                            # 使用新格式创建chat_id列表：[[chat_id, count]]
                            chat_id_list = [[self.chat_id, 1]]
                            chat_id_json = json.dumps(chat_id_list, ensure_ascii=False)

                            Jargon.create(
                                content=content,
                                raw_content=json.dumps(raw_content_list, ensure_ascii=False),
                                chat_id=chat_id_json,
                                is_global=is_global_new,
                                count=1,
                            )
                            saved += 1
                    except Exception as e:
                        logger.error(f"保存jargon失败: chat_id={self.chat_id}, content={content}, err={e}")
                        continue
                    finally:
                        self._add_to_cache(content)

                # 固定输出提取的jargon结果，格式化为可读形式（只要有提取结果就输出）
                if uniq_entries:
                    # 收集所有提取的jargon内容
                    jargon_list = [entry["content"] for entry in uniq_entries]
                    jargon_str = ",".join(jargon_list)

                    # 输出格式化的结果（使用logger.info会自动应用jargon模块的颜色）
                    logger.info(f"[{self.stream_name}]疑似黑话: {jargon_str}")

                if saved or updated:
                    logger.info(f"jargon写入: 新增 {saved} 条，更新 {updated} 条，chat_id={self.chat_id}")
            except Exception as e:
                logger.error(f"JargonMiner 运行失败: {e}")
                # 即使失败也保持时间戳更新，避免频繁重试

    async def process_extracted_entries(self, entries: List[Dict[str, List[str]]]) -> None:
        """
        处理已提取的黑话条目（从 expression_learner 路由过来的）

        Args:
            entries: 黑话条目列表，每个元素格式为 {"content": "...", "raw_content": [...]}
        """
        if not entries:
            return

        try:
            # 去重并合并raw_content（按 content 聚合）
            merged_entries: OrderedDict[str, Dict[str, List[str]]] = OrderedDict()
            for entry in entries:
                content_key = entry["content"]
                raw_list = entry.get("raw_content", []) or []
                if content_key in merged_entries:
                    merged_entries[content_key]["raw_content"].extend(raw_list)
                else:
                    merged_entries[content_key] = {
                        "content": content_key,
                        "raw_content": list(raw_list),
                    }

            uniq_entries = []
            for merged_entry in merged_entries.values():
                raw_content_list = merged_entry["raw_content"]
                if raw_content_list:
                    merged_entry["raw_content"] = list(dict.fromkeys(raw_content_list))
                uniq_entries.append(merged_entry)

            saved = 0
            updated = 0
            for entry in uniq_entries:
                content = entry["content"]
                raw_content_list = entry["raw_content"]  # 已经是列表

                try:
                    # 查询所有content匹配的记录
                    query = Jargon.select().where(Jargon.content == content)

                    # 查找匹配的记录
                    matched_obj = None
                    for obj in query:
                        if global_config.expression.all_global_jargon:
                            # 开启all_global：所有content匹配的记录都可以
                            matched_obj = obj
                            break
                        else:
                            # 关闭all_global：需要检查chat_id列表是否包含目标chat_id
                            chat_id_list = parse_chat_id_list(obj.chat_id)
                            if chat_id_list_contains(chat_id_list, self.chat_id):
                                matched_obj = obj
                                break

                    if matched_obj:
                        obj = matched_obj
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

                        # 更新chat_id列表：增加当前chat_id的计数
                        chat_id_list = parse_chat_id_list(obj.chat_id)
                        updated_chat_id_list = update_chat_id_list(chat_id_list, self.chat_id, increment=1)
                        obj.chat_id = json.dumps(updated_chat_id_list, ensure_ascii=False)

                        # 开启all_global时，确保记录标记为is_global=True
                        if global_config.expression.all_global_jargon:
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
                        if global_config.expression.all_global_jargon:
                            # 开启all_global：新记录默认为is_global=True
                            is_global_new = True
                        else:
                            # 关闭all_global：新记录is_global=False
                            is_global_new = False

                        # 使用新格式创建chat_id列表：[[chat_id, count]]
                        chat_id_list = [[self.chat_id, 1]]
                        chat_id_json = json.dumps(chat_id_list, ensure_ascii=False)

                        Jargon.create(
                            content=content,
                            raw_content=json.dumps(raw_content_list, ensure_ascii=False),
                            chat_id=chat_id_json,
                            is_global=is_global_new,
                            count=1,
                        )
                        saved += 1
                except Exception as e:
                    logger.error(f"保存jargon失败: chat_id={self.chat_id}, content={content}, err={e}")
                    continue
                finally:
                    self._add_to_cache(content)

            # 固定输出提取的jargon结果，格式化为可读形式（只要有提取结果就输出）
            if uniq_entries:
                # 收集所有提取的jargon内容
                jargon_list = [entry["content"] for entry in uniq_entries]
                jargon_str = ",".join(jargon_list)

                # 输出格式化的结果（使用logger.info会自动应用jargon模块的颜色）
                logger.info(f"[{self.stream_name}]疑似黑话: {jargon_str}")

            if saved or updated:
                logger.info(f"jargon写入: 新增 {saved} 条，更新 {updated} 条，chat_id={self.chat_id}")
        except Exception as e:
            logger.error(f"处理已提取的黑话条目失败: {e}")


class JargonMinerManager:
    def __init__(self) -> None:
        self._miners: dict[str, JargonMiner] = {}

    def get_miner(self, chat_id: str) -> JargonMiner:
        if chat_id not in self._miners:
            self._miners[chat_id] = JargonMiner(chat_id)
        return self._miners[chat_id]


miner_manager = JargonMinerManager()


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

    # 构建查询（选择所有需要的字段，以便后续过滤）
    query = Jargon.select()

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
    if global_config.expression.all_global_jargon:
        # 开启all_global：所有记录都是全局的，查询所有is_global=True的记录（无视chat_id）
        query = query.where(Jargon.is_global)
    # 注意：对于all_global=False的情况，chat_id过滤在Python层面进行，以便兼容新旧格式

    # 注意：meaning的过滤移到Python层面，因为我们需要先过滤chat_id

    # 按count降序排序，优先返回出现频率高的
    query = query.order_by(Jargon.count.desc())

    # 限制结果数量（先多取一些，因为后面可能过滤）
    query = query.limit(limit * 2)

    # 执行查询并返回结果，过滤chat_id
    results = []
    for jargon in query:
        # 如果提供了chat_id且all_global=False，需要检查chat_id列表是否包含目标chat_id
        if chat_id and not global_config.expression.all_global_jargon:
            chat_id_list = parse_chat_id_list(jargon.chat_id)
            # 如果记录是is_global=True，或者chat_id列表包含目标chat_id，则包含
            if not jargon.is_global and not chat_id_list_contains(chat_id_list, chat_id):
                continue

        # 只返回有meaning的记录
        if not jargon.meaning or jargon.meaning.strip() == "":
            continue

        results.append({"content": jargon.content or "", "meaning": jargon.meaning or ""})

        # 达到限制数量后停止
        if len(results) >= limit:
            break

    return results
