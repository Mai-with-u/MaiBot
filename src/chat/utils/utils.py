from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Tuple

import asyncio
import random
import re
import time

from src.chat.message_receive.chat_manager import chat_manager as _chat_manager
from src.chat.message_receive.message import SessionMessage
from src.common.data_models.message_component_data_model import AtComponent
from src.common.logger import get_logger
from src.config.config import global_config
from src.person_info.person_info import Person
from src.services.bot_account_service import get_bot_accounts
from src.services.embedding_service import EmbeddingServiceClient

from .llm_sentence_splitter import split_text_with_llm

if TYPE_CHECKING:
    from src.common.data_models.chat_target_info_data_model import ChatTargetInfo

logger = get_logger("chat_utils")
_warned_unconfigured_platforms: set[str] = set()


@dataclass(frozen=True)
class ProcessedResponseSegment:
    """回复后处理产生的单条消息及其发送提示。"""

    text: str
    quote_previous: bool = False
    # 原始文本中紧跟本段的分隔符；多条消息被压缩合并为一条时，用它恢复句间的停顿
    separator: str = ""
    # 纠正内容始终独占一条消息，不依赖是否启用引用。
    is_correction: bool = False


def is_english_letter(char: str) -> bool:
    """检查字符是否为英文字母（忽略大小写）"""
    return "a" <= char.lower() <= "z"


def parse_platform_accounts(platforms: list[str]) -> dict[str, str]:
    """解析 platforms 列表，返回平台到账号的映射

    Args:
        platforms: 格式为 ["platform:account"] 的列表，如 ["tg:123456789", "wx:wxid123"]

    Returns:
        字典，键为平台名，值为账号
    """
    result: dict[str, str] = {}
    for platform_entry in platforms:
        if ":" in platform_entry:
            platform_name, account = platform_entry.split(":", 1)
            normalized_platform = platform_name.lower().strip()
            account_str = account.strip()
            if normalized_platform and account_str:
                result[normalized_platform] = account_str
    return result


def _get_configured_qq_account() -> str:
    qq_account = global_config.bot.qq_account.strip()
    if qq_account in {"", "0"}:
        return ""
    return qq_account


def get_bot_account(platform: str, preferred_account_id: Optional[str] = None) -> str:
    """解析单个 Bot 账号；多账号且无会话归属时拒绝猜测。"""

    preferred = str(preferred_account_id or "").strip()
    if preferred:
        return preferred
    accounts = get_bot_accounts(platform)
    if len(accounts) == 1:
        return next(iter(accounts))
    if len(accounts) > 1:
        logger.error(f"平台 {platform} 存在多个 Bot 账号，缺少聊天流 account_id，无法选择发送身份")
    return ""


def get_configured_bot_accounts() -> dict[str, str]:
    """读取 legacy 发送链使用的备用配置账号，不包含数据库身份。"""

    bot_accounts: dict[str, str] = {}
    qq_account = _get_configured_qq_account()
    if qq_account:
        primary_platform = str(global_config.bot.platform or "qq").strip().lower()
        bot_accounts[primary_platform] = qq_account

    platforms_list = global_config.bot.platforms
    platform_accounts = parse_platform_accounts(platforms_list)

    for platform_name, account in platform_accounts.items():
        if platform_name in {"qq", "webui", "internal"}:
            continue
        bot_accounts[platform_name] = account

    return bot_accounts


def is_bot_self(platform: str, user_id: str) -> bool:
    """判断给定的平台和用户ID是否是机器人自己

    这个函数统一处理所有平台（包括 QQ、Telegram、WebUI 等）的机器人识别逻辑。

    Args:
        platform: 消息平台（如 "qq", "telegram", "webui" 等）
        user_id: 用户ID

    Returns:
        bool: 如果是机器人自己则返回 True，否则返回 False
    """
    normalized_platform = str(platform or "").strip().lower()
    if not normalized_platform or not user_id:
        return False

    # 将 user_id 转为字符串进行比较
    user_id_str = str(user_id).strip()
    if not user_id_str:
        return False

    bot_accounts = get_bot_accounts(normalized_platform)
    if bot_accounts:
        return user_id_str in bot_accounts

    if normalized_platform not in _warned_unconfigured_platforms:
        _warned_unconfigured_platforms.add(normalized_platform)
        logger.warning(f"平台 {normalized_platform} 未配置机器人账号，无法判断用户 {user_id_str} 是否为机器人自己")
    return False


