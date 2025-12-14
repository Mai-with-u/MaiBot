import random
import re
import time
import jieba
import json
import ast
import os
from datetime import datetime

from typing import Optional, Tuple, List, TYPE_CHECKING

from src.common.logger import get_logger
from src.common.data_models.database_data_model import DatabaseMessages
from src.config.config import global_config, model_config
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.chat_stream import get_chat_manager
from src.llm_models.utils_model import LLMRequest
from src.person_info.person_info import Person
from .typo_generator import ChineseTypoGenerator

if TYPE_CHECKING:
    from src.common.data_models.info_data_model import TargetPersonInfo

logger = get_logger("chat_utils")


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
    result = {}
    for platform_entry in platforms:
        if ":" in platform_entry:
            platform_name, account = platform_entry.split(":", 1)
            result[platform_name.strip()] = account.strip()
    return result


def get_current_platform_account(platform: str, platform_accounts: dict[str, str], qq_account: str) -> str:
    """根据当前平台获取对应的账号

    Args:
        platform: 当前消息的平台
        platform_accounts: 从 platforms 列表解析的平台账号映射
        qq_account: QQ 账号（兼容旧配置）

    Returns:
        当前平台对应的账号
    """
    if platform == "qq":
        return qq_account
    elif platform == "telegram":
        # 优先使用 tg，其次使用 telegram
        return platform_accounts.get("tg", "") or platform_accounts.get("telegram", "")
    else:
        # 其他平台直接使用平台名作为键
        return platform_accounts.get(platform, "")


def is_mentioned_bot_in_message(message: MessageRecv) -> tuple[bool, bool, float]:
    """检查消息是否提到了机器人（统一多平台实现）"""
    text = message.processed_plain_text or ""
    platform = getattr(message.message_info, "platform", "") or ""

    # 获取各平台账号
    platforms_list = getattr(global_config.bot, "platforms", []) or []
    platform_accounts = parse_platform_accounts(platforms_list)
    qq_account = str(getattr(global_config.bot, "qq_account", "") or "")

    # 获取当前平台对应的账号
    current_account = get_current_platform_account(platform, platform_accounts, qq_account)

    nickname = str(global_config.bot.nickname or "")
    alias_names = list(getattr(global_config.bot, "alias_names", []) or [])
    keywords = [nickname] + alias_names

    reply_probability = 0.0
    is_at = False
    is_mentioned = False

    # 1) 直接的 additional_config 标记
    add_cfg = getattr(message.message_info, "additional_config", None) or {}
    if isinstance(add_cfg, dict):
        if add_cfg.get("at_bot") or add_cfg.get("is_mentioned"):
            is_mentioned = True
            # 当提供数值型 is_mentioned 时，当作概率提升
            try:
                if add_cfg.get("is_mentioned") not in (None, ""):
                    reply_probability = float(add_cfg.get("is_mentioned"))  # type: ignore
            except Exception:
                pass

    # 2) 已经在上游设置过的 message.is_mentioned
    if getattr(message, "is_mentioned", False):
        is_mentioned = True

    # 3) 扫描分段：是否包含 mention_bot（适配器插入）
    def _has_mention_bot(seg) -> bool:
        try:
            if seg is None:
                return False
            if getattr(seg, "type", None) == "mention_bot":
                return True
            if getattr(seg, "type", None) == "seglist":
                for s in getattr(seg, "data", []) or []:
                    if _has_mention_bot(s):
                        return True
            return False
        except Exception:
            return False

    if _has_mention_bot(getattr(message, "message_segment", None)):
        is_at = True
        is_mentioned = True

    # 4) 统一的 @ 检测逻辑
    if current_account and not is_at and not is_mentioned:
        if platform == "qq":
            # QQ 格式: @<name:qq_id>
            if re.search(rf"@<(.+?):{re.escape(current_account)}>", text):
                is_at = True
                is_mentioned = True
        else:
            # 其他平台格式: @username 或 @account
            if re.search(rf"@{re.escape(current_account)}(\b|$)", text, flags=re.IGNORECASE):
                is_at = True
                is_mentioned = True

    # 5) 统一的回复检测逻辑
    if not is_mentioned:
        # 通用回复格式：包含 "(你)" 或 "（你）"
        if re.search(r"\[回复 .*?\(你\)：", text) or re.search(r"\[回复 .*?（你）：", text):
            is_mentioned = True
        # ID 形式的回复检测
        elif current_account:
            if re.search(rf"\[回复 (.+?)\({re.escape(current_account)}\)：(.+?)\]，说：", text):
                is_mentioned = True
            elif re.search(
                rf"\[回复<(.+?)(?=:{re.escape(current_account)}>)\:{re.escape(current_account)}>：(.+?)\]，说：", text
            ):
                is_mentioned = True

    # 6) 名称/别名 提及（去除 @/回复标记后再匹配）
    if not is_mentioned and keywords:
        msg_content = text
        # 去除各种 @ 与 回复标记，避免误判
        msg_content = re.sub(r"@(.+?)（(\d+)）", "", msg_content)
        msg_content = re.sub(r"@<(.+?)(?=:(\d+))\:(\d+)>", "", msg_content)
        msg_content = re.sub(r"\[回复 (.+?)\(((\d+)|未知id|你)\)：(.+?)\]，说：", "", msg_content)
        msg_content = re.sub(r"\[回复<(.+?)(?=:(\d+))\:(\d+)>：(.+?)\]，说：", "", msg_content)
        for kw in keywords:
            if kw and kw in msg_content:
                is_mentioned = True
                break

    # 7) 概率设置
    if is_at and getattr(global_config.chat, "at_bot_inevitable_reply", 1):
        reply_probability = 1.0
        logger.debug("被@，回复概率设置为100%")
    elif is_mentioned and getattr(global_config.chat, "mentioned_bot_reply", 1):
        reply_probability = max(reply_probability, 1.0)
        logger.debug("被提及，回复概率设置为100%")

    return is_mentioned, is_at, reply_probability


