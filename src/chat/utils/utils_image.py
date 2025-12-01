import base64
import os
import time
import hashlib
import uuid
import io
import numpy as np

from typing import Optional, Tuple
from PIL import Image
from rich.traceback import install

from src.common.logger import get_logger
from src.common.database.database import db
from src.common.database.database_model import Images, ImageDescriptions, EmojiDescriptionCache
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

install(extra_lines=3)

logger = get_logger("chat_image")


class ImageManager:
    _instance = None
    IMAGE_DIR = "data"  # 图像存储根目录

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._ensure_image_dir()

            self._initialized = True
            self.vlm = LLMRequest(model_set=model_config.model_task_config.vlm, request_type="image")

            try:
                db.connect(reuse_if_open=True)
                db.create_tables([Images, ImageDescriptions, EmojiDescriptionCache], safe=True)
            except Exception as e:
                logger.error(f"数据库连接或表创建失败: {e}")

            try:
                self._cleanup_invalid_descriptions()
            except Exception as e:
                logger.warning(f"数据库清理失败: {e}")

            try:
                self._cleanup_emoji_from_image_descriptions()
            except Exception as e:
                logger.warning(f"清理ImageDescriptions中的emoji记录失败: {e}")

            self._initialized = True

    def _ensure_image_dir(self):
        """确保图像存储目录存在"""
        os.makedirs(self.IMAGE_DIR, exist_ok=True)

    @staticmethod
    def _get_description_from_db(image_hash: str, description_type: str) -> Optional[str]:
        """从数据库获取图片描述

        Args:
            image_hash: 图片哈希值
            description_type: 描述类型 ('emoji' 或 'image')

        Returns:
            Optional[str]: 描述文本，如果不存在则返回None
        """
        try:
            record = ImageDescriptions.get_or_none(
                (ImageDescriptions.image_description_hash == image_hash) & (ImageDescriptions.type == description_type)
            )
            return record.description if record else None
        except Exception as e:
            logger.error(f"从数据库获取描述失败 (Peewee): {str(e)}")
            return None

    @staticmethod
    def _save_description_to_db(image_hash: str, description: str, description_type: str) -> None:
        """保存图片描述到数据库

        Args:
            image_hash: 图片哈希值
            description: 描述文本
            description_type: 描述类型 ('emoji' 或 'image')
        """
        try:
            current_timestamp = time.time()
            defaults = {"description": description, "timestamp": current_timestamp}
            desc_obj, created = ImageDescriptions.get_or_create(
                image_description_hash=image_hash, type=description_type, defaults=defaults
            )
            if not created:  # 如果记录已存在，则更新
                desc_obj.description = description
                desc_obj.timestamp = current_timestamp
                desc_obj.save()
        except Exception as e:
            logger.error(f"保存描述到数据库失败 (Peewee): {str(e)}")

    @staticmethod
    def _cleanup_invalid_descriptions():
        """清理数据库中 description 为空或为 'None' 的记录"""
        invalid_values = ["", "None"]

        # 清理 Images 表
        deleted_images = (
            Images.delete().where((Images.description >> None) | (Images.description << invalid_values)).execute()
        )

        # 清理 ImageDescriptions 表
        deleted_descriptions = (
            ImageDescriptions.delete()
            .where((ImageDescriptions.description >> None) | (ImageDescriptions.description << invalid_values))
            .execute()
        )

        if deleted_images or deleted_descriptions:
            logger.info(f"[清理完成] 删除 Images: {deleted_images} 条, ImageDescriptions: {deleted_descriptions} 条")
        else:
            logger.info("[清理完成] 未发现无效描述记录")

    @staticmethod
    def _cleanup_emoji_from_image_descriptions():
        """清理Images和ImageDescriptions表中type为emoji的记录（已迁移到EmojiDescriptionCache）"""
        try:
            # 清理Images表中type为emoji的记录
            deleted_images = Images.delete().where(Images.type == "emoji").execute()
            
            # 清理ImageDescriptions表中type为emoji的记录
            deleted_descriptions = (
                ImageDescriptions.delete().where(ImageDescriptions.type == "emoji").execute()
            )
            
            total_deleted = deleted_images + deleted_descriptions
            if total_deleted > 0:
                logger.info(
                    f"[清理完成] 从Images表中删除 {deleted_images} 条emoji类型记录, "
                    f"从ImageDescriptions表中删除 {deleted_descriptions} 条emoji类型记录, "
                    f"共删除 {total_deleted} 条记录"
                )
            else:
                logger.info("[清理完成] Images和ImageDescriptions表中未发现emoji类型记录")
        except Exception as e:
            logger.error(f"清理Images和ImageDescriptions中的emoji记录时出错: {str(e)}")
            raise

    async def get_emoji_tag(self, image_base64: str) -> str:
        from src.chat.emoji_system.emoji_manager import get_emoji_manager

        emoji_manager = get_emoji_manager()
        if isinstance(image_base64, str):
            image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")
        image_bytes = base64.b64decode(image_base64)
        image_hash = hashlib.md5(image_bytes).hexdigest()
        emoji = await emoji_manager.get_emoji_from_manager(image_hash)
        if not emoji:
            return "[表情包：未知]"
        emotion_list = emoji.emotion
        tag_str = ",".join(emotion_list)
        return f"[表情包：{tag_str}]"

    async def _save_emoji_file_if_needed(self, image_base64: str, image_hash: str, image_format: str) -> None:
        """如果启用了steal_emoji且表情包未注册，保存文件到data/emoji目录
        
        Args:
            image_base64: 图片的base64编码
            image_hash: 图片的MD5哈希值
            image_format: 图片格式
        """
        if not global_config.emoji.steal_emoji:
            return
        
        try:
            from src.chat.emoji_system.emoji_manager import EMOJI_DIR
            from src.chat.emoji_system.emoji_manager import get_emoji_manager

            # 确保目录存在
            os.makedirs(EMOJI_DIR, exist_ok=True)

            # 检查是否已存在该表情包（通过哈希值）
            emoji_manager = get_emoji_manager()
            existing_emoji = await emoji_manager.get_emoji_from_manager(image_hash)
            if existing_emoji:
                logger.debug(f"[自动保存] 表情包已注册，跳过保存: {image_hash[:8]}...")
                return

            # 生成文件名：使用哈希值前8位 + 格式
            filename = f"{image_hash[:8]}.{image_format}"
            file_path = os.path.join(EMOJI_DIR, filename)

            # 检查文件是否已存在（可能之前保存过但未注册）
            if not os.path.exists(file_path):
                # 保存文件
                if base64_to_image(image_base64, file_path):
                    logger.info(f"[自动保存] 表情包已保存到 {file_path} (Hash: {image_hash[:8]}...)")
                else:
                    logger.warning(f"[自动保存] 保存表情包文件失败: {file_path}")
            else:
                logger.debug(f"[自动保存] 表情包文件已存在，跳过: {file_path}")
        except Exception as save_error:
            logger.warning(f"[自动保存] 保存表情包文件时出错: {save_error}")

    async def get_emoji_description(self, image_base64: str) -> str:
        """获取表情包描述，优先使用EmojiDescriptionCache表中的缓存数据"""
        try:
            # 计算图片哈希
            # 确保base64字符串只包含ASCII字符
            if isinstance(image_base64, str):
                image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()  # type: ignore

            # 优先使用EmojiManager查询已注册表情包的描述
            try:
                from src.chat.emoji_system.emoji_manager import get_emoji_manager

                emoji_manager = get_emoji_manager()
                tags = await emoji_manager.get_emoji_tag_by_hash(image_hash)
                if tags:
                    tag_str = ",".join(tags)
                    logger.info(f"[缓存命中] 使用已注册表情包描述: {tag_str}...")
                    return f"[表情包：{tag_str}]"
            except Exception as e:
                logger.debug(f"查询EmojiManager时出错: {e}")

            # 查询EmojiDescriptionCache表的缓存（包含描述和情感标签）
            try:
                cache_record = EmojiDescriptionCache.get_or_none(EmojiDescriptionCache.emoji_hash == image_hash)
                if cache_record:
                    # 优先使用情感标签，如果没有则使用详细描述
                    result_text = ""
                    if cache_record.emotion_tags:
                        logger.info(f"[缓存命中] 使用EmojiDescriptionCache表中的情感标签: {cache_record.emotion_tags[:50]}...")
                        result_text = f"[表情包：{cache_record.emotion_tags}]"
                    elif cache_record.description:
                        logger.info(f"[缓存命中] 使用EmojiDescriptionCache表中的描述: {cache_record.description[:50]}...")
                        result_text = f"[表情包：{cache_record.description}]"
                    
                    # 即使缓存命中，如果启用了steal_emoji，也检查是否需要保存文件
                    if result_text:
                        await self._save_emoji_file_if_needed(image_base64, image_hash, image_format)
                        return result_text
            except Exception as e:
                logger.debug(f"查询EmojiDescriptionCache时出错: {e}")

            # === 二步走识别流程 ===

            # 第一步：VLM视觉分析 - 生成详细描述
            if image_format in ["gif", "GIF"]:
                image_base64_processed = self.transform_gif(image_base64)
                if image_base64_processed is None:
                    logger.warning("GIF转换失败，无法获取描述")
                    return "[表情包(GIF处理失败)]"
                vlm_prompt = "这是一个动态图表情包，每一张图代表了动态图的某一帧，黑色背景代表透明，描述一下表情包表达的情感和内容，描述细节，从互联网梗,meme的角度去分析"
                detailed_description, _ = await self.vlm.generate_response_for_image(
                    vlm_prompt, image_base64_processed, "jpg", temperature=0.4
                )
            else:
                vlm_prompt = (
                    "这是一个表情包，请详细描述一下表情包所表达的情感和内容，描述细节，从互联网梗,meme的角度去分析"
                )
                detailed_description, _ = await self.vlm.generate_response_for_image(
                    vlm_prompt, image_base64, image_format, temperature=0.4
                )

            if detailed_description is None:
                logger.warning("VLM未能生成表情包详细描述")
                return "[表情包(VLM描述生成失败)]"

            # 第二步：LLM情感分析 - 基于详细描述生成简短的情感标签
            emotion_prompt = f"""
            请你基于这个表情包的详细描述，提取出最核心的情感含义，用1-2个词概括。
            详细描述：'{detailed_description}'
            
            要求：
            1. 只输出1-2个最核心的情感词汇
            2. 从互联网梗、meme的角度理解
            3. 输出简短精准，不要解释
            4. 如果有多个词用逗号分隔
            """

            # 使用较低温度确保输出稳定
            emotion_llm = LLMRequest(model_set=model_config.model_task_config.utils, request_type="emoji")
            emotion_result, _ = await emotion_llm.generate_response_async(emotion_prompt, temperature=0.3)

            if not emotion_result:
                logger.warning("LLM未能生成情感标签，使用详细描述的前几个词")
                # 降级处理：从详细描述中提取关键词
                import jieba

                words = list(jieba.cut(detailed_description))
                emotion_result = "，".join(words[:2]) if len(words) >= 2 else (words[0] if words else "表情")

            # 处理情感结果，取前1-2个最重要的标签
            emotions = [e.strip() for e in emotion_result.replace("，", ",").split(",") if e.strip()]
            final_emotion = emotions[0] if emotions else "表情"

            # 如果有第二个情感且不重复，也包含进来
            if len(emotions) > 1 and emotions[1] != emotions[0]:
                final_emotion = f"{emotions[0]}，{emotions[1]}"

            logger.debug(f"[emoji识别] 详细描述: {detailed_description[:50]}... -> 情感标签: {final_emotion}")

            # 再次检查缓存（防止并发情况下其他线程已经保存）
            try:
                cache_record = EmojiDescriptionCache.get_or_none(EmojiDescriptionCache.emoji_hash == image_hash)
                if cache_record and cache_record.emotion_tags:
                    logger.warning(f"虽然生成了描述，但是找到缓存表情包情感标签: {cache_record.emotion_tags}")
                    return f"[表情包：{cache_record.emotion_tags}]"
            except Exception as e:
                logger.debug(f"再次查询EmojiDescriptionCache时出错: {e}")

            # 保存识别出的详细描述和情感标签到 emoji_description_cache
            try:
                current_timestamp = time.time()
                cache_record, created = EmojiDescriptionCache.get_or_create(
                    emoji_hash=image_hash,
                    defaults={
                        "description": detailed_description,
                        "emotion_tags": final_emotion,
                        "timestamp": current_timestamp,
                    },
                )
                if not created:
                    # 更新已有记录
                    cache_record.description = detailed_description
                    cache_record.emotion_tags = final_emotion
                    cache_record.timestamp = current_timestamp
                    cache_record.save()
                logger.info(f"[缓存保存] 表情包描述和情感标签已保存到EmojiDescriptionCache: {image_hash[:8]}...")
            except Exception as e:
                logger.error(f"保存表情包描述和情感标签缓存失败: {str(e)}")

            # 如果启用了steal_emoji，自动保存表情包文件到data/emoji目录
            await self._save_emoji_file_if_needed(image_base64, image_hash, image_format)

            return f"[表情包：{final_emotion}]"

        except Exception as e:
            logger.error(f"获取表情包描述失败: {str(e)}")
            return "[表情包(处理失败)]"

    async def get_image_description(self, image_base64: str) -> str:
        """获取普通图片描述，优先使用Images表中的缓存数据"""
        try:
            # 计算图片哈希
            if isinstance(image_base64, str):
                image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()

            # 优先检查Images表中是否已有完整的描述
            existing_image = Images.get_or_none(Images.emoji_hash == image_hash)
            if existing_image:
                # 更新计数
                if hasattr(existing_image, "count") and existing_image.count is not None:
                    existing_image.count += 1
                else:
                    existing_image.count = 1
                existing_image.save()

                # 如果已有描述，直接返回
                if existing_image.description:
                    logger.debug(f"[缓存命中] 使用Images表中的图片描述: {existing_image.description[:50]}...")
                    return f"[图片：{existing_image.description}]"

            if cached_description := self._get_description_from_db(image_hash, "image"):
                logger.debug(f"[缓存命中] 使用ImageDescriptions表中的描述: {cached_description[:50]}...")
                return f"[图片：{cached_description}]"

            # 调用AI获取描述
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()  # type: ignore
            prompt = global_config.personality.visual_style
            logger.info(f"[VLM调用] 为图片生成新描述 (Hash: {image_hash[:8]}...)")
            description, _ = await self.vlm.generate_response_for_image(
                prompt, image_base64, image_format, temperature=0.4
            )

            if description is None:
                logger.warning("AI未能生成图片描述")
                return "[图片(描述生成失败)]"

            # 保存图片和描述
            current_timestamp = time.time()
            filename = f"{int(current_timestamp)}_{image_hash[:8]}.{image_format}"
            image_dir = os.path.join(self.IMAGE_DIR, "image")
            os.makedirs(image_dir, exist_ok=True)
            file_path = os.path.join(image_dir, filename)

            try:
                # 保存文件
                with open(file_path, "wb") as f:
                    f.write(image_bytes)

                # 保存到数据库，补充缺失字段
                if existing_image:
                    existing_image.path = file_path
                    existing_image.description = description
                    existing_image.timestamp = current_timestamp
                    if not hasattr(existing_image, "image_id") or not existing_image.image_id:
                        existing_image.image_id = str(uuid.uuid4())
                    if not hasattr(existing_image, "vlm_processed") or existing_image.vlm_processed is None:
                        existing_image.vlm_processed = True
                    existing_image.save()
                    logger.debug(f"[数据库] 更新已有图片记录: {image_hash[:8]}...")
                else:
                    Images.create(
                        image_id=str(uuid.uuid4()),
                        emoji_hash=image_hash,
                        path=file_path,
                        type="image",
                        description=description,
                        timestamp=current_timestamp,
                        vlm_processed=True,
                        count=1,
                    )
                    logger.debug(f"[数据库] 创建新图片记录: {image_hash[:8]}...")
            except Exception as e:
                logger.error(f"保存图片文件或元数据失败: {str(e)}")

            # 保存描述到ImageDescriptions表作为备用缓存
            self._save_description_to_db(image_hash, description, "image")

            logger.info(f"[VLM完成] 图片描述生成: {description[:50]}...")
            return f"[图片：{description}]"
        except Exception as e:
            logger.error(f"获取图片描述失败: {str(e)}")
            return "[图片(处理失败)]"

    @staticmethod
    def transform_gif(gif_base64: str, similarity_threshold: float = 1000.0, max_frames: int = 15) -> Optional[str]:
        # sourcery skip: use-contextlib-suppress
        """将GIF转换为水平拼接的静态图像, 跳过相似的帧

        Args:
            gif_base64: GIF的base64编码字符串
            similarity_threshold: 判定帧相似的阈值 (MSE)，越小表示要求差异越大才算不同帧，默认1000.0
            max_frames: 最大抽取的帧数，默认15

        Returns:
            Optional[str]: 拼接后的JPG图像的base64编码字符串, 或者在失败时返回None
        """
        try:
            # 确保base64字符串只包含ASCII字符
            if isinstance(gif_base64, str):
                gif_base64 = gif_base64.encode("ascii", errors="ignore").decode("ascii")
            # 解码base64
            gif_data = base64.b64decode(gif_base64)
            gif = Image.open(io.BytesIO(gif_data))

            # 收集所有帧
            all_frames = []
            try:
                while True:
                    gif.seek(len(all_frames))
                    # 确保是RGB格式方便比较
                    frame = gif.convert("RGB")
                    all_frames.append(frame.copy())
            except EOFError:
                pass  # 读完啦

            if not all_frames:
                logger.warning("GIF中没有找到任何帧")
                return None  # 空的GIF直接返回None

            # --- 新的帧选择逻辑 ---
            selected_frames = []
            last_selected_frame_np = None

            for i, current_frame in enumerate(all_frames):
                current_frame_np = np.array(current_frame)

                # 第一帧总是要选的
                if i == 0:
                    selected_frames.append(current_frame)
                    last_selected_frame_np = current_frame_np
                    continue

                # 计算和上一张选中帧的差异（均方误差 MSE）
                if last_selected_frame_np is not None:
                    mse = np.mean((current_frame_np - last_selected_frame_np) ** 2)
                    # logger.debug(f"帧 {i} 与上一选中帧的 MSE: {mse}") # 可以取消注释来看差异值

                    # 如果差异够大，就选它！
                    if mse > similarity_threshold:
                        selected_frames.append(current_frame)
                        last_selected_frame_np = current_frame_np
                        # 检查是不是选够了
                        if len(selected_frames) >= max_frames:
                            # logger.debug(f"已选够 {max_frames} 帧，停止选择。")
                            break
                # 如果差异不大就跳过这一帧啦

            # --- 帧选择逻辑结束 ---

            # 如果选择后连一帧都没有（比如GIF只有一帧且后续处理失败？）或者原始GIF就没帧，也返回None
            if not selected_frames:
                logger.warning("处理后没有选中任何帧")
                return None

            # logger.debug(f"总帧数: {len(all_frames)}, 选中帧数: {len(selected_frames)}")

            # 获取选中的第一帧的尺寸（假设所有帧尺寸一致）
            frame_width, frame_height = selected_frames[0].size

            # 计算目标尺寸，保持宽高比
            target_height = 200  # 固定高度
            # 防止除以零
            if frame_height == 0:
                logger.error("帧高度为0，无法计算缩放尺寸")
                return None
            target_width = int((target_height / frame_height) * frame_width)
            # 宽度也不能是0
            if target_width == 0:
                logger.warning(f"计算出的目标宽度为0 (原始尺寸 {frame_width}x{frame_height})，调整为1")
                target_width = 1

            # 调整所有选中帧的大小
            resized_frames = [
                frame.resize((target_width, target_height), Image.Resampling.LANCZOS) for frame in selected_frames
            ]

            # 创建拼接图像
            total_width = target_width * len(resized_frames)
            # 防止总宽度为0
            if total_width == 0 and resized_frames:
                logger.warning("计算出的总宽度为0，但有选中帧，可能目标宽度太小")
                # 至少给点宽度吧
                total_width = len(resized_frames)
            elif total_width == 0:
                logger.error("计算出的总宽度为0且无选中帧")
                return None

            combined_image = Image.new("RGB", (total_width, target_height))

            # 水平拼接图像
            for idx, frame in enumerate(resized_frames):
                combined_image.paste(frame, (idx * target_width, 0))

            # 转换为base64
            buffer = io.BytesIO()
            combined_image.save(buffer, format="JPEG", quality=85)  # 保存为JPEG
            return base64.b64encode(buffer.getvalue()).decode("utf-8")
        except MemoryError:
            logger.error("GIF转换失败: 内存不足，可能是GIF太大或帧数太多")
            return None  # 内存不够啦
        except Exception as e:
            logger.error(f"GIF转换失败: {str(e)}", exc_info=True)  # 记录详细错误信息
            return None  # 其他错误也返回None

    async def process_image(self, image_base64: str) -> Tuple[str, str]:
        # sourcery skip: hoist-if-from-if
        """处理图片并返回图片ID和描述

        Args:
            image_base64: 图片的base64编码

        Returns:
            Tuple[str, str]: (图片ID, 描述)
        """
        try:
            # 生成图片ID
            # 计算图片哈希
            # 确保base64字符串只包含ASCII字符
            if isinstance(image_base64, str):
                image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()

            if existing_image := Images.get_or_none(Images.emoji_hash == image_hash):
                # 检查是否缺少必要字段，如果缺少则创建新记录
                if (
                    not hasattr(existing_image, "image_id")
                    or not existing_image.image_id
                    or not hasattr(existing_image, "count")
                    or existing_image.count is None
                    or not hasattr(existing_image, "vlm_processed")
                    or existing_image.vlm_processed is None
                ):
                    logger.debug(f"图片记录缺少必要字段，补全旧记录: {image_hash}")
                    if not existing_image.image_id:
                        existing_image.image_id = str(uuid.uuid4())
                    if existing_image.count is None:
                        existing_image.count = 0
                    if existing_image.vlm_processed is None:
                        existing_image.vlm_processed = False

                existing_image.count += 1
                existing_image.save()
                return existing_image.image_id, f"[picid:{existing_image.image_id}]"
            else:
                # print(f"图片不存在: {image_hash}")
                image_id = str(uuid.uuid4())

            # 保存新图片
            current_timestamp = time.time()
            image_dir = os.path.join(self.IMAGE_DIR, "images")
            os.makedirs(image_dir, exist_ok=True)
            filename = f"{image_id}.png"
            file_path = os.path.join(image_dir, filename)

            # 保存文件
            with open(file_path, "wb") as f:
                f.write(image_bytes)

            # 保存到数据库
            Images.create(
                image_id=image_id,
                emoji_hash=image_hash,
                path=file_path,
                type="image",
                timestamp=current_timestamp,
                vlm_processed=False,
                count=1,
            )

            # 启动异步VLM处理
            await self._process_image_with_vlm(image_id, image_base64)

            return image_id, f"[picid:{image_id}]"

        except Exception as e:
            logger.error(f"处理图片失败: {str(e)}")
            return "", "[图片]"

    async def _process_image_with_vlm(self, image_id: str, image_base64: str) -> None:
        """使用VLM处理图片并更新数据库

        Args:
            image_id: 图片ID
            image_base64: 图片的base64编码
        """
        try:
            # 计算图片哈希
            # 确保base64字符串只包含ASCII字符
            if isinstance(image_base64, str):
                image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()

            # 获取当前图片记录
            image = Images.get(Images.image_id == image_id)

            # 优先检查是否已有其他相同哈希的图片记录包含描述
            existing_with_description = Images.get_or_none(
                (Images.emoji_hash == image_hash) & (Images.description.is_null(False)) & (Images.description != "")
            )
            if existing_with_description and existing_with_description.id != image.id:
                logger.debug(f"[缓存复用] 从其他相同图片记录复用描述: {existing_with_description.description[:50]}...")
                image.description = existing_with_description.description
                image.vlm_processed = True
                image.save()
                # 同时保存到ImageDescriptions表作为备用缓存
                self._save_description_to_db(image_hash, existing_with_description.description, "image")
                return

            # 检查ImageDescriptions表的缓存描述
            if cached_description := self._get_description_from_db(image_hash, "image"):
                logger.debug(f"[缓存复用] 从ImageDescriptions表复用描述: {cached_description[:50]}...")
                image.description = cached_description
                image.vlm_processed = True
                image.save()
                return

            # 获取图片格式
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()  # type: ignore

            # 构建prompt
            prompt = global_config.personality.visual_style

            # 获取VLM描述
            description, _ = await self.vlm.generate_response_for_image(
                prompt, image_base64, image_format, temperature=0.4
            )

            if description is None:
                logger.warning("VLM未能生成图片描述")
                description = ""

            if cached_description := self._get_description_from_db(image_hash, "image"):
                logger.info(f"虽然生成了描述，但是找到缓存图片描述: {cached_description}")
                description = cached_description

            # 更新数据库
            image.description = description
            image.vlm_processed = True
            image.save()

            # 保存描述到ImageDescriptions表作为备用缓存
            self._save_description_to_db(image_hash, description, "image")

        except Exception as e:
            logger.error(f"VLM处理图片失败: {str(e)}")


