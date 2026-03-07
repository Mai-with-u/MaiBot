"""
表情服务模块

提供表情包相关的核心功能。
"""

import base64
import os
import random
import uuid

from typing import Any, Dict, List, Optional, Tuple

from src.chat.emoji_system.emoji_manager import emoji_manager, EMOJI_DIR
from src.common.logger import get_logger
from src.common.utils.utils_image import ImageUtils
from src.config.config import global_config

logger = get_logger("emoji_service")


# =============================================================================
# 表情包获取函数
# =============================================================================


async def get_by_description(description: str) -> Optional[Tuple[str, str, str]]:
    """根据描述选择表情包"""
    if not description:
        raise ValueError("描述不能为空")
    if not isinstance(description, str):
        raise TypeError("描述必须是字符串类型")
    try:
        logger.debug(f"[EmojiService] 根据描述获取表情包: {description}")

        emoji_obj = await emoji_manager.get_emoji_for_emotion(description)

        if not emoji_obj:
            logger.warning(f"[EmojiService] 未找到匹配描述 '{description}' 的表情包")
            return None

        emoji_path = str(emoji_obj.full_path)
        emoji_description = emoji_obj.description
        matched_emotion = random.choice(emoji_obj.emotion) if emoji_obj.emotion else ""
        emoji_base64 = ImageUtils.image_path_to_base64(emoji_path)

        if not emoji_base64:
            logger.error(f"[EmojiService] 无法将表情包文件转换为base64: {emoji_path}")
            return None

        logger.debug(f"[EmojiService] 成功获取表情包: {emoji_description}, 匹配情感: {matched_emotion}")
        return emoji_base64, emoji_description, matched_emotion

    except Exception as e:
        logger.error(f"[EmojiService] 获取表情包失败: {e}")
        return None


async def get_random(count: Optional[int] = 1) -> List[Tuple[str, str, str]]:
    """随机获取指定数量的表情包"""
    if not isinstance(count, int):
        raise TypeError("count 必须是整数类型")
    if count < 0:
        raise ValueError("count 不能为负数")
    if count == 0:
        logger.warning("[EmojiService] count 为0，返回空列表")
        return []

    try:
        all_emojis = emoji_manager.emojis

        if not all_emojis:
            logger.warning("[EmojiService] 没有可用的表情包")
            return []

        valid_emojis = [emoji for emoji in all_emojis if not emoji.is_deleted]
        if not valid_emojis:
            logger.warning("[EmojiService] 没有有效的表情包")
            return []

        if len(valid_emojis) < count:
            logger.debug(
                f"[EmojiService] 有效表情包数量 ({len(valid_emojis)}) 少于请求的数量 ({count})，将返回所有有效表情包"
            )
            count = len(valid_emojis)

        selected_emojis = random.sample(valid_emojis, count)

        results = []
        for selected_emoji in selected_emojis:
            emoji_base64 = ImageUtils.image_path_to_base64(str(selected_emoji.full_path))

            if not emoji_base64:
                logger.error(f"[EmojiService] 无法转换表情包为base64: {selected_emoji.full_path}")
                continue

            matched_emotion = random.choice(selected_emoji.emotion) if selected_emoji.emotion else "随机表情"

            emoji_manager.update_emoji_usage(selected_emoji)
            results.append((emoji_base64, selected_emoji.description, matched_emotion))

        if not results and count > 0:
            logger.warning("[EmojiService] 随机获取表情包失败，没有一个可以成功处理")
            return []

        logger.debug(f"[EmojiService] 成功获取 {len(results)} 个随机表情包")
        return results

    except Exception as e:
        logger.error(f"[EmojiService] 获取随机表情包失败: {e}")
        return []


async def get_by_emotion(emotion: str) -> Optional[Tuple[str, str, str]]:
    """根据情感标签获取表情包"""
    if not emotion:
        raise ValueError("情感标签不能为空")
    if not isinstance(emotion, str):
        raise TypeError("情感标签必须是字符串类型")
    try:
        logger.info(f"[EmojiService] 根据情感获取表情包: {emotion}")

        all_emojis = emoji_manager.emojis

        matching_emojis = []
        matching_emojis.extend(
            emoji_obj
            for emoji_obj in all_emojis
            if not emoji_obj.is_deleted and emotion.lower() in [e.lower() for e in emoji_obj.emotion]
        )
        if not matching_emojis:
            logger.warning(f"[EmojiService] 未找到匹配情感 '{emotion}' 的表情包")
            return None

        selected_emoji = random.choice(matching_emojis)
        emoji_base64 = ImageUtils.image_path_to_base64(selected_emoji.full_path)

        if not emoji_base64:
            logger.error(f"[EmojiService] 无法转换表情包为base64: {selected_emoji.full_path}")
            return None

        emoji_manager.update_emoji_usage(selected_emoji)

        logger.info(f"[EmojiService] 成功获取情感表情包: {selected_emoji.description}")
        return emoji_base64, selected_emoji.description, emotion

    except Exception as e:
        logger.error(f"[EmojiService] 根据情感获取表情包失败: {e}")
        return None