async def get_embedding(text, request_type="embedding") -> Optional[List[float]]:
    """获取文本的embedding向量"""
    # 每次都创建新的LLMRequest实例以避免事件循环冲突
    llm = LLMRequest(model_set=model_config.model_task_config.embedding, request_type=request_type)
    try:
        embedding, _ = await llm.get_embedding(text)
    except Exception as e:
        logger.error(f"获取embedding失败: {str(e)}")
        embedding = None
    return embedding


def split_into_sentences_w_remove_punctuation(text: str) -> list[str]:
    """将文本分割成句子，并根据概率合并
    1. 识别分割点（, ， 。 ; 空格），但如果分割点左右都是英文字母则不分割。
    2. 将文本分割成 (内容, 分隔符) 的元组。
    3. 根据原始文本长度计算合并概率，概率性地合并相邻段落。
    注意：此函数假定颜文字已在上层被保护。
    Args:
        text: 要分割的文本字符串 (假定颜文字已被保护)
    Returns:
        List[str]: 分割和合并后的句子列表
    """
    # 预处理：处理多余的换行符
    # 1. 将连续的换行符替换为单个换行符
    text = re.sub(r"\n\s*\n+", "\n", text)
    # 2. 处理换行符和其他分隔符的组合
    text = re.sub(r"\n\s*([，,。;\s])", r"\1", text)
    text = re.sub(r"([，,。;\s])\s*\n", r"\1", text)

    # 处理两个汉字中间的换行符
    text = re.sub(r"([\u4e00-\u9fff])\n([\u4e00-\u9fff])", r"\1。\2", text)

    len_text = len(text)
    if len_text < 3:
        return list(text) if random.random() < 0.01 else [text]

    # 定义分隔符
    separators = {"，", ",", " ", "。", ";"}
    segments = []
    current_segment = ""

    # 1. 分割成 (内容, 分隔符) 元组
    i = 0
    while i < len(text):
        char = text[i]
        if char in separators:
            # 检查分割条件：如果空格左右都是英文字母、数字，或数字和英文之间，则不分割（仅对空格应用此规则）
            can_split = True
            if 0 < i < len(text) - 1:
                prev_char = text[i - 1]
                next_char = text[i + 1]
                # 只对空格应用"不分割数字和数字、数字和英文、英文和数字、英文和英文之间的空格"规则
                if char == " ":
                    prev_is_alnum = prev_char.isdigit() or is_english_letter(prev_char)
                    next_is_alnum = next_char.isdigit() or is_english_letter(next_char)
                    if prev_is_alnum and next_is_alnum:
                        can_split = False

            if can_split:
                # 只有当当前段不为空时才添加
                if current_segment:
                    segments.append((current_segment, char))
                # 如果当前段为空，但分隔符是空格，则也添加一个空段（保留空格）
                elif char == " ":
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
    if not segments:
        return [text] if text else []  # 如果原始文本非空，则返回原始文本（可能只包含未被分割的字符或颜文字占位符）

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
        if idx + 1 < len(segments) and random.random() < merge_probability and current_content:
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

    # 提取最终的句子内容
    final_sentences = [content for content, sep in merged_segments if content]  # 只保留有内容的段

    # 清理可能引入的空字符串和仅包含空白的字符串
    final_sentences = [
        s for s in final_sentences if s.strip()
    ]  # 过滤掉空字符串以及仅包含空白（如换行符、空格）的字符串

    logger.debug(f"分割并合并后的句子: {final_sentences}")
    return final_sentences