def _has_at_component_targeting_bot(message: SessionMessage, platform: str) -> bool:
    """检查消息中的结构化 @ 组件是否直接指向当前 bot。"""

    raw_message = getattr(message, "raw_message", None)
    for component in getattr(raw_message, "components", []) or []:
        if isinstance(component, AtComponent) and is_bot_self(platform, component.target_user_id):
            return True
    return False


def is_mentioned_bot_in_message(message: SessionMessage, *, is_group_chat: bool = True) -> Tuple[bool, bool, float]:
    """兼容统一多平台入口，识别行为由对应聊天场景负责。"""
    from src.maisaka.scenes.factory import get_scene

    return get_scene(is_group_chat).detect_mentions(message)


async def get_embedding(text: str, request_type: str = "embedding") -> Optional[List[float]]:
    """获取文本的嵌入向量。

    Args:
        text: 待编码的文本内容。
        request_type: 当前请求的业务类型标识。

    Returns:
        Optional[List[float]]: 成功时返回嵌入向量，失败时返回 `None`。
    """
    embedding_client = EmbeddingServiceClient(task_name="embedding", request_type=request_type)
    try:
        embedding_result = await embedding_client.embed_text(text)
        embedding = embedding_result.embedding
    except Exception as e:
        logger.error(f"获取embedding失败: {str(e)}")
        embedding = None
    return embedding