# 创建全局单例
image_manager = None


def get_image_manager() -> ImageManager:
    """获取全局图片管理器单例"""
    global image_manager
    if image_manager is None:
        image_manager = ImageManager()
    return image_manager


def image_path_to_base64(image_path: str) -> str:
    """将图片路径转换为base64编码
    Args:
        image_path: 图片文件路径
    Returns:
        str: base64编码的图片数据
    Raises:
        FileNotFoundError: 当图片文件不存在时
        IOError: 当读取图片文件失败时
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    with open(image_path, "rb") as f:
        if image_data := f.read():
            return base64.b64encode(image_data).decode("utf-8")
        else:
            raise IOError(f"读取图片文件失败: {image_path}")


def base64_to_image(image_base64: str, output_path: str) -> bool:
    """将base64编码的图片保存为文件

    Args:
        image_base64: 图片的base64编码
        output_path: 输出文件路径

    Returns:
        bool: 是否成功保存

    Raises:
        ValueError: 当base64编码无效时
        IOError: 当保存文件失败时
    """
    try:
        # 确保base64字符串只包含ASCII字符
        if isinstance(image_base64, str):
            image_base64 = image_base64.encode("ascii", errors="ignore").decode("ascii")

        # 解码base64
        image_bytes = base64.b64decode(image_base64)

        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # 保存文件
        with open(output_path, "wb") as f:
            f.write(image_bytes)

        return True

    except Exception as e:
        logger.error(f"保存base64图片失败: {e}")
        return False