def random_remove_punctuation(text: str) -> str:
    """随机处理标点符号，模拟人类打字习惯

    Args:
        text: 要处理的文本

    Returns:
        str: 处理后的文本
    """
    result = ""
    text_len = len(text)

    for i, char in enumerate(text):
        if char == "。" and i == text_len - 1:  # 结尾的句号
            if random.random() > 0.1:  # 90%概率删除结尾句号
                continue
        elif char == "，":
            rand = random.random()
            if rand < 0.05:  # 5%概率删除逗号
                continue
            elif rand < 0.25:  # 20%概率把逗号变成空格
                result += " "
                continue
        result += char
    return result


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


def process_llm_response(text: str, enable_splitter: bool = True, enable_chinese_typo: bool = True) -> list[str]:
    if not global_config.response_post_process.enable_response_post_process:
        return [text]

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
        return ["呃呃"]

    logger.debug(f"{text}去除括号处理后的文本: {cleaned_text}")

    # 对清理后的文本进行进一步处理
    max_length = global_config.response_splitter.max_length * 2
    max_sentence_num = global_config.response_splitter.max_sentence_num
    # 如果基本上是中文，则进行长度过滤
    if get_western_ratio(cleaned_text) < 0.1 and len(cleaned_text) > max_length:
        logger.warning(f"回复过长 ({len(cleaned_text)} 字符)，返回默认回复")
        return [_get_random_default_reply()]

    typo_generator = ChineseTypoGenerator(
        error_rate=global_config.chinese_typo.error_rate,
        min_freq=global_config.chinese_typo.min_freq,
        tone_error_rate=global_config.chinese_typo.tone_error_rate,
        word_replace_rate=global_config.chinese_typo.word_replace_rate,
    )

    if global_config.response_splitter.enable and enable_splitter:
        split_sentences = split_into_sentences_w_remove_punctuation(cleaned_text)
    else:
        split_sentences = [cleaned_text]

    sentences: List[str] = []
    for sentence in split_sentences:
        if global_config.chinese_typo.enable and enable_chinese_typo:
            typoed_text, typo_corrections = typo_generator.create_typo_sentence(sentence)
            if typo_corrections:
                # 50%概率新增正确字/词，50%概率用正确分句替换错别字分句
                if random.random() < 0.5:
                    sentences.append(typoed_text)
                    sentences.append(typo_corrections)
                else:
                    # 用正确的分句替换错别字分句
                    sentences.append(sentence)
            else:
                sentences.append(typoed_text)
        else:
            sentences.append(sentence)

    if len(sentences) > max_sentence_num:
        if global_config.response_splitter.enable_overflow_return_all:
            logger.warning(f"分割后消息数量过多 ({len(sentences)} 条)，直接返回原文")
            sentences = [cleaned_text]
        else:
            logger.warning(f"分割后消息数量过多 ({len(sentences)} 条)，返回默认回复")
            return [_get_random_default_reply()]

    # if extracted_contents:
    #     for content in extracted_contents:
    #         sentences.append(content)

    # 在所有句子处理完毕后，对包含占位符的列表进行恢复
    if global_config.response_splitter.enable_kaomoji_protection:
        sentences = recover_kaomoji(sentences, kaomoji_mapping)

    return sentences


def calculate_typing_time(
    input_string: str,
    thinking_start_time: float,
    chinese_time: float = 0.3,
    english_time: float = 0.15,
    is_emoji: bool = False,
) -> float:
    """
    计算输入字符串所需的时间，中文和英文字符有不同的输入时间
        input_string (str): 输入的字符串
        chinese_time (float): 中文字符的输入时间，默认为0.2秒
        english_time (float): 英文字符的输入时间，默认为0.1秒
        is_emoji (bool): 是否为emoji，默认为False

    特殊情况：
    - 如果只有一个中文字符，将使用3倍的中文输入时间
    - 在所有输入结束后，额外加上回车时间0.3秒
    - 如果is_emoji为True，将使用固定1秒的输入时间
    """
    # chinese_time *= 1 / typing_speed_multiplier
    # english_time *= 1 / typing_speed_multiplier
    # 计算中文字符数
    chinese_chars = sum("\u4e00" <= char <= "\u9fff" for char in input_string)

    # 如果只有一个中文字符，使用3倍时间
    if chinese_chars == 1 and len(input_string.strip()) == 1:
        return chinese_time * 3 + 0.3  # 加上回车时间

    # 正常计算所有字符的输入时间
    total_time = 0.0
    for char in input_string:
        total_time += chinese_time if "\u4e00" <= char <= "\u9fff" else english_time
    if is_emoji:
        total_time = 1

    if time.time() - thinking_start_time > 10:
        total_time = 1

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

    for idx, match in enumerate(kaomoji_matches):
        kaomoji = match[0] or match[1]
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