def _split_into_sentence_segments(text: str) -> list[tuple[str, str]]:
    """将文本分割成 (句子, 尾随分隔符) 元组，并根据概率合并
    1. 识别分割点（, ， 。 ; 空格），但如果分割点左右都是英文字母则不分割。
    2. 将文本分割成 (内容, 分隔符) 的元组。
    3. 根据原始文本长度计算合并概率，概率性地合并相邻段落。
    注意：此函数假定颜文字已在上层被保护。
    Args:
        text: 要分割的文本字符串 (假定颜文字已被保护)
    Returns:
        List[Tuple[str, str]]: 分割和合并后的 (句子, 尾随分隔符) 列表，
        尾随分隔符是原始文本中紧跟该句的分隔符（最后一句为空字符串），
        供后续把多句压缩回一条消息时恢复句间停顿。
    """
    # 预处理：处理多余的换行符
    # 1. 将连续的换行符替换为单个换行符（保留换行符用于分割）
    text = re.sub(r"\n\s*\n+", "\n", text)
    # 2. 处理换行符和其他分隔符的组合（保留换行符，删除其他分隔符）
    text = re.sub(r"\n\s*([，,。;\s])", r"\n\1", text)
    text = re.sub(r"([，,。;\s])\s*\n", r"\1\n", text)

    # 处理两个汉字中间的换行符（保留换行符，不替换为句号，让换行符强制分割）
    # text = re.sub(r"([\u4e00-\u9fff])\n([\u4e00-\u9fff])", r"\1。\2", text)  # 注释掉，保留换行符用于分割

    len_text = len(text)
    if len_text < 3:
        if random.random() < 0.01:
            return [(char, "") for char in text]
        return [(text, "")]

    # 先标记哪些位置位于成对引号内部，避免在引号内部进行句子分割
    # 支持的引号包括：中英文单/双引号和常见中文书名号/引号
    quote_chars = {
        '"',
        "'",
        "“",
        "”",
        "‘",
        "’",
        "「",
        "」",
        "『",
        "』",
    }
    inside_quote = [False] * len_text
    in_quote = False
    current_quote_char = ""
    for idx, ch in enumerate(text):
        if ch in quote_chars:
            # 遇到引号时切换状态（英文引号本身开闭相同，用同一个字符表示）
            if not in_quote:
                in_quote = True
                current_quote_char = ch
                inside_quote[idx] = False
            else:
                # 只有遇到同一类引号才视为关闭
                if ch == current_quote_char or ch in {'"', "'"} and current_quote_char in {'"', "'"}:
                    in_quote = False
                    current_quote_char = ""
                inside_quote[idx] = False
        else:
            inside_quote[idx] = in_quote

    # 定义分隔符（包含换行符）
    separators = {"，", ",", " ", "。", ";", "\n"}
    segments = []
    current_segment = ""

    # 1. 分割成 (内容, 分隔符) 元组
    i = 0
    while i < len(text):
        char = text[i]
        if char in separators:
            # 引号内部一律不作为分割点（包括换行）
            if inside_quote[i]:
                can_split = False
            else:
                # 换行符在不在引号内时都强制分割
                if char == "\n":
                    can_split = True
                else:
                    # 检查分割条件
                    can_split = True
                    # 检查分隔符左右是否有冒号（中英文），如果有则不分割
                    if i > 0:
                        prev_char = text[i - 1]
                        if prev_char in {":", "："}:
                            can_split = False
                    if i < len(text) - 1:
                        next_char = text[i + 1]
                        if next_char in {":", "："}:
                            can_split = False

                    # 如果左右没有冒号，再检查空格的特殊情况
                    if can_split and char == " " and i > 0 and i < len(text) - 1:
                        prev_char = text[i - 1]
                        next_char = text[i + 1]
                        dash_chars = {"-", "—"}
                        if prev_char in dash_chars or next_char in dash_chars:
                            can_split = False
                        else:
                            # 不分割数字和数字、数字和英文、英文和数字、英文和英文之间的空格
                            prev_is_alnum = prev_char.isdigit() or is_english_letter(prev_char)
                            next_is_alnum = next_char.isdigit() or is_english_letter(next_char)
                            if prev_is_alnum and next_is_alnum:
                                can_split = False

            if can_split:
                # 只有当当前段不为空时才添加
                if current_segment:
                    segments.append((current_segment, char))
                # 如果当前段为空，但分隔符是空格或换行符，则也添加一个空段（保留分隔符）
                elif char in {" ", "\n"}:
                    segments.append(("", char))
                current_segment = ""
            else:
                # 不分割，将分隔符加入当前段
                current_segment += char
        else:
            current_segment += char
        i += 1

    # 添加最后一个段（没有后续分隔符）
    if current_segment:
        segments.append((current_segment, ""))

    # 过滤掉完全空的段（内容和分隔符都为空）
    segments = [(content, sep) for content, sep in segments if content or sep]

    # 如果分割后为空（例如，输入全是分隔符且不满足保留条件），恢复颜文字并返回
    # 如果原始文本非空，则返回原始文本（可能只包含未被分割的字符或颜文字占位符）
    if not segments:
        return [(text, "")] if text else []

    # 2. 概率合并
    if len_text < 12:
        split_strength = 0.2
    elif len_text < 32:
        split_strength = 0.6
    else:
        split_strength = 0.7
    # 合并概率与分割强度相反
    merge_probability = 1.0 - split_strength

    merged_segments = []
    idx = 0
    while idx < len(segments):
        current_content, current_sep = segments[idx]

        # 检查是否可以与下一段合并
        # 条件：不是最后一段，且随机数小于合并概率，且当前段有内容（避免合并空段）
        if idx + 1 < len(segments) and current_content and current_sep != "\n" and random.random() < merge_probability:
            next_content, next_sep = segments[idx + 1]
            # 合并: (内容1 + 分隔符1 + 内容2, 分隔符2)
            # 只有当下一段也有内容时才合并文本，否则只传递分隔符
            if next_content:
                merged_content = current_content + current_sep + next_content
                merged_segments.append((merged_content, next_sep))
            else:  # 下一段内容为空，只保留当前内容和下一段的分隔符
                merged_segments.append((current_content, next_sep))

            idx += 2  # 跳过下一段，因为它已被合并
        else:
            # 不合并，直接添加当前段
            merged_segments.append((current_content, current_sep))
            idx += 1

    # 提取最终的句子内容（保留尾随分隔符，供压缩拼接时恢复停顿）
    sentence_segments = [(content, sep) for content, sep in merged_segments if content]  # 只保留有内容的段

    # 清理可能引入的空字符串和仅包含空白的字符串
    sentence_segments = [
        (sentence, sep) for sentence, sep in sentence_segments if sentence.strip()
    ]  # 过滤掉空字符串以及仅包含空白（如换行符、空格）的字符串
    sentence_segments = [
        (normalized_sentence, sep)
        for sentence, sep in sentence_segments
        if (normalized_sentence := re.sub(r"[^\S\r\n]*[\r\n]+[^\S\r\n]*", " ", sentence).strip())
    ]

    logger.debug(f"分割并合并后的句子: {[sentence for sentence, _sep in sentence_segments]}")
    return sentence_segments


