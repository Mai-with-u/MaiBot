import asyncio
import base64
import hashlib
import os
import random
import time
import traceback
import io
import re
import binascii

from typing import Optional, Tuple, List, Any
from PIL import Image
from rich.traceback import install

from src.common.database.database_model import Emoji, EmojiDescriptionCache
from src.common.database.database import db as peewee_db
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.chat.utils.utils_image import image_path_to_base64, get_image_manager
from src.llm_models.utils_model import LLMRequest

install(extra_lines=3)

logger = get_logger("emoji")

BASE_DIR = os.path.join("data")
EMOJI_DIR = os.path.join(BASE_DIR, "emoji")  # 表情包存储目录
EMOJI_REGISTERED_DIR = os.path.join(BASE_DIR, "emoji_registed")  # 已注册的表情包注册目录
MAX_EMOJI_FOR_PROMPT = 20  # 最大允许的表情包描述数量于图片替换的 prompt 中

"""
还没经过测试，有些地方数据库和内存数据同步可能不完全

"""


class MaiEmoji:
    """定义一个表情包"""

    def __init__(self, full_path: str):
        if not full_path:
            raise ValueError("full_path cannot be empty")
        self.full_path = full_path  # 文件的完整路径 (包括文件名)
        self.path = os.path.dirname(full_path)  # 文件所在的目录路径
        self.filename = os.path.basename(full_path)  # 文件名
        self.embedding = []
        self.hash = ""  # 初始为空，在创建实例时会计算
        self.description = ""
        self.emotion: List[str] = []
        self.usage_count = 0
        self.last_used_time = time.time()
        self.register_time = time.time()
        self.is_deleted = False  # 标记是否已被删除
        self.format = ""

    async def initialize_hash_format(self) -> Optional[bool]:
        """从文件创建表情包实例, 计算哈希值和格式"""
        try:
            # 使用 full_path 检查文件是否存在
            if not os.path.exists(self.full_path):
                logger.error(f"[初始化错误] 表情包文件不存在: {self.full_path}")
                self.is_deleted = True
                return None

            # 使用 full_path 读取文件
            logger.debug(f"[初始化] 正在读取文件: {self.full_path}")
            image_base64 = image_path_to_base64(self.full_path)
            if image_base64 is None:
                logger.error(f"[初始化错误] 无法读取或转换Base64: {self.full_path}")
                self.is_deleted = True
                return None
            logger.debug(f"[初始化] 文件读取成功 (Base64预览: {image_base64[:50]}...)")

            # 计算哈希值
            logger.debug(f"[初始化] 正在解码Base64并计算哈希: {self.filename}")
            # 确保base64字符串只包含ASCII字符
            if isinstance(image_base64, str):
                image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")
            image_bytes = base64.b64decode(image_base64)
            self.hash = hashlib.md5(image_bytes).hexdigest()
            logger.debug(f"[初始化] 哈希计算成功: {self.hash}")

            # 获取图片格式
            logger.debug(f"[初始化] 正在使用Pillow获取格式: {self.filename}")
            try:
                with Image.open(io.BytesIO(image_bytes)) as img:
                    self.format = img.format.lower()  # type: ignore
                logger.debug(f"[初始化] 格式获取成功: {self.format}")
            except Exception as pil_error:
                logger.error(f"[初始化错误] Pillow无法处理图片 ({self.filename}): {pil_error}")
                logger.error(traceback.format_exc())
                self.is_deleted = True
                return None

            # 如果所有步骤成功，返回 True
            return True

        except FileNotFoundError:
            logger.error(f"[初始化错误] 文件在处理过程中丢失: {self.full_path}")
            self.is_deleted = True
            return None
        except (binascii.Error, ValueError) as b64_error:
            logger.error(f"[初始化错误] Base64解码失败 ({self.filename}): {b64_error}")
            self.is_deleted = True
            return None
        except Exception as e:
            logger.error(f"[初始化错误] 初始化表情包时发生未预期错误 ({self.filename}): {str(e)}")
            logger.error(traceback.format_exc())
            self.is_deleted = True
            return None

    async def register_to_db(self) -> bool:
        """
        注册表情包
        将表情包对应的文件，从当前路径移动到EMOJI_REGISTERED_DIR目录下
        并修改对应的实例属性，然后将表情包信息保存到数据库中
        """
        try:
            # 确保目标目录存在

            # 源路径是当前实例的完整路径 self.full_path
            source_full_path = self.full_path
            # 目标完整路径
            destination_full_path = os.path.join(EMOJI_REGISTERED_DIR, self.filename)

            # 检查源文件是否存在
            if not os.path.exists(source_full_path):
                logger.error(f"[错误] 源文件不存在: {source_full_path}")
                return False

            # --- 文件移动 ---
            try:
                # 如果目标文件已存在，先删除 (确保移动成功)
                if os.path.exists(destination_full_path):
                    os.remove(destination_full_path)

                os.rename(source_full_path, destination_full_path)
                logger.debug(f"[移动] 文件从 {source_full_path} 移动到 {destination_full_path}")
                # 更新实例的路径属性为新路径
                self.full_path = destination_full_path
                self.path = EMOJI_REGISTERED_DIR
                # self.filename 保持不变
            except Exception as move_error:
                logger.error(f"[错误] 移动文件失败: {str(move_error)}")
                # 如果移动失败，尝试将实例状态恢复？暂时不处理，仅返回失败
                return False

            # --- 数据库操作 ---
            try:
                # 准备数据库记录 for emoji collection
                emotion_str = ",".join(self.emotion) if self.emotion else ""

                Emoji.create(
                    emoji_hash=self.hash,
                    full_path=self.full_path,
                    format=self.format,
                    description=self.description,
                    emotion=emotion_str,  # Store as comma-separated string
                    query_count=0,  # Default value
                    is_registered=True,
                    is_banned=False,  # Default value
                    record_time=self.register_time,  # Use MaiEmoji's register_time for DB record_time
                    register_time=self.register_time,
                    usage_count=self.usage_count,
                    last_used_time=self.last_used_time,
                )

                logger.info(f"[注册] 表情包信息保存到数据库: {self.filename} ({self.emotion})")

                return True

            except Exception as db_error:
                logger.error(f"[错误] 保存数据库失败 ({self.filename}): {str(db_error)}")
                return False

        except Exception as e:
            logger.error(f"[错误] 注册表情包失败 ({self.filename}): {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def delete(self) -> bool:
        """删除表情包

        删除表情包的文件和数据库记录

        返回:
            bool: 是否成功删除
        """
        try:
            # 1. 删除文件
            file_to_delete = self.full_path
            if os.path.exists(file_to_delete):
                try:
                    os.remove(file_to_delete)
                    logger.debug(f"[删除] 文件: {file_to_delete}")
                except Exception as e:
                    logger.error(f"[错误] 删除文件失败 {file_to_delete}: {str(e)}")
                    # 文件删除失败，但仍然尝试删除数据库记录

            # 2. 删除数据库记录
            try:
                will_delete_emoji = Emoji.get(Emoji.emoji_hash == self.hash)
                result = will_delete_emoji.delete_instance()  # Returns the number of rows deleted.
            except Emoji.DoesNotExist:  # type: ignore
                logger.warning(f"[删除] 数据库中未找到哈希值为 {self.hash} 的表情包记录。")
                result = 0  # Indicate no DB record was deleted

            if result > 0:
                logger.info(f"[删除] 表情包数据库记录 {self.filename} (Hash: {self.hash})")
                # 3. 标记对象已被删除
                self.is_deleted = True
                return True
            else:
                # 如果数据库记录删除失败，但文件可能已删除，记录一个警告
                if not os.path.exists(file_to_delete):
                    logger.warning(
                        f"[警告] 表情包文件 {file_to_delete} 已删除，但数据库记录删除失败 (Hash: {self.hash})"
                    )
                else:
                    logger.error(f"[错误] 删除表情包数据库记录失败: {self.hash}")
                return False

        except Exception as e:
            logger.error(f"[错误] 删除表情包失败 ({self.filename}): {str(e)}")
            return False


def _emoji_objects_to_readable_list(emoji_objects: List["MaiEmoji"]) -> List[str]:
    """将表情包对象列表转换为可读的字符串列表

    参数:
        emoji_objects: MaiEmoji对象列表

    返回:
        list[str]: 可读的表情包信息字符串列表
    """
    emoji_info_list = []
    for i, emoji in enumerate(emoji_objects):
        # 转换时间戳为可读时间
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(emoji.register_time))
        # 构建每个表情包的信息字符串
        emoji_info = f"编号: {i + 1}\n描述: {emoji.description}\n使用次数: {emoji.usage_count}\n添加时间: {time_str}\n"
        emoji_info_list.append(emoji_info)
    return emoji_info_list