def get_chat_type_and_target_info(chat_id: str) -> Tuple[bool, Optional["TargetPersonInfo"]]:
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
        if chat_stream := get_chat_manager().get_stream(chat_id):
            if chat_stream.group_info:
                is_group_chat = True
                chat_target_info = None  # Explicitly None for group chat
            elif chat_stream.user_info:  # It's a private chat
                is_group_chat = False
                user_info = chat_stream.user_info
                platform: str = chat_stream.platform
                user_id: str = user_info.user_id  # type: ignore

                from src.common.data_models.info_data_model import TargetPersonInfo  # 解决循环导入问题

                # Initialize target_info with basic info
                target_info = TargetPersonInfo(
                    platform=platform,
                    user_id=user_id,
                    user_nickname=user_info.user_nickname,  # type: ignore
                    person_id=None,
                    person_name=None,
                )

                # Try to fetch person info
                try:
                    person = Person(platform=platform, user_id=user_id)
                    if not person.is_known:
                        logger.warning(f"用户 {user_info.user_nickname} 尚未认识")
                        # 如果用户尚未认识，则返回False和None
                        return False, None
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


def record_replyer_action_temp(chat_id: str, reason: str, think_level: int) -> None:
    """
    临时记录replyer动作被选择的信息（仅群聊）

    Args:
        chat_id: 聊天ID
        reason: 选择理由
        think_level: 思考深度等级
    """
    try:
        # 确保data/temp目录存在
        temp_dir = "data/temp"
        os.makedirs(temp_dir, exist_ok=True)

        # 创建记录数据
        record_data = {
            "chat_id": chat_id,
            "reason": reason,
            "think_level": think_level,
            "timestamp": datetime.now().isoformat(),
        }

        # 生成文件名（使用时间戳避免冲突）
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"replyer_action_{timestamp_str}.json"
        filepath = os.path.join(temp_dir, filename)

        # 写入文件
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record_data, f, ensure_ascii=False, indent=2)

        logger.debug(f"已记录replyer动作选择: chat_id={chat_id}, think_level={think_level}")
    except Exception as e:
        logger.warning(f"记录replyer动作选择失败: {e}")


def assign_message_ids(messages: List[DatabaseMessages]) -> List[Tuple[str, DatabaseMessages]]:
    """
    为消息列表中的每个消息分配唯一的简短随机ID

    Args:
        messages: 消息列表

    Returns:
        List[DatabaseMessages]: 分配了唯一ID的消息列表(写入message_id属性)
    """
    result: List[Tuple[str, DatabaseMessages]] = []  # 复制原始消息列表
    used_ids = set()
    len_i = len(messages)
    if len_i > 100:
        a = 10
        b = 99
    else:
        a = 1
        b = 9

    for i, message in enumerate(messages):
        # 生成唯一的简短ID
        while True:
            # 使用索引+随机数生成简短ID
            random_suffix = random.randint(a, b)
            message_id = f"m{i + 1}{random_suffix}"

            if message_id not in used_ids:
                used_ids.add(message_id)
                break
        result.append((message_id, message))

    return result