def split_into_sentences_w_remove_punctuation(text: str) -> list[str]:
    """将文本分割成句子，并根据概率合并（不保留分隔符信息，仅返回句子内容）"""
    return [sentence for sentence, _separator in _split_into_sentence_segments(text)]


def _merge_processed_segments_to_max_count(
    segments: List[ProcessedResponseSegment],
    max_count: int,
) -> List[ProcessedResponseSegment]:
    """压缩回复段数量；纠正内容独占消息所需的边界优先于条数上限。"""

    if len(segments) <= max_count:
        return segments
    if max_count <= 0:
        return []

    segment_count = len(segments)
    group_starts = {0}
    for index, segment in enumerate(segments):
        if segment.is_correction or segment.quote_previous:
            # 同时保留纠正段前后的边界，避免它吞入后续正文；上限不足时也不能舍弃。
            group_starts.add(index)
            if index + 1 < segment_count:
                group_starts.add(index + 1)

    # 在保留纠正消息边界后，沿用原来的均匀分组策略填充其余可用分组。
    evenly_spaced_starts: List[int] = []
    start_index = 0
    for group_index in range(max_count):
        remaining_segments = segment_count - start_index
        remaining_groups = max_count - group_index
        group_size = (remaining_segments + remaining_groups - 1) // remaining_groups
        evenly_spaced_starts.append(start_index)
        start_index += group_size

    for candidate_start in evenly_spaced_starts:
        if len(group_starts) >= max_count:
            break
        group_starts.add(candidate_start)

    sorted_starts = sorted(group_starts)
    merged_segments: List[ProcessedResponseSegment] = []
    for group_index, group_start in enumerate(sorted_starts):
        group_end = sorted_starts[group_index + 1] if group_index + 1 < len(sorted_starts) else segment_count
        group = segments[group_start:group_end]
        # 组内多段被压缩为一条消息时，补回各段尾随的分隔符，避免多句硬拼接成无标点的一整坨
        merged_text_parts: List[str] = []
        for position, segment in enumerate(group):
            merged_text_parts.append(segment.text)
            if position < len(group) - 1:
                merged_text_parts.append(segment.separator)
        merged_segments.append(
            ProcessedResponseSegment(
                text="".join(merged_text_parts),
                quote_previous=group[0].quote_previous,
                separator=group[-1].separator,
                is_correction=group[0].is_correction,
            )
        )

    return merged_segments


def _get_random_default_reply() -> str:
    """获取随机默认回复"""
    default_replies = [
        f"{global_config.bot.nickname}不知道哦",
        f"{global_config.bot.nickname}不知道",
        "不知道哦",
        "不知道",
        "不晓得",
        "懒得说",
        "()",
    ]
    return random.choice(default_replies)


@dataclass(frozen=True)
class _PreparedResponse:
    segments: List[ProcessedResponseSegment]
    overflow_text: Optional[str] = None
    kaomoji_mapping: Optional[Dict[str, str]] = None


