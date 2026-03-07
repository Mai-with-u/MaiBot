from datetime import datetime
from pathlib import Path
from rich.traceback import install
from sqlmodel import select
from typing import Optional, Tuple, List

import asyncio
import hashlib
import heapq
import Levenshtein
import random
import re

from src.common.logger import get_logger
from src.common.data_models.image_data_model import MaiEmoji
from src.common.database.database_model import Images, ImageType
from src.common.database.database import get_db_session, get_db_session_manual
from src.common.utils.utils_image import ImageUtils
from src.prompt.prompt_manager import prompt_manager
from src.config.config import global_config
from src.config.config import model_config
from src.llm_models.utils_model import LLMRequest

logger = get_logger("emoji")

install(extra_lines=3)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.absolute().resolve()
DATA_DIR = PROJECT_ROOT / "data"
EMOJI_DIR = DATA_DIR / "emoji"  # 表情包存储目录
EMOJI_REGISTERED_DIR = DATA_DIR / "emoji_registered"  # 已注册的表情包注册目录
MAX_EMOJI_FOR_PROMPT = 20  # 最大允许的表情包描述数量于图片替换的 prompt 中


def _ensure_directories():
    """确保表情包相关目录存在"""
    EMOJI_DIR.mkdir(parents=True, exist_ok=True)
    EMOJI_REGISTERED_DIR.mkdir(parents=True, exist_ok=True)


# TODO: 修改这个vlm为获取的vlm client，暂时使用这个VLM方法
emoji_manager_vlm = LLMRequest(model_set=model_config.model_task_config.vlm, request_type="emoji.see")
emoji_manager_emotion_judge_llm = LLMRequest(model_set=model_config.model_task_config.utils, request_type="emoji")