def _to_emoji_objects(data: Any) -> Tuple[List["MaiEmoji"], int]:
    emoji_objects = []
    load_errors = 0
    # data is now an iterable of Peewee Emoji model instances
    emoji_data_list = list(data)

    for emoji_data in emoji_data_list:  # emoji_data is an Emoji model instance
        full_path = emoji_data.full_path
        if not full_path:
            logger.warning(
                f"[加载错误] 数据库记录缺少 'full_path' 字段: ID {emoji_data.id if hasattr(emoji_data, 'id') else 'Unknown'}"
            )
            load_errors += 1
            continue

        try:
            emoji = MaiEmoji(full_path=full_path)

            emoji.hash = emoji_data.emoji_hash
            if not emoji.hash:
                logger.warning(f"[加载错误] 数据库记录缺少 'hash' 字段: {full_path}")
                load_errors += 1
                continue

            emoji.description = emoji_data.description
            # Deserialize emotion string from DB to list
            emoji.emotion = emoji_data.emotion.replace("，", ",").split(",") if emoji_data.emotion else []
            emoji.usage_count = emoji_data.usage_count

            db_last_used_time = emoji_data.last_used_time
            db_register_time = emoji_data.register_time

            # If last_used_time from DB is None, use MaiEmoji's initialized register_time or current time
            emoji.last_used_time = db_last_used_time if db_last_used_time is not None else emoji.register_time
            # If register_time from DB is None, use MaiEmoji's initialized register_time (which is time.time())
            emoji.register_time = db_register_time if db_register_time is not None else emoji.register_time

            emoji.format = emoji_data.format

            emoji_objects.append(emoji)

        except ValueError as ve:
            logger.error(f"[加载错误] 初始化 MaiEmoji 失败 ({full_path}): {ve}")
            load_errors += 1
        except Exception as e:
            logger.error(f"[加载错误] 处理数据库记录时出错 ({full_path}): {str(e)}")
            load_errors += 1
    return emoji_objects, load_errors