def _prepare_rule_response(text: str, enable_splitter: bool) -> _PreparedResponse:
    if not global_config.response_post_process.enable_response_post_process:
        return _PreparedResponse([ProcessedResponseSegment(text)])

    # 先保护颜文字
    if global_config.response_splitter.enable_kaomoji_protection:
        protected_text, kaomoji_mapping = protect_kaomoji(text)
        logger.debug(f"保护颜文字后的文本: {protected_text}")
    else:
        protected_text = text
        kaomoji_mapping = {}
    # 提取被 () 或 [] 或 （）包裹且包含中文的内容
    pattern = re.compile(r"[(\[（](?=.*[一-鿿]).*?[)\]）]")
    _extracted_contents = pattern.findall(protected_text)  # 在保护后的文本上查找
    # 去除 () 和 [] 及其包裹的内容
    cleaned_text = pattern.sub("", protected_text)

    if cleaned_text == "":
        return _PreparedResponse([ProcessedResponseSegment("呃呃")])

    logger.debug(f"{text}去除括号处理后的文本: {cleaned_text}")

    # 对清理后的文本进行进一步处理
    max_length = global_config.response_splitter.max_length * 2
    # 如果基本上是中文，则进行长度过滤
    if get_western_ratio(cleaned_text) < 0.1 and len(cleaned_text) > max_length:
        logger.warning(f"回复过长 ({len(cleaned_text)} 字符)，返回默认回复")
        return _PreparedResponse([ProcessedResponseSegment(_get_random_default_reply())])

    if global_config.response_splitter.enable and enable_splitter:
        split_sentences = _split_into_sentence_segments(cleaned_text)
    else:
        split_sentences = [(cleaned_text, "")]

    return _PreparedResponse(
        [ProcessedResponseSegment(sentence, separator=separator) for sentence, separator in split_sentences],
        overflow_text=cleaned_text,
        kaomoji_mapping=kaomoji_mapping,
    )


def _finish_response(
    prepared: _PreparedResponse, segments: List[ProcessedResponseSegment]
) -> List[ProcessedResponseSegment]:
    if prepared.overflow_text is None:
        return segments
    splitter = global_config.response_splitter
    if len(segments) > splitter.max_sentence_num:
        if not splitter.enable_overflow_return_all:
            logger.warning("分割后消息数量过多 (%s 条)，返回默认回复", len(segments))
            return [ProcessedResponseSegment(_get_random_default_reply())]
        logger.warning("分割后消息数量过多 (%s 条)，直接返回原文", len(segments))
        segments = [ProcessedResponseSegment(prepared.overflow_text)]

    segments = _merge_processed_segments_to_max_count(segments, splitter.max_split_num)

    # 保留尾随分隔符和纠正标记，只恢复各段中的颜文字占位符。
    if prepared.kaomoji_mapping:
        recovered_sentences = recover_kaomoji([segment.text for segment in segments], prepared.kaomoji_mapping)
        segments = [
            ProcessedResponseSegment(
                text=recovered_text,
                quote_previous=segment.quote_previous,
                separator=segment.separator,
                is_correction=segment.is_correction,
            )
            for segment, recovered_text in zip(segments, recovered_sentences, strict=True)
        ]
    return segments


def process_llm_response_segments(text: str, enable_splitter: bool = True) -> List[ProcessedResponseSegment]:
    """同步处理文本分段及压缩。"""
    prepared = _prepare_rule_response(text, enable_splitter)
    return _finish_response(prepared, prepared.segments)


async def process_llm_response_segments_async(
    text: str,
    enable_splitter: bool = True,
    *,
    transform_segments: Optional[
        Callable[[List[ProcessedResponseSegment]], Awaitable[List[ProcessedResponseSegment]]]
    ] = None,
) -> List[ProcessedResponseSegment]:
    """分段后允许异步处理器修改回复，再执行条数限制和压缩。"""
    splitter = global_config.response_splitter
    if (
        splitter.mode == "llm"
        and global_config.response_post_process.enable_response_post_process
        and splitter.enable
        and enable_splitter
    ):
        try:
            split_sentences = await split_text_with_llm(text)
        except Exception as exc:
            logger.warning(f"LLM 断句失败，本次回复不断句：{exc}")
            split_sentences = [(text, "")]
        prepared = _PreparedResponse(
            [ProcessedResponseSegment(sentence, separator=separator) for sentence, separator in split_sentences],
            overflow_text=text,
        )
    else:
        prepared = await asyncio.to_thread(_prepare_rule_response, text, enable_splitter)

    segments = prepared.segments
    if transform_segments is not None and prepared.overflow_text is not None and segments:
        segments = await transform_segments(segments)
    return await asyncio.to_thread(_finish_response, prepared, segments)


def process_llm_response(text: str, enable_splitter: bool = True) -> List[str]:
    """处理回复文本，并返回兼容旧调用链的纯文本列表。"""
    return [segment.text for segment in process_llm_response_segments(text, enable_splitter)]