def parse_keywords_string(keywords_input) -> list[str]:
    # sourcery skip: use-contextlib-suppress
    """
    统一的关键词解析函数，支持多种格式的关键词字符串解析

    支持的格式：
    1. 字符串列表格式：'["utils.py", "修改", "代码", "动作"]'
    2. 斜杠分隔格式：'utils.py/修改/代码/动作'
    3. 逗号分隔格式：'utils.py,修改,代码,动作'
    4. 空格分隔格式：'utils.py 修改 代码 动作'
    5. 已经是列表的情况：["utils.py", "修改", "代码", "动作"]
    6. JSON格式字符串：'{"keywords": ["utils.py", "修改", "代码", "动作"]}'

    Args:
        keywords_input: 关键词输入，可以是字符串或列表

    Returns:
        list[str]: 解析后的关键词列表，去除空白项
    """
    if not keywords_input:
        return []

    # 如果已经是列表，直接处理
    if isinstance(keywords_input, list):
        return [str(k).strip() for k in keywords_input if str(k).strip()]

    # 转换为字符串处理
    keywords_str = str(keywords_input).strip()
    if not keywords_str:
        return []

    try:
        # 尝试作为JSON对象解析（支持 {"keywords": [...]} 格式）
        json_data = json.loads(keywords_str)
        if isinstance(json_data, dict) and "keywords" in json_data:
            keywords_list = json_data["keywords"]
            if isinstance(keywords_list, list):
                return [str(k).strip() for k in keywords_list if str(k).strip()]
        elif isinstance(json_data, list):
            # 直接是JSON数组格式
            return [str(k).strip() for k in json_data if str(k).strip()]
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        # 尝试使用 ast.literal_eval 解析（支持Python字面量格式）
        parsed = ast.literal_eval(keywords_str)
        if isinstance(parsed, list):
            return [str(k).strip() for k in parsed if str(k).strip()]
    except (ValueError, SyntaxError):
        pass

    # 尝试不同的分隔符
    separators = ["/", ",", " ", "|", ";"]

    for separator in separators:
        if separator in keywords_str:
            keywords_list = [k.strip() for k in keywords_str.split(separator) if k.strip()]
            if len(keywords_list) > 1:  # 确保分割有效
                return keywords_list

    # 如果没有分隔符，返回单个关键词
    return [keywords_str] if keywords_str else []


def cut_key_words(concept_name: str) -> list[str]:
    """对概念名称进行jieba分词，并过滤掉关键词列表中的关键词"""
    concept_name_tokens = list(jieba.cut(concept_name))

    # 定义常见连词、停用词与标点
    conjunctions = {"和", "与", "及", "跟", "以及", "并且", "而且", "或", "或者", "并"}
    stop_words = {
        "的",
        "了",
        "呢",
        "吗",
        "吧",
        "啊",
        "哦",
        "恩",
        "嗯",
        "呀",
        "嘛",
        "哇",
        "在",
        "是",
        "很",
        "也",
        "又",
        "就",
        "都",
        "还",
        "更",
        "最",
        "被",
        "把",
        "给",
        "对",
        "和",
        "与",
        "及",
        "跟",
        "并",
        "而且",
        "或者",
        "或",
        "以及",
    }
    chinese_punctuations = set("，。！？、；：（）【】《》“”‘’—…·-——,.!?;:()[]<>'\"/\\")

    # 清理空白并初步过滤纯标点
    cleaned_tokens = []
    for tok in concept_name_tokens:
        t = tok.strip()
        if not t:
            continue
        # 去除纯标点
        if all(ch in chinese_punctuations for ch in t):
            continue
        cleaned_tokens.append(t)

    # 合并连词两侧的词（仅当两侧都存在且不是标点/停用词时）
    merged_tokens = []
    i = 0
    n = len(cleaned_tokens)
    while i < n:
        tok = cleaned_tokens[i]
        if tok in conjunctions and merged_tokens and i + 1 < n:
            left = merged_tokens[-1]
            right = cleaned_tokens[i + 1]
            # 左右都需要是有效词
            if (
                left
                and right
                and left not in conjunctions
                and right not in conjunctions
                and left not in stop_words
                and right not in stop_words
                and not all(ch in chinese_punctuations for ch in left)
                and not all(ch in chinese_punctuations for ch in right)
            ):
                # 合并为一个新词，并替换掉左侧与跳过右侧
                combined = f"{left}{tok}{right}"
                merged_tokens[-1] = combined
                i += 2
                continue
        # 常规推进
        merged_tokens.append(tok)
        i += 1

    # 二次过滤：去除停用词、单字符纯标点与无意义项
    result_tokens = []
    seen = set()
    # ban_words = set(getattr(global_config.memory, "memory_ban_words", []) or [])
    for tok in merged_tokens:
        if tok in conjunctions:
            # 独立连词丢弃
            continue
        if tok in stop_words:
            continue
        # if tok in ban_words:
        # continue
        if all(ch in chinese_punctuations for ch in tok):
            continue
        if tok.strip() == "":
            continue
        if tok not in seen:
            seen.add(tok)
            result_tokens.append(tok)

    filtered_concept_name_tokens = result_tokens
    return filtered_concept_name_tokens
