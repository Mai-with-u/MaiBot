"""
回复器API模块

提供回复器相关功能，采用标准Python包设计模式
使用方式：
    from src.plugin_system.apis import generator_api
    replyer = generator_api.get_replyer(chat_stream)
    success, reply_set, _ = await generator_api.generate_reply(chat_stream, action_data, reasoning)
"""

import traceback
import time
from typing import Tuple, Any, Dict, List, Optional, TYPE_CHECKING
from rich.traceback import install
from src.common.logger import get_logger
from src.common.data_models.message_data_model import ReplySetModel
from src.chat.replyer.group_generator import DefaultReplyer
from src.chat.replyer.private_generator import PrivateReplyer
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.utils.utils import process_llm_response
from src.chat.replyer.replyer_manager import replyer_manager
from src.plugin_system.base.component_types import ActionInfo
from src.chat.logger.plan_reply_logger import PlanReplyLogger

if TYPE_CHECKING:
    from src.common.data_models.info_data_model import ActionPlannerInfo
    from src.common.data_models.database_data_model import DatabaseMessages
    from src.common.data_models.llm_data_model import LLMGenerationDataModel

install(extra_lines=3)

logger = get_logger("generator_api")


# =============================================================================
# 回复器获取API函数
# =============================================================================


def get_replyer(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    request_type: str = "replyer",
) -> Optional[DefaultReplyer | PrivateReplyer]:
    """获取回复器对象

    优先使用chat_stream，如果没有则使用chat_id直接查找。
    使用 ReplyerManager 来管理实例，避免重复创建。

    Args:
        chat_stream: 聊天流对象（优先）
        chat_id: 聊天ID（实际上就是stream_id）
        request_type: 请求类型

    Returns:
        Optional[DefaultReplyer]: 回复器对象，如果获取失败则返回None

    Raises:
        ValueError: chat_stream 和 chat_id 均为空
    """
    if not chat_id and not chat_stream:
        raise ValueError("chat_stream 和 chat_id 不可均为空")
    try:
        logger.debug(f"[GeneratorAPI] 正在获取回复器，chat_id: {chat_id}, chat_stream: {'有' if chat_stream else '无'}")
        return replyer_manager.get_replyer(
            chat_stream=chat_stream,
            chat_id=chat_id,
            request_type=request_type,
        )
    except Exception as e:
        logger.error(f"[GeneratorAPI] 获取回复器时发生意外错误: {e}", exc_info=True)
        traceback.print_exc()
        return None


# =============================================================================
# 回复生成API函数
# =============================================================================


async def generate_reply(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    action_data: Optional[Dict[str, Any]] = None,
    reply_message: Optional["DatabaseMessages"] = None,
    think_level: int = 1,
    extra_info: str = "",
    reply_reason: str = "",
    available_actions: Optional[Dict[str, ActionInfo]] = None,
    chosen_actions: Optional[List["ActionPlannerInfo"]] = None,
    unknown_words: Optional[List[str]] = None,
    enable_tool: bool = False,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    request_type: str = "generator_api",
    from_plugin: bool = True,
    reply_time_point: Optional[float] = None,
) -> Tuple[bool, Optional["LLMGenerationDataModel"]]:
    """生成回复

    Args:
        chat_stream: 聊天流对象（优先）
        chat_id: 聊天ID（备用）
        action_data: 动作数据（向下兼容，包含reply_to和extra_info）
        reply_message: 回复的消息对象
        extra_info: 额外信息，用于补充上下文
        reply_reason: 回复原因
        available_actions: 可用动作
        chosen_actions: 已选动作
        unknown_words: Planner 在 reply 动作中给出的未知词语列表，用于黑话检索
        enable_tool: 是否启用工具调用
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
        return_prompt: 是否返回提示词
        model_set_with_weight: 模型配置列表，每个元素为 (TaskConfig, weight) 元组
        request_type: 请求类型（可选，记录LLM使用）
        from_plugin: 是否来自插件
        reply_time_point: 回复时间点
    Returns:
        Tuple[bool, List[Tuple[str, Any]], Optional[str]]: (是否成功, 回复集合, 提示词)
    """
    try:
        # 如果 reply_time_point 未传入，设置为当前时间戳
        if reply_time_point is None:
            reply_time_point = time.time()
        
        # 获取回复器
        logger.debug("[GeneratorAPI] 开始生成回复")
        replyer = get_replyer(chat_stream, chat_id, request_type=request_type)
        if not replyer:
            logger.error("[GeneratorAPI] 无法获取回复器")
            return False, None

        if action_data:
            if not extra_info:
                extra_info = action_data.get("extra_info", "")
            if not reply_reason:
                reply_reason = action_data.get("reason", "")
            # 仅在 reply 场景下使用的未知词语解析（Planner JSON 中下发）
            if unknown_words is None:
                uw = action_data.get("unknown_words")
                if isinstance(uw, list):
                    # 只保留非空字符串
                    cleaned: List[str] = []
                    for item in uw:
                        if isinstance(item, str):
                            s = item.strip()
                            if s:
                                cleaned.append(s)
                    if cleaned:
                        unknown_words = cleaned

        # 调用回复器生成回复
        success, llm_response = await replyer.generate_reply_with_context(
            extra_info=extra_info,
            available_actions=available_actions,
            chosen_actions=chosen_actions,
            enable_tool=enable_tool,
            reply_message=reply_message,
            reply_reason=reply_reason,
            unknown_words=unknown_words,
            think_level=think_level,
            from_plugin=from_plugin,
            stream_id=chat_stream.stream_id if chat_stream else chat_id,
            reply_time_point=reply_time_point,
            log_reply=False,
        )
        if not success:
            logger.warning("[GeneratorAPI] 回复生成失败")
            return False, None
        reply_set: Optional[ReplySetModel] = None
        if content := llm_response.content:
            processed_response = process_llm_response(content, enable_splitter, enable_chinese_typo)
            llm_response.processed_output = processed_response
            reply_set = ReplySetModel()
            for text in processed_response:
                reply_set.add_text_content(text)
        llm_response.reply_set = reply_set
        logger.debug(f"[GeneratorAPI] 回复生成成功，生成了 {len(reply_set) if reply_set else 0} 个回复项")

        # 统一在这里记录最终回复日志（包含分割后的 processed_output）
        try:
            PlanReplyLogger.log_reply(
                chat_id=chat_stream.stream_id if chat_stream else (chat_id or ""),
                prompt=llm_response.prompt or "",
                output=llm_response.content,
                processed_output=llm_response.processed_output,
                model=llm_response.model,
                timing=llm_response.timing,
                reasoning=llm_response.reasoning,
                think_level=think_level,
                success=True,
            )
        except Exception:
            logger.exception("[GeneratorAPI] 记录reply日志失败")

        return success, llm_response

    except ValueError as ve:
        raise ve

    except UserWarning as uw:
        logger.warning(f"[GeneratorAPI] 中断了生成: {uw}")
        return False, None

    except Exception as e:
        logger.error(f"[GeneratorAPI] 生成回复时出错: {e}")
        logger.error(traceback.format_exc())
        return False, None