def calculate_typing_time(
    input_string: str,
    # thinking_start_time: float,
    chinese_time: float = 0.3,
    english_time: float = 0.15,
    is_emoji: bool = False,
    is_image: bool = False,
) -> float:
    """
    计算输入字符串所需的时间，中文和英文字符有不同的输入时间
        input_string (str): 输入的字符串
        chinese_time (float): 中文字符的输入时间，默认为0.2秒
        english_time (float): 英文字符的输入时间，默认为0.1秒
        is_emoji (bool): 是否为emoji，默认为False
        is_image (bool): 是否为纯图片消息，默认为False

    特殊情况：
    - 如果只有一个中文字符，将使用3倍的中文输入时间
    - 在所有输入结束后，额外加上回车时间0.3秒
    - 如果is_emoji或is_image为True，将使用固定1秒的输入时间，再应用打字速度配置
    """
    # chinese_time *= 1 / typing_speed_multiplier
    # english_time *= 1 / typing_speed_multiplier
    # 计算中文字符数
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in input_string)

    # 如果只有一个中文字符，使用3倍时间
    if not (is_emoji or is_image) and chinese_chars == 1 and len(input_string.strip()) == 1:
        return chinese_time * 3 + 0.3  # 加上回车时间

    # 正常计算所有字符的输入时间
    total_time = 0.0
    for char in input_string:
        total_time += chinese_time if "\u4e00" <= char <= "\u9fff" else english_time
    if is_emoji or is_image:
        total_time = 1

    typing_speed = global_config.response_post_process.typing_speed
    if typing_speed <= 0:
        return 0
    total_time *= typing_speed

    # if time.time() - thinking_start_time > 10:
    #     total_time = 1

    # print(f"thinking_start_time:{thinking_start_time}")
    # print(f"nowtime:{time.time()}")
    # print(f"nowtime - thinking_start_time:{time.time() - thinking_start_time}")
    # print(f"{total_time}")

    return total_time  # 加上回车时间


def truncate_message(message: str, max_length=20) -> str:
    """截断消息，使其不超过指定长度"""
    return f"{message[:max_length]}..." if len(message) > max_length else message


def protect_kaomoji(sentence):
    """ "
    识别并保护句子中的颜文字（含括号与无括号），将其替换为占位符，
    并返回替换后的句子和占位符到颜文字的映射表。
    Args:
        sentence (str): 输入的原始句子
    Returns:
        tuple: (处理后的句子, {占位符: 颜文字})
    """
    kaomoji_pattern = re.compile(
        r"("
        r"[(\[（【]"  # 左括号
        r"[^()\[\]（）【】]*?"  # 非括号字符（惰性匹配）
        r"[^一-龥a-zA-Z0-9\s]"  # 非中文、非英文、非数字、非空格字符（必须包含至少一个）
        r"[^()\[\]（）【】]*?"  # 非括号字符（惰性匹配）
        r"[)\]）】"  # 右括号
        r"]"
        r")"
        r"|"
        r"([▼▽・ᴥω･﹏^><≧≦￣｀´∀ヮДд︿﹀へ｡ﾟ╥╯╰︶︹•⁄]{2,15})"
    )

    kaomoji_matches = kaomoji_pattern.findall(sentence)
    placeholder_to_kaomoji = {}

    for match in kaomoji_matches:
        kaomoji = match[0] or match[1]
        if kaomoji.startswith("[表情包") and kaomoji.endswith("]"):
            continue
        idx = len(placeholder_to_kaomoji)
        placeholder = f"__KAOMOJI_{idx}__"
        sentence = sentence.replace(kaomoji, placeholder, 1)
        placeholder_to_kaomoji[placeholder] = kaomoji

    return sentence, placeholder_to_kaomoji


def recover_kaomoji(sentences, placeholder_to_kaomoji):
    """
    根据映射表恢复句子中的颜文字。
    Args:
        sentences (list): 含有占位符的句子列表
        placeholder_to_kaomoji (dict): 占位符到颜文字的映射表
    Returns:
        list: 恢复颜文字后的句子列表
    """
    recovered_sentences = []
    for sentence in sentences:
        for placeholder, kaomoji in placeholder_to_kaomoji.items():
            sentence = sentence.replace(placeholder, kaomoji)
        recovered_sentences.append(sentence)
    return recovered_sentences