class EmojiManager:
    """
    表情包管理器
    """

    def __init__(self):
        _ensure_directories()

        self._emoji_num: int = 0
        self.emojis: list[MaiEmoji] = []

        logger.info("启动表情包管理器")

    async def get_emoji_description(
        self, *, emoji_bytes: Optional[bytes] = None, emoji_hash: Optional[str] = None
    ) -> Optional[Tuple[str, List[str]]]:
        """
        根据表情包哈希获取表情包描述和情感列表的封装方法

        Args:
            emoji_bytes (Optional[bytes]): 表情包的字节数据，如果提供了字节数据但数据库中没有找到对应记录，则会尝试构建表情包描述
            emoji_hash (Optional[str]): 表情包的哈希值，如果提供了哈希值则优先使用哈希值查找表情包描述
        Returns:
            return (Optional[Tuple[str, List[str]]]): 如果找到对应的表情包，则返回包含描述和情感标签的元组；若没找到，则尝试构建表情包描述并返回，如果构建失败则返回 None
        Raises:
            ValueError: 如果既没有提供表情包字节数据，也没有提供表情包哈希值，则抛出异常
            Exception: 如果在缓存表情包的过程中发生错误，则抛出异常
        """
        # 先查找
        if emoji_hash is None and emoji_bytes is not None:
            emoji_hash = hashlib.sha256(emoji_bytes).hexdigest()
        else:
            emoji_hash = emoji_hash
        if not emoji_hash:
            raise ValueError("获取表情包描述失败: 既没有提供表情包字节数据，也没有提供表情包哈希值")

        if emoji := self.get_emoji_by_hash(emoji_hash):
            return emoji.description, emoji.emotion or []
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji_hash, image_type=ImageType.EMOJI).limit(1)
                if result := session.exec(statement).first():
                    return result.description, result.emotion.split(",") if result.emotion else []
        except Exception as e:
            logger.warning(f"从数据库查找表情包时出错: {e}，将尝试构建表情包描述")

        # 如果提供了字节数据但数据库中没有找到，尝试构建
        if not emoji_bytes:
            return None

        # 找不到尝试构建
        logger.info(f"未找到哈希值为 {emoji_hash} 的表情包与其描述，尝试构建描述")
        full_path = EMOJI_DIR / f"{emoji_hash}.png"
        try:
            full_path.write_bytes(emoji_bytes)
            new_emoji = MaiEmoji(full_path=full_path, image_bytes=emoji_bytes)
            await new_emoji.calculate_hash_format()
        except Exception as e:
            logger.error(f"缓存表情包文件时出错: {e}")
            raise e
        success_desc, new_emoji = await self.build_emoji_description(new_emoji)
        if not success_desc:
            logger.error("构建表情包描述失败")
            return None
        success_emotion, new_emoji = await self.build_emoji_emotion(new_emoji)
        if not success_emotion:
            logger.error("构建表情包情感标签失败")
            return None

        # 缓存结果到数据库
        with get_db_session() as session:
            try:
                image_record = new_emoji.to_db_instance()
                image_record.is_registered = False
                image_record.is_banned = False
                image_record.register_time = datetime.now()
                image_record.no_file_flag = True
                session.add(image_record)
            except Exception as e:
                logger.error(f"缓存表情包描述时出错: {e}")
        return new_emoji.description, new_emoji.emotion or []

    def load_emojis_from_db(self) -> None:
        """
        从数据库加载已注册的表情包

        Raises:
            Exception: 如果加载过程中发生不可恢复错误，则抛出异常
        """
        logger.debug("[数据库] 开始加载所有表情包记录...")
        try:
            with get_db_session() as session:
                statement = select(Images)
                results = session.exec(statement).all()
                for record in results:
                    if record.image_type != ImageType.EMOJI:
                        continue
                    if record.no_file_flag:
                        continue
                    if record.is_banned:
                        continue
                    try:
                        emoji = MaiEmoji.from_db_instance(record)
                        self.emojis.append(emoji)
                    except Exception as e:
                        logger.error(
                            f"[数据库] 加载表情包记录时出错: {e}\n记录ID: {record.id}, 路径: {record.full_path}"
                        )
                self._emoji_num = len(self.emojis)
                logger.info(f"[数据库] 成功加载 {self._emoji_num} 个已注册表情包")
        except Exception as e:
            logger.critical(f"[数据库] 加载表情包记录时发生不可恢复错误: {e}")
            self.emojis = []
            self._emoji_num = 0
            raise e

    def register_emoji_to_db(self, emoji: MaiEmoji) -> bool:
        # sourcery skip: extract-method
        """
        将表情包注册到数据库中

        Args:
            emoji (MaiEmoji): 需要注册的表情包对象
        Returns:
            return (bool): 注册是否成功
        """
        if not emoji or not isinstance(emoji, MaiEmoji):
            logger.error("[注册表情包] 无效的表情包对象")
            return False
        if not emoji.full_path.exists():
            logger.error(f"[注册表情包] 表情包文件不存在: {emoji.full_path}")
            return False

        # 将表情包移动到已注册目录
        target_path = EMOJI_REGISTERED_DIR / emoji.file_name
        try:
            emoji.full_path.replace(target_path)
            emoji.full_path = target_path
        except Exception as e:
            logger.error(f"[注册表情包] 移动表情包文件时出错: {e}")
            return False

        # 注册到数据库
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if existing_record := session.exec(statement).first():
                    if existing_record.no_file_flag:
                        existing_record.no_file_flag = False
                        existing_record.is_banned = False
                        existing_record.full_path = str(emoji.full_path)
                        existing_record.description = emoji.description
                        existing_record.emotion = ",".join(emoji.emotion) if emoji.emotion else None
                        existing_record.query_count = emoji.query_count
                        existing_record.last_used_time = emoji.last_used_time
                        existing_record.register_time = emoji.register_time
                        session.add(existing_record)
                        logger.info(
                            f"[注册表情包] 更新已有记录并注册表情包到数据库, ID: {existing_record.id}, 路径: {emoji.full_path}"
                        )
                    else:
                        logger.warning(f"[注册表情包] 数据库中已存在表情包记录: {emoji.file_hash}")
                        return False
                else:
                    image_record = emoji.to_db_instance()
                    image_record.is_registered = True
                    image_record.is_banned = False
                    image_record.register_time = datetime.now()
                    session.add(image_record)
                    session.flush()
                    record_id = image_record.id
                    logger.info(f"[注册表情包] 成功注册表情包到数据库, ID: {record_id}, 路径: {emoji.full_path}")
        except Exception as e:
            logger.error(f"[注册表情包] 注册到数据库时出错: {e}")
            return False
        return True

    def delete_emoji(self, emoji: MaiEmoji, no_desc: bool = False) -> bool:
        """
        删除表情包的文件和数据库记录

        Args:
            emoji (MaiEmoji): 需要删除的表情包对象
            no_desc (bool): 如果为 True，则表示删除的表情包记录没有描述信息，删除时直接删除数据库记录；如果为`False`，则表示删除的表情包记录有描述信息，删除时将数据库记录的`no_file_flag`标记为`True`而不是直接删除记录。默认为`False`。
        Returns:
            return (bool): 删除是否成功
        """
        # 删除文件
        file_to_delete = emoji.full_path
        try:
            file_to_delete.unlink()
            logger.info(f"[删除表情包] 成功删除表情包文件: {emoji.file_name}")
        except FileNotFoundError:
            logger.warning(f"[删除表情包] 表情包文件 {emoji.file_name} 不存在，跳过文件删除")
        except Exception as e:
            logger.error(f"[删除表情包] 删除表情包文件时出错: {e}")
            return False

        # 删除数据库记录
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := session.exec(statement).first():
                    if no_desc:
                        session.delete(image_record)
                        logger.info(f"[删除表情包] 成功删除数据库中的空表情包记录: {emoji.file_name}")
                    else:
                        image_record.no_file_flag = True
                        session.add(image_record)
                        logger.info(f"[删除表情包] 成功修改数据库中的表情包记录: {emoji.file_name}")
                else:
                    logger.warning(f"[删除表情包] 数据库中未找到表情包记录: {emoji.file_name}")
        except Exception as e:
            logger.error(f"[删除表情包] 删除数据库记录时出错: {e}")
            # 如果数据库记录删除失败，但文件可能已删除，记录一个警告
            if file_to_delete.exists():
                logger.warning(f"[删除表情包] 数据库记录修改失败，但文件仍存在: {emoji.file_name}")
            return False

        return True

    def update_emoji_usage(self, emoji: MaiEmoji) -> bool:
        # sourcery skip: extract-method
        """
        更新表情包的使用情况，更新查询次数和上次使用时间

        Args:
            emoji (MaiEmoji): 使用的表情包对象
        Returns:
            return (bool): 更新是否成功
        """
        if not emoji or not emoji.file_hash:
            logger.error("[更新表情包使用] 无效的表情包对象")
            return False
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := session.exec(statement).first():
                    emoji.query_count += 1
                    image_record.query_count = emoji.query_count
                    emoji.last_used_time = datetime.now()
                    image_record.last_used_time = emoji.last_used_time
                    session.add(image_record)
                    logger.info(f"[记录表情包使用] 成功记录表情包使用: {emoji.file_hash}")
                else:
                    logger.error(f"[记录表情包使用] 未找到表情包记录: {emoji.file_hash}")
                    return False
        except Exception as e:
            logger.error(f"[记录表情包使用] 记录使用时出错: {e}")
            return False
        return True

    def update_emoji(self, emoji: MaiEmoji) -> bool:
        """
        更新表情包的情感标签和描述信息

        Args:
            emoji (MaiEmoji): 需要更新的表情包对象，必须包含有效的 file_hash
        Returns:
            return (bool): 更新是否成功
        """
        if not emoji or not emoji.file_hash:
            logger.error("[更新表情包] 无效的表情包对象")
            return False

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := session.exec(statement).first():
                    image_record.description = emoji.description
                    image_record.emotion = ",".join(emoji.emotion) if emoji.emotion else None
                    session.add(image_record)
                    logger.info(f"[更新表情包] 成功更新表情包信息: {emoji.file_hash}")
                else:
                    logger.error(f"[更新表情包] 未找到表情包记录: {emoji.file_hash}")
                    return False
        except Exception as e:
            logger.error(f"[更新表情包] 更新数据库记录时出错: {e}")
            return False
        return True

    def get_emoji_by_hash(self, emoji_hash: str) -> Optional[MaiEmoji]:
        """
        根据哈希值获取表情包对象

        Args:
            emoji_hash (str): 表情包的哈希值
        Returns:
            return (Optional[MaiEmoji]): 返回表情包对象，如果未找到则返回 None
        """
        for emoji in self.emojis:
            if emoji.file_hash == emoji_hash:
                return emoji
        logger.info(f"[获取表情包] 未找到哈希值为 {emoji_hash} 的表情包")
        return None

    def get_emoji_by_hash_from_db(self, emoji_hash: str) -> Optional[MaiEmoji]:
        """
        根据哈希值从数据库获取表情包对象

        Args:
            emoji_hash (str): 表情包的哈希值
        Returns:
            return (Optional[MaiEmoji]): 返回表情包对象，如果未找到则返回 None
        """
        try:
            with get_db_session() as session:
                statement = (
                    select(Images)
                    .filter_by(image_hash=emoji_hash, image_type=ImageType.EMOJI, is_banned=False)
                    .limit(1)
                )
                if image_record := session.exec(statement).first():
                    if image_record.no_file_flag:
                        logger.warning(f"[数据库] 表情包记录 {emoji_hash} 标记为文件不存在，无法获取表情包对象")
                        return None
                    return MaiEmoji.from_db_instance(image_record)
                logger.info(f"[数据库] 未找到哈希值为 {emoji_hash} 的表情包记录")
                return None
        except Exception as e:
            logger.error(f"[数据库] 获取表情包时出错: {e}")
            return None

    def ban_emoji(self, emoji: MaiEmoji) -> bool:
        """封禁表情包，将表情包的 is_banned 字段设置为 True，并从表情包列表中移除"""
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := session.exec(statement).first():
                    image_record.is_banned = True
                    session.add(image_record)
                    if emoji in self.emojis:
                        self.emojis.remove(emoji)
                    logger.info(f"[封禁表情包] 成功封禁表情包: {emoji.file_name}")
                else:
                    logger.warning(f"[封禁表情包] 未找到表情包记录: {emoji.file_name}")
                    return False
        except Exception as e:
            logger.error(f"[封禁表情包] 封禁时出错: {e}")
            return False
        return True

    async def get_emoji_for_emotion(self, emotion_label: str) -> Optional[MaiEmoji]:
        """
        根据文本情感标签获取合适的表情包

        Args:
            emotion_label (str): 文本的情感标签
        Returns:
            return (Optional[MaiEmoji]): 返回表情包对象，如果未找到则返回 None
        """
        if not self.emojis:
            logger.warning("[获取表情包] 表情包列表为空")
            return None

        emoji_similarities = await asyncio.to_thread(self._calculate_emotion_similarity_list, emotion_label)
        if not emoji_similarities:
            logger.info("[获取表情包] 未找到匹配的表情包")
            return None

        # 获取前10个相似度最高的表情包
        top_emojis = heapq.nlargest(10, emoji_similarities, key=lambda x: x[1])
        selected_emoji, similarity = random.choice(top_emojis)
        self.update_emoji_usage(selected_emoji)
        logger.info(
            f"[获取表情包] 为[{emotion_label}]选中表情包: {selected_emoji.file_name}({selected_emoji.emotion})，相似度: {similarity:.4f}"
        )
        return selected_emoji

    async def replace_an_emoji_by_llm(self, new_emoji: MaiEmoji) -> bool:
        """
        使用 LLM 决策替换一个表情包

        **不校验是否开启表情包偷取**

        Args:
            new_emoji (MaiEmoji): 新添加的表情包对象
        Returns:
            return (bool): 是否成功替换了一个表情包
        """
        # sourcery skip: use-getitem-for-re-match-groups
        probabilities = [1 / (emoji.query_count + 1) for emoji in self.emojis]
        selected_emojis = random.choices(
            self.emojis, weights=probabilities, k=min(MAX_EMOJI_FOR_PROMPT, len(self.emojis))
        )
        emoji_info_list: list[str] = []
        for i, emoji in enumerate(selected_emojis):
            time_str = emoji.register_time.strftime("%Y-%m-%d %H:%M:%S") if emoji.register_time else "未知时间"
            emoji_info = (
                f"编号: {i + 1}\n描述: {emoji.description}\n使用次数: {emoji.query_count}\n添加时间: {time_str}\n"
            )
            emoji_info_list.append(emoji_info)

        emoji_replace_prompt_template = prompt_manager.get_prompt("emoji_replace")
        emoji_replace_prompt_template.add_context("nickname", global_config.bot.nickname)
        emoji_replace_prompt_template.add_context("emoji_num", str(self._emoji_num))
        emoji_replace_prompt_template.add_context("emoji_num_max", str(global_config.emoji.max_reg_num))
        emoji_replace_prompt_template.add_context("emoji_list", "\n".join(emoji_info_list))
        emoji_replace_prompt = await prompt_manager.render_prompt(emoji_replace_prompt_template)

        decision, _ = await emoji_manager_emotion_judge_llm.generate_response_async(
            emoji_replace_prompt, temperature=0.8, max_tokens=600
        )
        logger.info(f"[决策] 结果: {decision}")

        # 解析决策结果
        if "不删除" in decision:
            logger.info("[决策] 不删除任何表情包")
            return False
        try:
            match = re.search(r"删除编号(\d+)", decision)
        except Exception as e:
            logger.error(f"[决策] 解析决策结果时出错: {e}")
            return False
        if match:
            emoji_index = int(match.group(1)) - 1  # 转换为0-based索引
            # 检查索引是否有效
            if 0 <= emoji_index < len(selected_emojis):
                emoji_to_delete = selected_emojis[emoji_index]
                logger.info(f"[决策] 删除表情包: {emoji_to_delete.description}")
                if self.delete_emoji(emoji_to_delete):
                    self.emojis.remove(emoji_to_delete)
                    if self.register_emoji_to_db(new_emoji):
                        self.emojis.append(new_emoji)
                        logger.info(f"[注册表情包] 成功替换并注册新表情包: {new_emoji.description}")
                        return True
                    else:
                        logger.error(f"[注册表情包] 注册新表情包失败: {new_emoji.description}")
                else:
                    logger.error("[错误] 删除表情包失败，无法完成替换")
            else:
                logger.error(f"[决策] 无效的表情包编号: {emoji_index + 1}")
        else:
            logger.error("[决策] 未能解析删除编号")
        return False

    async def build_emoji_description(self, target_emoji: MaiEmoji) -> Tuple[bool, MaiEmoji]:
        """
        构建表情包描述

        Args:
            target_emoji (MaiEmoji): 目标表情包对象
        Returns:
            return (Tuple[bool, MaiEmoji]): 返回是否成功构建描述，及表情包对象
        """
        if not target_emoji.file_hash:
            # Should not happen, but just in case
            await target_emoji.calculate_hash_format()

        # 调用VLM生成描述
        image_format = target_emoji.image_format
        image_bytes = target_emoji.image_bytes or await asyncio.to_thread(
            target_emoji.read_image_bytes, target_emoji.full_path
        )

        if image_format == "gif":
            try:
                image_bytes = await asyncio.to_thread(ImageUtils.gif_2_static_image, image_bytes)
            except Exception as e:
                logger.error(f"[构建描述] 转换 GIF 图片时出错: {e}")
                return False, target_emoji
            prompt: str = "这是一个动态图表情包，每一张图代表了动态图的某一帧，黑色背景代表透明，简短描述一下表情包表达的情感和内容，从互联网梗、meme的角度去分析，精简回答"
            image_base64 = ImageUtils.image_bytes_to_base64(image_bytes)
            description, _ = await emoji_manager_vlm.generate_response_for_image(
                prompt, image_base64, "jpg", temperature=0.5
            )
        else:
            prompt: str = "这是一个表情包，请详细描述一下表情包所表达的情感和内容，简短描述细节，从互联网梗、meme的角度去分析，精简回答"
            image_base64 = ImageUtils.image_bytes_to_base64(image_bytes)
            description, _ = await emoji_manager_vlm.generate_response_for_image(
                prompt, image_base64, image_format, temperature=0.5
            )

        # 表情包审查
        if global_config.emoji.content_filtration:
            filtration_prompt_template = prompt_manager.get_prompt("emoji_content_filtration")
            filtration_prompt_template.add_context("demand", global_config.emoji.filtration_prompt)
            filtration_prompt = await prompt_manager.render_prompt(filtration_prompt_template)
            llm_response, _ = await emoji_manager_vlm.generate_response_for_image(
                filtration_prompt, image_base64, image_format, temperature=0.3
            )
            if "否" in llm_response:
                logger.warning(f"[表情包审查] 表情包内容不符合要求，拒绝注册: {target_emoji.file_name}")
                return False, target_emoji
        target_emoji.description = description
        logger.info(f"[构建描述] 成功为表情包构建描述: {target_emoji.description}")
        return True, target_emoji

    async def build_emoji_emotion(self, target_emoji: MaiEmoji) -> Tuple[bool, MaiEmoji]:
        """
        构建表情包情感，使用场景标签

        Args:
            target_emoji (MaiEmoji): 目标表情包对象
        Returns:
            return (Tuple[bool, MaiEmoji]): 返回是否成功构建情感标签，及表情包对象
        """
        if not target_emoji.description:
            logger.error("[构建情感标签] 表情包描述为空，无法构建情感标签")
            return False, target_emoji

        # 获取Prompt
        emotion_prompt_template = prompt_manager.get_prompt("emoji_content_analysis")
        emotion_prompt_template.add_context("description", target_emoji.description)
        emotion_prompt = await prompt_manager.render_prompt(emotion_prompt_template)
        # 调用LLM生成情感标签
        emotion_result, _ = await emoji_manager_emotion_judge_llm.generate_response_async(
            emotion_prompt, temperature=0.3, max_tokens=200
        )

        # 解析情感标签结果
        emotions = [e.strip() for e in emotion_result.replace("，", ",").split(",") if e.strip()]

        # 根据情感标签数量随机选择 - 超过5个选3个，超过2个选2个
        if len(emotions) > 5:
            emotions = random.sample(emotions, 3)
        elif len(emotions) > 2:
            emotions = random.sample(emotions, 2)

        logger.info(f"[构建情感标签] 成功为表情包构建情感标签: {','.join(emotions)}")
        target_emoji.emotion = emotions
        return True, target_emoji

    def check_emoji_file_integrity(self) -> None:
        """
        检查表情包完整性，删除文件缺失的表情包记录
        """
        logger.info("[完整性检查] 开始检查表情包文件完整性...")
        to_delete_emojis: list[Tuple[MaiEmoji, bool]] = []
        removal_count = 0
        for emoji in self.emojis:
            if not emoji.full_path.exists():
                logger.warning(f"[完整性检查] 表情包文件缺失，准备修改记录: {emoji.file_name}")
                to_delete_emojis.append((emoji, False))
            if not emoji.description:
                logger.warning(f"[完整性检查] 表情包记录缺失描述，准备删除记录: {emoji.file_name}")
                to_delete_emojis.append((emoji, True))

        for emoji, is_description_empty in to_delete_emojis:
            if self.delete_emoji(emoji, is_description_empty):
                self.emojis.remove(emoji)
                self._emoji_num -= 1
                removal_count += 1
                logger.info(f"[完整性检查] 成功删除缺失文件的表情包记录: {emoji.file_name}")
            else:
                logger.error(f"[完整性检查] 删除缺失文件的表情包记录失败: {emoji.file_name}")

        logger.info(f"[完整性检查] 表情包文件完整性检查完成，删除了 {removal_count} 条记录")

    def remove_untracked_emoji_files(self) -> None:
        """
        删除未被数据库记录跟踪的表情包文件
        """
        logger.info("[未跟踪表情包清理] 开始清理未被数据库记录跟踪的表情包文件...")
        tracked_files = {emoji.full_path.name for emoji in self.emojis}
        all_files = set(EMOJI_REGISTERED_DIR.glob("*"))
        removal_count = 0

        for file_path in all_files:
            if file_path.name not in tracked_files:
                try:
                    file_path.unlink()
                    removal_count += 1
                    logger.info(f"[未跟踪表情包清理] 删除未跟踪的表情包文件: {file_path.name}")
                except Exception as e:
                    logger.error(f"[未跟踪表情包清理] 删除文件 {file_path.name} 时出错: {e}")

        logger.info(f"[未跟踪表情包清理] 未跟踪表情包文件清理完成，删除了 {removal_count} 个文件")

    async def periodic_emoji_maintenance(self) -> None:
        """
        定期执行表情包维护任务，包括完整性检查和未跟踪文件清理
        """
        while True:
            EMOJI_DIR.mkdir(parents=True, exist_ok=True)
            EMOJI_REGISTERED_DIR.mkdir(parents=True, exist_ok=True)
            try:
                self.check_emoji_file_integrity()
                self.remove_untracked_emoji_files()
            except Exception as e:
                logger.error(f"[定期维护] 执行表情包维护任务时出错: {e}")

            if global_config.emoji.steal_emoji and (
                self._emoji_num < global_config.emoji.max_reg_num
                or (self._emoji_num > global_config.emoji.max_reg_num and global_config.emoji.do_replace)
            ):
                logger.info("[定期维护] 尝试从表情包盗取目录注册新表情包...")
                for emoji_file in EMOJI_DIR.iterdir():
                    if not emoji_file.is_file():
                        continue
                    if await self.register_emoji_by_filename(emoji_file):
                        break  # 每次只注册一个表情包
                    try:
                        emoji_file.unlink()
                        logger.info(f"[定期维护] 删除无法注册的表情包文件: {emoji_file.name}")
                    except Exception as e:
                        logger.error(f"[定期维护] 删除文件 {emoji_file.name} 时出错: {e}")
            await asyncio.sleep(global_config.emoji.check_interval * 60)

    async def register_emoji_by_filename(self, filename: Path | str) -> bool:
        """
        根据指定的表情包图片，分析并注册到数据库

        Args:
            filename (Path | str): 表情包图片的完整文件路径（可能根据文件实际格式修正）

        Returns:
            return (bool): 注册是否成功
        """
        file_full_path = Path(filename).absolute().resolve()
        if not file_full_path.exists():
            logger.error(f"[注册表情包] 表情包文件不存在: {file_full_path}")
            return False
        try:
            target_emoji = MaiEmoji(full_path=file_full_path)
        except Exception as e:
            logger.error(f"[注册表情包] 创建表情包对象时出错: {e}")
            return False

        # 0. 先验证数据库中是否已经存在相同哈希的表情包，避免重复构建
        try:
            with get_db_session_manual() as session:
                statement = (
                    select(Images).filter_by(image_hash=target_emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                )
                if image_record := session.exec(statement).first():
                    if image_record.no_file_flag:
                        image_record.no_file_flag = False
                        image_record.is_banned = False
                        image_record.is_registered = True
                        image_record.full_path = str(target_emoji.full_path)
                        session.add(image_record)
                        session.commit()
                        logger.info(f"表情包注册成功，Hash: {target_emoji.file_hash}")
                        return True
                    else:
                        logger.warning(f"[注册表情包] 数据库中已存在表情包记录，跳过注册: {target_emoji.file_name}")
                        return False
        except Exception as e:
            logger.error(f"[注册表情包] 查询数据库时出错: {e}")
            return False

        # 1. 计算哈希值和格式
        calc_success = await target_emoji.calculate_hash_format()
        if not calc_success:
            logger.error(f"[注册表情包] 计算表情包哈希值和格式失败: {file_full_path}")
            return False
        file_full_path = target_emoji.full_path  # 更新为可能修正后的路径
        # 2. 检查是否已经存在过
        if existing_emoji := self.get_emoji_by_hash(target_emoji.file_hash):
            logger.warning(f"[注册表情包] 表情包已存在，跳过注册: {existing_emoji.file_name}")
            return False
        # 3. 构建描述
        desc_success, target_emoji = await self.build_emoji_description(target_emoji)
        if not desc_success:
            logger.error(f"[注册表情包] 构建表情包描述失败: {file_full_path}")
            return False
        # 4. 构建情感标签
        emo_success, target_emoji = await self.build_emoji_emotion(target_emoji)
        if not emo_success:
            logger.error(f"[注册表情包] 构建表情包情感标签失败: {file_full_path}")
            return False

        # 5. 检查容量并决定是否替换或者直接注册
        if self._emoji_num >= global_config.emoji.max_reg_num and global_config.emoji.do_replace:
            logger.warning(f"[注册表情包] 表情包数量已达上限{global_config.emoji.max_reg_num}，尝试替换一个表情包")
            replaced = await self.replace_an_emoji_by_llm(target_emoji)
            if not replaced:
                logger.error("[注册表情包] 替换表情包失败，无法注册新表情包")
                return False
            return True
        else:
            if self.register_emoji_to_db(target_emoji):
                self.emojis.append(target_emoji)
                self._emoji_num += 1
                logger.info(f"[注册表情包] 成功注册新表情包: {target_emoji.file_name}")
                return True
            else:
                logger.error(f"[注册表情包] 注册表情包到数据库失败: {file_full_path}")
                return False

    def _calculate_emotion_similarity_list(self, text_emotion: str) -> List[Tuple[MaiEmoji, float]]:
        """
        计算文本情感标签与所有表情包情感标签的相似度列表

        Args:
            text_emotion (str): 文本的情感标签
        Returns:
            return (List[Tuple[MaiEmoji, float]]): 返回表情包对象及其相似度的列表
        """
        similarity_list: List[Tuple[MaiEmoji, float]] = []
        for emoji in self.emojis:
            if not emoji.emotion:
                continue
            # 计算情感标签相似度，使用 Levenshtein 距离作为相似度指标
            distance = Levenshtein.distance(text_emotion, emoji.emotion)
            max_len = max(len(text_emotion), len(emoji.emotion))
            similarity = 1 - (distance / max_len if max_len > 0 else 0)
            similarity_list.append((emoji, similarity))
        return similarity_list


emoji_manager = EmojiManager()