# =============================================================================
# 表情包信息查询函数
# =============================================================================


def get_count() -> int:
    try:
        return len(emoji_manager.emojis)
    except Exception as e:
        logger.error(f"[EmojiService] 获取表情包数量失败: {e}")
        return 0


def get_info():
    try:
        return {
            "current_count": len(emoji_manager.emojis),
            "max_count": global_config.emoji.max_reg_num,
            "available_emojis": len([e for e in emoji_manager.emojis if not e.is_deleted]),
        }
    except Exception as e:
        logger.error(f"[EmojiService] 获取表情包信息失败: {e}")
        return {"current_count": 0, "max_count": 0, "available_emojis": 0}


def get_emotions() -> List[str]:
    try:
        emotions = set()

        for emoji_obj in emoji_manager.emojis:
            if not emoji_obj.is_deleted and emoji_obj.emotion:
                emotions.update(emoji_obj.emotion)

        return sorted(list(emotions))
    except Exception as e:
        logger.error(f"[EmojiService] 获取情感标签失败: {e}")
        return []


async def get_all() -> List[Tuple[str, str, str]]:
    try:
        all_emojis = emoji_manager.emojis

        if not all_emojis:
            logger.warning("[EmojiService] 没有可用的表情包")
            return []

        results = []
        for emoji_obj in all_emojis:
            if emoji_obj.is_deleted:
                continue

            emoji_base64 = ImageUtils.image_path_to_base64(str(emoji_obj.full_path))

            if not emoji_base64:
                logger.error(f"[EmojiService] 无法转换表情包为base64: {emoji_obj.full_path}")
                continue

            matched_emotion = random.choice(emoji_obj.emotion) if emoji_obj.emotion else "随机表情"
            results.append((emoji_base64, emoji_obj.description, matched_emotion))

        logger.debug(f"[EmojiService] 成功获取 {len(results)} 个表情包")
        return results

    except Exception as e:
        logger.error(f"[EmojiService] 获取所有表情包失败: {e}")
        return []


def get_descriptions() -> List[str]:
    try:
        descriptions = []

        descriptions.extend(
            emoji_obj.description
            for emoji_obj in emoji_manager.emojis
            if not emoji_obj.is_deleted and emoji_obj.description
        )
        return descriptions
    except Exception as e:
        logger.error(f"[EmojiService] 获取表情包描述失败: {e}")
        return []


# =============================================================================
# 表情包注册函数
# =============================================================================