def get_western_ratio(paragraph):
    """计算段落中字母数字字符的西文比例
    原理：检查段落中字母数字字符的西文比例
    通过is_english_letter函数判断每个字符是否为西文
    只检查字母数字字符，忽略标点符号和空格等非字母数字字符

    Args:
        paragraph: 要检查的文本段落

    Returns:
        float: 西文字符比例(0.0-1.0)，如果没有字母数字字符则返回0.0
    """
    alnum_chars = [char for char in paragraph if char.isalnum()]
    if not alnum_chars:
        return 0.0

    western_count = sum(bool(is_english_letter(char)) for char in alnum_chars)
    return western_count / len(alnum_chars)


def translate_timestamp_to_human_readable(timestamp: float, mode: str = "normal") -> str:
    # sourcery skip: merge-comparisons, merge-duplicate-blocks, switch
    """将时间戳转换为人类可读的时间格式

    Args:
        timestamp: 时间戳
        mode: 转换模式，"normal"为标准格式，"relative"为相对时间格式

    Returns:
        str: 格式化后的时间字符串
    """
    if mode == "normal":
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    elif mode == "normal_no_YMD":
        return time.strftime("%H:%M:%S", time.localtime(timestamp))
    elif mode == "relative":
        now = time.time()
        diff = now - timestamp

        if diff < 20:
            return "刚刚"
        elif diff < 60:
            return f"{int(diff)}秒前"
        elif diff < 3600:
            return f"{int(diff / 60)}分钟前"
        elif diff < 86400:
            return f"{int(diff / 3600)}小时前"
        elif diff < 86400 * 2:
            return f"{int(diff / 86400)}天前"
        else:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)) + ":"
    else:  # mode = "lite" or unknown
        # 只返回时分秒格式
        return time.strftime("%H:%M:%S", time.localtime(timestamp))


def get_chat_type_and_target_info(chat_id: str) -> Tuple[bool, Optional["ChatTargetInfo"]]:
    """
    获取聊天类型（是否群聊）和私聊对象信息。

    Args:
        chat_id: 聊天流ID

    Returns:
        Tuple[bool, Optional[Dict]]:
            - bool: 是否为群聊 (True 是群聊, False 是私聊或未知)
            - Optional[Dict]: 如果是私聊，包含对方信息的字典；否则为 None。
            字典包含: platform, user_id, user_nickname, person_id, person_name
    """
    is_group_chat = False  # Default to private/unknown
    chat_target_info = None

    try:
        if chat_stream := _chat_manager.get_session_by_session_id(chat_id):
            if chat_stream.is_group_session:
                is_group_chat = True
                chat_target_info = None  # Explicitly None for group chat
            elif chat_stream.user_id:  # It's a private chat
                is_group_chat = False
                platform: str = chat_stream.platform
                user_id: str = chat_stream.user_id

                # Try to get nickname from context
                user_nickname = None
                if (
                    chat_stream.context
                    and chat_stream.context.message
                    and chat_stream.context.message.message_info.user_info
                ):
                    user_nickname = chat_stream.context.message.message_info.user_info.user_nickname

                from src.common.data_models.chat_target_info_data_model import ChatTargetInfo  # 解决循环导入问题

                # Initialize target_info with basic info
                target_info = ChatTargetInfo(
                    platform=platform,
                    user_id=user_id,
                    session_nickname=user_nickname or "",
                    person_id=None,
                    person_name=None,
                )

                # Try to fetch person info
                try:
                    person = Person(platform=platform, user_id=user_id)
                    if not person.is_known:
                        logger.warning(f"用户 {user_nickname} 尚未认识")
                        # 如果用户尚未认识，则返回False和None
                        return False, None
                    target_info.is_known = True
                    if person.person_id:
                        target_info.person_id = person.person_id
                        target_info.person_name = person.person_name
                except Exception as person_e:
                    logger.warning(
                        f"获取 person_id 或 person_name 时出错 for {platform}:{user_id} in utils: {person_e}"
                    )

                chat_target_info = target_info
        else:
            logger.warning(f"无法获取 chat_stream for {chat_id} in utils")
    except Exception as e:
        logger.error(f"获取聊天类型和目标信息时出错 for {chat_id}: {e}", exc_info=True)

    return is_group_chat, chat_target_info