async def rewrite_reply(
    chat_stream: Optional[ChatStream] = None,
    reply_data: Optional[Dict[str, Any]] = None,
    chat_id: Optional[str] = None,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    raw_reply: str = "",
    reason: str = "",
    reply_to: str = "",
    request_type: str = "generator_api",
) -> Tuple[bool, Optional["LLMGenerationDataModel"]]:
    """重写回复

    Args:
        chat_stream: 聊天流对象（优先）
        reply_data: 回复数据字典（向下兼容备用，当其他参数缺失时从此获取）
        chat_id: 聊天ID（备用）
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
        model_set_with_weight: 模型配置列表，每个元素为 (TaskConfig, weight) 元组
        raw_reply: 原始回复内容
        reason: 回复原因
        reply_to: 回复对象
        return_prompt: 是否返回提示词

    Returns:
        Tuple[bool, List[Tuple[str, Any]]]: (是否成功, 回复集合)
    """
    try:
        # 获取回复器
        replyer = get_replyer(chat_stream, chat_id, request_type=request_type)
        if not replyer:
            logger.error("[GeneratorAPI] 无法获取回复器")
            return False, None

        logger.info("[GeneratorAPI] 开始重写回复")

        # 如果参数缺失，从reply_data中获取
        if reply_data:
            raw_reply = raw_reply or reply_data.get("raw_reply", "")
            reason = reason or reply_data.get("reason", "")
            reply_to = reply_to or reply_data.get("reply_to", "")

        # 调用回复器重写回复
        success, llm_response = await replyer.rewrite_reply_with_context(
            raw_reply=raw_reply,
            reason=reason,
            reply_to=reply_to,
        )
        reply_set: Optional[ReplySetModel] = None
        if success and llm_response and (content := llm_response.content):
            reply_set = process_human_text(content, enable_splitter, enable_chinese_typo)
        llm_response.reply_set = reply_set
        if success:
            logger.info(f"[GeneratorAPI] 重写回复成功，生成了 {len(reply_set) if reply_set else 0} 个回复项")
        else:
            logger.warning("[GeneratorAPI] 重写回复失败")

        return success, llm_response

    except ValueError as ve:
        raise ve

    except Exception as e:
        logger.error(f"[GeneratorAPI] 重写回复时出错: {e}")
        return False, None


def process_human_text(content: str, enable_splitter: bool, enable_chinese_typo: bool) -> Optional[ReplySetModel]:
    """将文本处理为更拟人化的文本

    Args:
        content: 文本内容
        enable_splitter: 是否启用消息分割器
        enable_chinese_typo: 是否启用错字生成器
    """
    if not isinstance(content, str):
        raise ValueError("content 必须是字符串类型")
    try:
        reply_set = ReplySetModel()
        processed_response = process_llm_response(content, enable_splitter, enable_chinese_typo)

        for text in processed_response:
            reply_set.add_text_content(text)

        return reply_set

    except Exception as e:
        logger.error(f"[GeneratorAPI] 处理人形文本时出错: {e}")
        return None


async def generate_response_custom(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    request_type: str = "generator_api",
    prompt: str = "",
) -> Optional[str]:
    replyer = get_replyer(chat_stream, chat_id, request_type=request_type)
    if not replyer:
        logger.error("[GeneratorAPI] 无法获取回复器")
        return None

    try:
        logger.debug("[GeneratorAPI] 开始生成自定义回复")
        response, _, _, _ = await replyer.llm_generate_content(prompt)
        if response:
            logger.debug("[GeneratorAPI] 自定义回复生成成功")
            return response
        else:
            logger.warning("[GeneratorAPI] 自定义回复生成失败")
            return None
    except Exception as e:
        logger.error(f"[GeneratorAPI] 生成自定义回复时出错: {e}")
        return None