def _ensure_emoji_dir() -> None:
    """确保表情存储目录存在"""
    os.makedirs(EMOJI_DIR, exist_ok=True)
    os.makedirs(EMOJI_REGISTERED_DIR, exist_ok=True)


async def clear_temp_emoji() -> None:
    """清理临时表情包
    清理/data/emoji、/data/image和/data/images目录下的所有文件
    当目录中文件数超过100时，会全部删除
    """

    logger.info("[清理] 开始清理缓存...")

    for need_clear in (
        os.path.join(BASE_DIR, "emoji"),
        os.path.join(BASE_DIR, "image"),
        os.path.join(BASE_DIR, "images"),
    ):
        if os.path.exists(need_clear):
            files = os.listdir(need_clear)
            # 如果文件数超过100就全部删除
            if len(files) > 100:
                for filename in files:
                    file_path = os.path.join(need_clear, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        logger.debug(f"[清理] 删除: {filename}")


async def clean_unused_emojis(emoji_dir: str, emoji_objects: List["MaiEmoji"], removed_count: int) -> int:
    """清理指定目录中未被 emoji_objects 追踪的表情包文件"""
    if not os.path.exists(emoji_dir):
        logger.warning(f"[清理] 目标目录不存在，跳过清理: {emoji_dir}")
        return removed_count

    cleaned_count = 0
    try:
        # 获取内存中所有有效表情包的完整路径集合
        tracked_full_paths = {emoji.full_path for emoji in emoji_objects if not emoji.is_deleted}

        # 遍历指定目录中的所有文件
        for file_name in os.listdir(emoji_dir):
            file_full_path = os.path.join(emoji_dir, file_name)

            # 确保处理的是文件而不是子目录
            if not os.path.isfile(file_full_path):
                continue

            # 如果文件不在被追踪的集合中，则删除
            if file_full_path not in tracked_full_paths:
                try:
                    os.remove(file_full_path)
                    logger.info(f"[清理] 删除未追踪的表情包文件: {file_full_path}")
                    cleaned_count += 1
                except Exception as e:
                    logger.error(f"[错误] 删除文件时出错 ({file_full_path}): {str(e)}")

        if cleaned_count > 0:
            logger.info(f"[清理] 在目录 {emoji_dir} 中清理了 {cleaned_count} 个破损表情包。")
        else:
            logger.debug(f"[清理] 目录 {emoji_dir} 中没有需要清理的。")

    except Exception as e:
        logger.error(f"[错误] 清理未使用表情包文件时出错 ({emoji_dir}): {str(e)}")

    return removed_count + cleaned_count


class EmojiManager:
    _instance = None

    def __new__(cls) -> "EmojiManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return  # 如果已经初始化过，直接返回

        self._scan_task = None

        self.vlm = LLMRequest(model_set=model_config.model_task_config.vlm, request_type="emoji.see")
        self.llm_emotion_judge = LLMRequest(
            model_set=model_config.model_task_config.utils, request_type="emoji"
        )  

        self.emoji_num = 0
        self.emoji_num_max = global_config.emoji.max_reg_num
        self.emoji_num_max_reach_deletion = global_config.emoji.do_replace
        self.emoji_objects: list[MaiEmoji] = []  # 存储MaiEmoji对象的列表，使用类型注解明确列表元素类型

        logger.info("启动表情包管理器")

    def initialize(self) -> None:
        """初始化数据库连接和表情目录"""
        peewee_db.connect(reuse_if_open=True)
        if peewee_db.is_closed():
            raise RuntimeError("数据库连接失败")
        _ensure_emoji_dir()
        Emoji.create_table(safe=True)  # Ensures table exists
        EmojiDescriptionCache.create_table(safe=True)
        self._initialized = True

    def _ensure_db(self) -> None:
        """确保数据库已初始化"""
        if not self._initialized:
            self.initialize()
        if not self._initialized:
            raise RuntimeError("EmojiManager not initialized")

    def record_usage(self, emoji_hash: str) -> None:
        """记录表情使用次数"""
        try:
            emoji_update = Emoji.get(Emoji.emoji_hash == emoji_hash)
            emoji_update.usage_count += 1
            emoji_update.last_used_time = time.time()  # Update last used time
            emoji_update.save()  # Persist changes to DB
        except Emoji.DoesNotExist:  # type: ignore
            logger.error(f"记录表情使用失败: 未找到 hash 为 {emoji_hash} 的表情包")
        except Exception as e:
            logger.error(f"记录表情使用失败: {str(e)}")

    async def get_emoji_for_text(self, text_emotion: str) -> Optional[Tuple[str, str, str]]:
        """根据文本内容获取相关表情包
        Args:
            text_emotion: 输入的情感描述文本
        Returns:
            Optional[Tuple[str, str]]: (表情包完整文件路径, 表情包描述)，如果没有找到则返回None
        """
        try:
            self._ensure_db()
            _time_start = time.time()

            # 获取所有表情包 (从内存缓存中获取)
            all_emojis = self.emoji_objects

            if not all_emojis:
                logger.warning("内存中没有任何表情包对象")
                return None

            # 计算每个表情包与输入文本的最大情感相似度
            emoji_similarities = []
            for emoji in all_emojis:
                # 跳过已标记为删除的对象
                if emoji.is_deleted:
                    continue

                emotions = emoji.emotion
                if not emotions:
                    continue

                # 计算与每个emotion标签的相似度，取最大值
                max_similarity = 0
                best_matching_emotion = ""
                for emotion in emotions:
                    # 使用编辑距离计算相似度
                    distance = self._levenshtein_distance(text_emotion, emotion)
                    max_len = max(len(text_emotion), len(emotion))
                    similarity = 1 - (distance / max_len if max_len > 0 else 0)
                    if similarity > max_similarity:
                        max_similarity = similarity
                        best_matching_emotion = emotion

                if best_matching_emotion:
                    emoji_similarities.append((emoji, max_similarity, best_matching_emotion))

            # 按相似度降序排序
            emoji_similarities.sort(key=lambda x: x[1], reverse=True)

            # 获取前10个最相似的表情包
            top_emojis = emoji_similarities[:10] if len(emoji_similarities) > 10 else emoji_similarities

            if not top_emojis:
                logger.warning("未找到匹配的表情包")
                return None

            # 从前几个中随机选择一个
            selected_emoji, similarity, matched_emotion = random.choice(top_emojis)

            # 更新使用次数
            self.record_usage(selected_emoji.hash)

            _time_end = time.time()

            logger.info(
                f"为[{text_emotion}]找到表情包: {matched_emotion} ({selected_emoji.filename}), Similarity: {similarity:.4f}"
            )
            # 返回完整文件路径和描述
            return selected_emoji.full_path, f"[ {selected_emoji.description} ]", matched_emotion

        except Exception as e:
            logger.error(f"[错误] 获取表情包失败: {str(e)}")
            return None

    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        # sourcery skip: simplify-empty-collection-comparison, simplify-len-comparison, simplify-str-len-comparison
        """计算两个字符串的编辑距离

        Args:
            s1: 第一个字符串
            s2: 第二个字符串

        Returns:
            int: 编辑距离
        """
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    async def check_emoji_file_integrity(self) -> None:
        """检查表情包文件完整性
        遍历self.emoji_objects中的所有对象，检查文件是否存在
        如果文件已被删除，则执行对象的删除方法并从列表中移除
        """
        try:
            # if not self.emoji_objects:
            #     logger.warning("[检查] emoji_objects为空，跳过完整性检查")
            #     return

            total_count = len(self.emoji_objects)
            self.emoji_num = total_count
            removed_count = 0
            # 使用列表复制进行遍历，因为我们会在遍历过程中修改列表
            objects_to_remove = []
            for emoji in self.emoji_objects:
                try:
                    # 跳过已经标记为删除的，避免重复处理
                    if emoji.is_deleted:
                        objects_to_remove.append(emoji)  # 收集起来一次性移除
                        continue

                    # 检查文件是否存在
                    if not os.path.exists(emoji.full_path):
                        logger.warning(f"[检查] 表情包文件丢失: {emoji.full_path}")
                        # 执行表情包对象的删除方法
                        await emoji.delete()  # delete 方法现在会标记 is_deleted
                        objects_to_remove.append(emoji)  # 标记删除后，也收集起来移除
                        # 更新计数
                        self.emoji_num -= 1
                        removed_count += 1
                        continue

                    # 检查描述是否为空 (如果为空也视为无效)
                    if not emoji.description:
                        logger.warning(f"[检查] 表情包描述为空，视为无效: {emoji.filename}")
                        await emoji.delete()
                        objects_to_remove.append(emoji)
                        self.emoji_num -= 1
                        removed_count += 1
                        continue

                except Exception as item_error:
                    logger.error(f"[错误] 处理表情包记录时出错 ({emoji.filename}): {str(item_error)}")
                    # 即使出错，也尝试继续检查下一个
                    continue

            # 从 self.emoji_objects 中移除标记的对象
            if objects_to_remove:
                self.emoji_objects = [e for e in self.emoji_objects if e not in objects_to_remove]

            # 清理 EMOJI_REGISTERED_DIR 目录中未被追踪的文件
            removed_count = await clean_unused_emojis(EMOJI_REGISTERED_DIR, self.emoji_objects, removed_count)

            # 输出清理结果
            if removed_count > 0:
                logger.info(f"[清理] 已清理 {removed_count} 个失效/文件丢失的表情包记录")
                logger.info(f"[统计] 清理前记录数: {total_count} | 清理后有效记录数: {len(self.emoji_objects)}")
            else:
                logger.info(f"[检查] 已检查 {total_count} 个表情包记录，全部完好")

        except Exception as e:
            logger.error(f"[错误] 检查表情包完整性失败: {str(e)}")
            logger.error(traceback.format_exc())

    async def start_periodic_check_register(self) -> None:
        """定期检查表情包完整性和数量"""
        await self.get_all_emoji_from_db()
        while True:
            # logger.info("[扫描] 开始检查表情包完整性...")
            await self.check_emoji_file_integrity()
            await clear_temp_emoji()
            logger.info("[扫描] 开始扫描新表情包...")

            # 检查表情包目录是否存在
            if not os.path.exists(EMOJI_DIR):
                logger.warning(f"[警告] 表情包目录不存在: {EMOJI_DIR}")
                os.makedirs(EMOJI_DIR, exist_ok=True)
                logger.info(f"[创建] 已创建表情包目录: {EMOJI_DIR}")
                await asyncio.sleep(global_config.emoji.check_interval * 60)
                continue

            # 检查目录是否为空
            files = os.listdir(EMOJI_DIR)
            if not files:
                logger.warning(f"[警告] 表情包目录为空: {EMOJI_DIR}")
                await asyncio.sleep(global_config.emoji.check_interval * 60)
                continue

            # 检查是否需要处理表情包(数量超过最大值或不足)
            if global_config.emoji.steal_emoji and (
                (self.emoji_num > self.emoji_num_max and global_config.emoji.do_replace)
                or (self.emoji_num < self.emoji_num_max)
            ):
                try:
                    # 获取目录下所有图片文件
                    files_to_process = [
                        f
                        for f in files
                        if os.path.isfile(os.path.join(EMOJI_DIR, f))
                        and f.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))
                    ]

                    # 处理每个符合条件的文件
                    for filename in files_to_process:
                        # 尝试注册表情包
                        success = await self.register_emoji_by_filename(filename)
                        if success:
                            # 注册成功则跳出循环
                            break

                        # 注册失败则删除对应文件
                        file_path = os.path.join(EMOJI_DIR, filename)
                        os.remove(file_path)
                        logger.warning(f"[清理] 删除注册失败的表情包文件: {filename}")
                except Exception as e:
                    logger.error(f"[错误] 扫描表情包目录失败: {str(e)}")

            await asyncio.sleep(global_config.emoji.check_interval * 60)

    async def get_all_emoji_from_db(self) -> None:
        """获取所有表情包并初始化为MaiEmoji类对象，更新 self.emoji_objects"""
        try:
            self._ensure_db()
            logger.debug("[数据库] 开始加载所有表情包记录 (Peewee)...")

            emoji_peewee_instances = Emoji.select()
            emoji_objects, load_errors = _to_emoji_objects(emoji_peewee_instances)

            # 更新内存中的列表和数量
            self.emoji_objects = emoji_objects
            self.emoji_num = len(emoji_objects)

            logger.info(f"[数据库] 加载完成: 共加载 {self.emoji_num} 个表情包记录。")
            if load_errors > 0:
                logger.warning(f"[数据库] 加载过程中出现 {load_errors} 个错误。")

        except Exception as e:
            logger.error(f"[错误] 从数据库加载所有表情包对象失败: {str(e)}")
            self.emoji_objects = []  # 加载失败则清空列表
            self.emoji_num = 0

    async def get_emoji_from_db(self, emoji_hash: Optional[str] = None) -> List["MaiEmoji"]:
        """获取指定哈希值的表情包并初始化为MaiEmoji类对象列表 (主要用于调试或特定查找)

        参数:
            emoji_hash: 可选，如果提供则只返回指定哈希值的表情包

        返回:
            list[MaiEmoji]: 表情包对象列表
        """
        try:
            self._ensure_db()

            if emoji_hash:
                query = Emoji.select().where(Emoji.emoji_hash == emoji_hash)
            else:
                logger.warning(
                    "[查询] 未提供 hash，将尝试加载所有表情包，建议使用 get_all_emoji_from_db 更新管理器状态。"
                )
                query = Emoji.select()

            emoji_peewee_instances = query
            emoji_objects, load_errors = _to_emoji_objects(emoji_peewee_instances)

            if load_errors > 0:
                logger.warning(f"[查询] 加载过程中出现 {load_errors} 个错误。")

            return emoji_objects

        except Exception as e:
            logger.error(f"[错误] 从数据库获取表情包对象失败: {str(e)}")
            return []

    async def get_emoji_from_manager(self, emoji_hash: str) -> Optional["MaiEmoji"]:
        # sourcery skip: use-next
        """从内存中的 emoji_objects 列表获取表情包

        参数:
            emoji_hash: 要查找的表情包哈希值
        返回:
            MaiEmoji 或 None: 如果找到则返回 MaiEmoji 对象，否则返回 None
        """
        for emoji in self.emoji_objects:
            # 确保对象未被标记为删除且哈希值匹配
            if not emoji.is_deleted and emoji.hash == emoji_hash:
                return emoji
        return None  # 如果循环结束还没找到，则返回 None

    async def get_emoji_tag_by_hash(self, emoji_hash: str) -> Optional[List[str]]:
        """根据哈希值获取已注册表情包的情感标签列表

        Args:
            emoji_hash: 表情包的哈希值

        Returns:
            Optional[List[str]]: 情感标签列表，如果未找到则返回None
        """
        try:
            # 先从内存中查找
            emoji = await self.get_emoji_from_manager(emoji_hash)
            if emoji and emoji.emotion:
                logger.info(f"[缓存命中] 从内存获取表情包情感标签: {emoji.emotion}...")
                return emoji.emotion

            # 如果内存中没有，从数据库查找
            self._ensure_db()
            try:
                emoji_record = Emoji.get_or_none(Emoji.emoji_hash == emoji_hash)
                if emoji_record and emoji_record.emotion:
                    logger.info(f"[缓存命中] 从数据库获取表情包情感标签: {emoji_record.emotion[:50]}...")
                    return emoji_record.emotion.replace("，", ",").split(",")
            except Exception as e:
                logger.error(f"从数据库查询表情包情感标签时出错: {e}")

            return None

        except Exception as e:
            logger.error(f"获取表情包情感标签失败 (Hash: {emoji_hash}): {str(e)}")
            return None

    async def get_emoji_description_by_hash(self, emoji_hash: str) -> Optional[str]:
        """根据哈希值获取已注册表情包的描述

        Args:
            emoji_hash: 表情包的哈希值

        Returns:
            Optional[str]: 表情包描述，如果未找到则返回None
        """
        try:
            # 先从内存中查找
            emoji = await self.get_emoji_from_manager(emoji_hash)
            if emoji and emoji.description:
                logger.info(f"[缓存命中] 从内存获取表情包描述: {emoji.description[:50]}...")
                return emoji.description

            # 如果内存中没有，从数据库查找
            self._ensure_db()
            try:
                emoji_record = Emoji.get_or_none(Emoji.emoji_hash == emoji_hash)
                if emoji_record and emoji_record.description:
                    logger.info(f"[缓存命中] 从数据库获取表情包描述: {emoji_record.description[:50]}...")
                    return emoji_record.description
            except Exception as e:
                logger.error(f"从数据库查询表情包描述时出错: {e}")

            return None

        except Exception as e:
            logger.error(f"获取表情包描述失败 (Hash: {emoji_hash}): {str(e)}")
            return None

    async def delete_emoji(self, emoji_hash: str) -> bool:
        """根据哈希值删除表情包

        Args:
            emoji_hash: 表情包的哈希值

        Returns:
            bool: 是否成功删除
        """
        try:
            self._ensure_db()

            # 从emoji_objects中查找表情包对象
            emoji = await self.get_emoji_from_manager(emoji_hash)

            if not emoji:
                logger.warning(f"[警告] 未找到哈希值为 {emoji_hash} 的表情包")
                return False

            # 使用MaiEmoji对象的delete方法删除表情包
            success = await emoji.delete()

            if success:
                # 从emoji_objects列表中移除该对象
                self.emoji_objects = [e for e in self.emoji_objects if e.hash != emoji_hash]
                # 更新计数
                self.emoji_num -= 1
                logger.info(f"[统计] 当前表情包数量: {self.emoji_num}")

                return True
            else:
                logger.error(f"[错误] 删除表情包失败: {emoji_hash}")
                return False

        except Exception as e:
            logger.error(f"[错误] 删除表情包失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def replace_a_emoji(self, new_emoji: "MaiEmoji") -> bool:
        # sourcery skip: use-getitem-for-re-match-groups
        """替换一个表情包

        Args:
            new_emoji: 新表情包对象

        Returns:
            bool: 是否成功替换表情包
        """
        try:
            self._ensure_db()

            # 获取所有表情包对象
            emoji_objects = self.emoji_objects
            # 计算每个表情包的选择概率
            probabilities = [1 / (emoji.usage_count + 1) for emoji in emoji_objects]
            # 归一化概率，确保总和为1
            total_probability = sum(probabilities)
            normalized_probabilities = [p / total_probability for p in probabilities]

            # 使用概率分布选择最多20个表情包
            selected_emojis = random.choices(
                emoji_objects, weights=normalized_probabilities, k=min(MAX_EMOJI_FOR_PROMPT, len(emoji_objects))
            )

            # 将表情包信息转换为可读的字符串
            emoji_info_list = _emoji_objects_to_readable_list(selected_emojis)

            # 构建提示词
            prompt = (
                f"{global_config.bot.nickname}的表情包存储已满({self.emoji_num}/{self.emoji_num_max})，"
                f"需要决定是否删除一个旧表情包来为新表情包腾出空间。\n\n"
                f"新表情包信息：\n"
                f"描述: {new_emoji.description}\n\n"
                f"现有表情包列表：\n" + "\n".join(emoji_info_list) + "\n\n"
                "请决定：\n"
                "1. 是否要删除某个现有表情包来为新表情包腾出空间？\n"
                "2. 如果要删除，应该删除哪一个(给出编号)？\n"
                "请只回答：'不删除'或'删除编号X'(X为表情包编号)。"
            )

            # 调用大模型进行决策
            decision, _ = await self.llm_emotion_judge.generate_response_async(prompt, temperature=0.8, max_tokens=600)
            logger.info(f"[决策] 结果: {decision}")

            # 解析决策结果
            if "不删除" in decision:
                logger.info("[决策] 不删除任何表情包")
                return False

            if match := re.search(r"删除编号(\d+)", decision):
                emoji_index = int(match.group(1)) - 1  # 转换为0-based索引

                # 检查索引是否有效
                if 0 <= emoji_index < len(selected_emojis):
                    emoji_to_delete = selected_emojis[emoji_index]

                    # 删除选定的表情包
                    logger.info(f"[决策] 删除表情包: {emoji_to_delete.description}")
                    delete_success = await self.delete_emoji(emoji_to_delete.hash)

                    if delete_success:
                        # 修复：等待异步注册完成
                        register_success = await new_emoji.register_to_db()
                        if register_success:
                            self.emoji_objects.append(new_emoji)
                            self.emoji_num += 1
                            logger.info(f"[成功] 注册: {new_emoji.filename}")
                            return True
                        else:
                            logger.error(f"[错误] 注册表情包到数据库失败: {new_emoji.filename}")
                            return False
                    else:
                        logger.error("[错误] 删除表情包失败，无法完成替换")
                        return False
                else:
                    logger.error(f"[错误] 无效的表情包编号: {emoji_index + 1}")
            else:
                logger.error(f"[错误] 无法从决策中提取表情包编号: {decision}")

            return False

        except Exception as e:
            logger.error(f"[错误] 替换表情包失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def build_emoji_description(self, image_base64: str) -> Tuple[str, List[str]]:
        """获取表情包描述和情感列表，优化复用已有描述

        Args:
            image_base64: 图片的base64编码

        Returns:
            Tuple[str, list]: 返回表情包描述和情感列表
        """
        try:
            # 解码图片并获取格式
            # 确保base64字符串只包含ASCII字符
            if isinstance(image_base64, str):
                image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()  # type: ignore

            # 尝试从 EmojiDescriptionCache 表获取已有的详细描述
            existing_description = None
            try:
                cache_record = EmojiDescriptionCache.get_or_none(EmojiDescriptionCache.emoji_hash == image_hash)
                if cache_record and cache_record.description:
                    existing_description = cache_record.description
                    logger.info(f"[复用描述] 表情描述缓存命中: {existing_description[:50]}...")
            except Exception as e:
                logger.debug(f"查询表情描述缓存时出错: {e}")

            # 第一步：VLM视觉分析（如果没有已有描述才调用）
            if existing_description:
                description = existing_description
                logger.info("[优化] 复用已有的详细描述，跳过VLM调用")
            else:
                logger.info("[VLM分析] 生成新的详细描述")
                if image_format in ["gif", "GIF"]:
                    image_base64 = get_image_manager().transform_gif(image_base64)  # type: ignore
                    if not image_base64:
                        raise RuntimeError("GIF表情包转换失败")
                    prompt = "这是一个动态图表情包，每一张图代表了动态图的某一帧，黑色背景代表透明，简短描述一下表情包表达的情感和内容，从互联网梗,meme的角度去分析，精简回答"
                    description, _ = await self.vlm.generate_response_for_image(
                        prompt, image_base64, "jpg", temperature=0.5
                    )
                else:
                    prompt = "这是一个表情包，请详细描述一下表情包所表达的情感和内容，简短描述细节，从互联网梗,meme的角度去分析，精简回答"
                    description, _ = await self.vlm.generate_response_for_image(
                        prompt, image_base64, image_format, temperature=0.5
                    )

            # 若是新生成的描述，写入缓存表（此时还没有情感标签，稍后会更新）
            if not existing_description:
                try:
                    cache_record, created = EmojiDescriptionCache.get_or_create(
                        emoji_hash=image_hash,
                        defaults={"description": description, "timestamp": time.time()},
                    )
                    if not created:
                        # 更新描述，但保留已有的情感标签（如果有）
                        cache_record.description = description
                        cache_record.timestamp = time.time()
                        cache_record.save()
                except Exception as cache_error:
                    logger.debug(f"写入表情描述缓存失败: {cache_error}")

            # 审核表情包
            if global_config.emoji.content_filtration:
                prompt = f'''
                    这是一个表情包，请对这个表情包进行审核，标准如下：
                    1. 必须符合"{global_config.emoji.filtration_prompt}"的要求
                    2. 不能是色情、暴力、等违法违规内容，必须符合公序良俗
                    3. 不能是任何形式的截图，聊天记录或视频截图
                    4. 不要出现5个以上文字
                    请回答这个表情包是否满足上述要求，是则回答是，否则回答否，不要出现任何其他内容
                '''
                content, _ = await self.vlm.generate_response_for_image(
                    prompt, image_base64, image_format, temperature=0.3, max_tokens=1000
                )
                if content == "否":
                    return "", []

            # 第二步：LLM情感分析 - 基于详细描述生成情感标签列表
            emotion_prompt = f"""
这是一个聊天场景中的表情包描述：'{description}'

请你识别这个表情包的含义和适用场景，给我简短的描述，每个描述不要超过15个字
你可以关注其幽默和讽刺意味，动用贴吧，微博，小红书的知识，必须从互联网梗,meme的角度去分析
请直接输出描述，不要出现任何其他内容，如果有多个描述，可以用逗号分隔
            """
            emotions_text, _ = await self.llm_emotion_judge.generate_response_async(
                emotion_prompt, temperature=0.7, max_tokens=256
            )

            # 处理情感列表
            emotions = [e.strip() for e in emotions_text.replace("，", ",").split(",") if e.strip()]

            # 根据情感标签数量随机选择 - 超过5个选3个，超过2个选2个
            if len(emotions) > 5:
                emotions = random.sample(emotions, 3)
            elif len(emotions) > 2:
                emotions = random.sample(emotions, 2)

            logger.info(f"[注册分析] 详细描述: {description[:50]}... -> 情感标签: {emotions}")

            # 将情感标签列表转换为逗号分隔的字符串
            emotion_tags_str = ",".join(emotions)

            # 更新EmojiDescriptionCache，保存情感标签
            try:
                cache_record = EmojiDescriptionCache.get_or_none(EmojiDescriptionCache.emoji_hash == image_hash)
                if cache_record:
                    # 更新已有记录的情感标签
                    cache_record.emotion_tags = emotion_tags_str
                    cache_record.timestamp = time.time()
                    cache_record.save()
                    logger.info(f"[缓存更新] 表情包情感标签已更新到EmojiDescriptionCache: {image_hash[:8]}...")
                else:
                    # 如果缓存不存在，创建新记录（包含描述和情感标签）
                    EmojiDescriptionCache.create(
                        emoji_hash=image_hash,
                        description=description,
                        emotion_tags=emotion_tags_str,
                        timestamp=time.time(),
                    )
                    logger.info(f"[缓存创建] 表情包描述和情感标签已保存到EmojiDescriptionCache: {image_hash[:8]}...")
            except Exception as cache_error:
                logger.debug(f"更新表情包情感标签缓存失败: {cache_error}")

            return f"[表情包：{description}]", emotions

        except Exception as e:
            logger.error(f"获取表情包描述失败: {str(e)}")
            return "", []

    async def register_emoji_by_filename(self, filename: str) -> bool:
        """读取指定文件名的表情包图片，分析并注册到数据库

        Args:
            filename: 表情包文件名，必须位于EMOJI_DIR目录下

        Returns:
            bool: 注册是否成功
        """
        file_full_path = os.path.join(EMOJI_DIR, filename)
        if not os.path.exists(file_full_path):
            logger.error(f"[注册失败] 文件不存在: {file_full_path}")
            return False

        try:
            # 1. 创建 MaiEmoji 实例并初始化哈希和格式
            new_emoji = MaiEmoji(full_path=file_full_path)
            init_result = await new_emoji.initialize_hash_format()
            if init_result is None or new_emoji.is_deleted:  # 初始化失败或文件读取错误
                logger.error(f"[注册失败] 初始化哈希和格式失败: {filename}")
                # 是否需要删除源文件？看业务需求，暂时不删
                return False

            # 2. 检查哈希是否已存在 (在内存中检查)
            if await self.get_emoji_from_manager(new_emoji.hash):
                logger.warning(f"[注册跳过] 表情包已存在 (Hash: {new_emoji.hash}): {filename}")
                # 删除重复的源文件
                try:
                    os.remove(file_full_path)
                    logger.info(f"[清理] 删除重复的待注册文件: {filename}")
                except Exception as e:
                    logger.error(f"[错误] 删除重复文件失败: {str(e)}")
                return False  # 返回 False 表示未注册新表情

            # 3. 构建描述和情感
            try:
                emoji_base64 = image_path_to_base64(file_full_path)
                if emoji_base64 is None:  # 再次检查读取
                    logger.error(f"[注册失败] 无法读取图片以生成描述: {filename}")
                    return False
                description, emotions = await self.build_emoji_description(emoji_base64)
                if not description:  # 检查描述是否成功生成或审核通过
                    logger.warning(f"[注册失败] 未能生成有效描述或审核未通过: {filename}")
                    # 删除未能生成描述的文件
                    try:
                        os.remove(file_full_path)
                        logger.info(f"[清理] 删除描述生成失败的文件: {filename}")
                    except Exception as e:
                        logger.error(f"[错误] 删除描述生成失败文件时出错: {str(e)}")
                    return False
                new_emoji.description = description
                new_emoji.emotion = emotions
            except Exception as build_desc_error:
                logger.error(f"[注册失败] 生成描述/情感时出错 ({filename}): {build_desc_error}")
                # 同样考虑删除文件
                try:
                    os.remove(file_full_path)
                    logger.info(f"[清理] 删除描述生成异常的文件: {filename}")
                except Exception as e:
                    logger.error(f"[错误] 删除描述生成异常文件时出错: {str(e)}")
                return False

            # 4. 检查容量并决定是否替换或直接注册
            if self.emoji_num >= self.emoji_num_max:
                logger.warning(f"表情包数量已达到上限({self.emoji_num}/{self.emoji_num_max})，尝试替换...")
                replaced = await self.replace_a_emoji(new_emoji)
                if not replaced:
                    logger.error("[注册失败] 替换表情包失败，无法完成注册")
                    # 替换失败，删除新表情包文件
                    try:
                        os.remove(file_full_path)  # new_emoji 的 full_path 此时还是源路径
                        logger.info(f"[清理] 删除替换失败的新表情文件: {filename}")
                    except Exception as e:
                        logger.error(f"[错误] 删除替换失败文件时出错: {str(e)}")
                    return False
                # 替换成功时，replace_a_emoji 内部已处理 new_emoji 的注册和添加到列表
                return True
            else:
                # 直接注册
                register_success = await new_emoji.register_to_db()  # 此方法会移动文件并更新 DB
                if register_success:
                    # 注册成功后，添加到内存列表
                    self.emoji_objects.append(new_emoji)
                    self.emoji_num += 1
                    logger.info(f"[成功] 注册新表情包: {filename} (当前: {self.emoji_num}/{self.emoji_num_max})")
                    return True
                else:
                    logger.error(f"[注册失败] 保存表情包到数据库/移动文件失败: {filename}")
                    # register_to_db 失败时，内部会尝试清理移动后的文件，源文件可能还在
                    # 是否需要删除源文件？
                    if os.path.exists(file_full_path):
                        try:
                            os.remove(file_full_path)
                            logger.info(f"[清理] 删除注册失败的源文件: {filename}")
                        except Exception as e:
                            logger.error(f"[错误] 删除注册失败源文件时出错: {str(e)}")
                    return False

        except Exception as e:
            logger.error(f"[错误] 注册表情包时发生未预期错误 ({filename}): {str(e)}")
            logger.error(traceback.format_exc())
            # 尝试删除源文件以避免循环处理
            if os.path.exists(file_full_path):
                try:
                    os.remove(file_full_path)
                    logger.info(f"[清理] 删除处理异常的源文件: {filename}")
                except Exception as remove_error:
                    logger.error(f"[错误] 删除异常处理文件时出错: {remove_error}")
            return False


emoji_manager = None


def get_emoji_manager():
    global emoji_manager
    if emoji_manager is None:
        emoji_manager = EmojiManager()
    return emoji_manager