async def register_emoji(image_base64: str, filename: Optional[str] = None) -> Dict[str, Any]:
    """注册新的表情包"""
    if not image_base64:
        raise ValueError("图片base64编码不能为空")
    if not isinstance(image_base64, str):
        raise TypeError("image_base64必须是字符串类型")
    if filename is not None and not isinstance(filename, str):
        raise TypeError("filename必须是字符串类型或None")

    try:
        logger.info(f"[EmojiService] 开始注册表情包，文件名: {filename or '自动生成'}")

        count_before = len(emoji_manager.emojis)
        max_count = global_config.emoji.max_reg_num

        can_register = count_before < max_count or (count_before >= max_count and global_config.emoji.do_replace)

        if not can_register:
            return {
                "success": False,
                "message": f"表情包数量已达上限({count_before}/{max_count})且未启用替换功能",
                "description": None,
                "emotions": None,
                "replaced": None,
                "hash": None,
            }

        os.makedirs(EMOJI_DIR, exist_ok=True)

        if not filename:
            import time as _time

            timestamp = int(_time.time())
            microseconds = int(_time.time() * 1000000) % 1000000

            random_bytes = random.getrandbits(72).to_bytes(9, "big")
            short_id = base64.b64encode(random_bytes).decode("ascii")[:12].rstrip("=")
            short_id = short_id.replace("/", "_").replace("+", "-")
            filename = f"emoji_{timestamp}_{microseconds}_{short_id}"

        if not filename.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
            filename = f"{filename}.png"

        temp_file_path = os.path.join(EMOJI_DIR, filename)
        attempts = 0
        max_attempts = 10
        while os.path.exists(temp_file_path) and attempts < max_attempts:
            random_bytes = random.getrandbits(48).to_bytes(6, "big")
            short_id = base64.b64encode(random_bytes).decode("ascii")[:8].rstrip("=")
            short_id = short_id.replace("/", "_").replace("+", "-")

            name_part, ext = os.path.splitext(filename)
            base_name = name_part.rsplit("_", 1)[0]
            filename = f"{base_name}_{short_id}{ext}"
            temp_file_path = os.path.join(EMOJI_DIR, filename)
            attempts += 1

        if os.path.exists(temp_file_path):
            uuid_short = str(uuid.uuid4())[:8]
            name_part, ext = os.path.splitext(filename)
            base_name = name_part.rsplit("_", 1)[0]
            filename = f"{base_name}_{uuid_short}{ext}"
            temp_file_path = os.path.join(EMOJI_DIR, filename)

            counter = 1
            original_filename = filename
            while os.path.exists(temp_file_path):
                name_part, ext = os.path.splitext(original_filename)
                filename = f"{name_part}_{counter}{ext}"
                temp_file_path = os.path.join(EMOJI_DIR, filename)
                counter += 1

                if counter > 100:
                    logger.error(f"[EmojiService] 无法生成唯一文件名，尝试次数过多: {original_filename}")
                    return {
                        "success": False,
                        "message": "无法生成唯一文件名，请稍后重试",
                        "description": None,
                        "emotions": None,
                        "replaced": None,
                        "hash": None,
                    }

        try:
            if not ImageUtils.base64_to_image(image_base64, temp_file_path):
                logger.error(f"[EmojiService] 无法保存base64图片到文件: {temp_file_path}")
                return {
                    "success": False,
                    "message": "无法保存图片文件",
                    "description": None,
                    "emotions": None,
                    "replaced": None,
                    "hash": None,
                }

            logger.debug(f"[EmojiService] 图片已保存到临时文件: {temp_file_path}")

        except Exception as save_error:
            logger.error(f"[EmojiService] 保存图片文件失败: {save_error}")
            return {
                "success": False,
                "message": f"保存图片文件失败: {str(save_error)}",
                "description": None,
                "emotions": None,
                "replaced": None,
                "hash": None,
            }

        register_success = await emoji_manager.register_emoji_by_filename(filename)

        if not register_success and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"[EmojiService] 已清理临时文件: {temp_file_path}")
            except Exception as cleanup_error:
                logger.warning(f"[EmojiService] 清理临时文件失败: {cleanup_error}")

        if register_success:
            count_after = len(emoji_manager.emojis)
            replaced = count_after <= count_before

            new_emoji_info = None
            if count_after > count_before or replaced:
                try:
                    for emoji_obj in reversed(emoji_manager.emojis):
                        if not emoji_obj.is_deleted and (
                            emoji_obj.file_name == filename
                            or (hasattr(emoji_obj, "full_path") and filename in str(emoji_obj.full_path))
                        ):
                            new_emoji_info = emoji_obj
                            break
                except Exception as find_error:
                    logger.warning(f"[EmojiService] 查找新注册表情包信息失败: {find_error}")

            description = new_emoji_info.description if new_emoji_info else None
            emotions = new_emoji_info.emotion if new_emoji_info else None
            emoji_hash = new_emoji_info.emoji_hash if new_emoji_info else None

            return {
                "success": True,
                "message": f"表情包注册成功 {'(替换旧表情包)' if replaced else '(新增表情包)'}",
                "description": description,
                "emotions": emotions,
                "replaced": replaced,
                "hash": emoji_hash,
            }
        else:
            return {
                "success": False,
                "message": "表情包注册失败，可能因为重复、格式不支持或审核未通过",
                "description": None,
                "emotions": None,
                "replaced": None,
                "hash": None,
            }

    except Exception as e:
        logger.error(f"[EmojiService] 注册表情包时发生异常: {e}")
        return {
            "success": False,
            "message": f"注册过程中发生错误: {str(e)}",
            "description": None,
            "emotions": None,
            "replaced": None,
            "hash": None,
        }
