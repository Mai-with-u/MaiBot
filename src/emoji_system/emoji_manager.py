from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

import asyncio
import hashlib
import heapq
import random
import re

from rapidfuzz.distance import Levenshtein
from rich.traceback import install
from sqlmodel import select

from src.common.data_models.image_data_model import MaiEmoji
from src.common.database.database import get_db_session, get_db_session_manual
from src.common.database.database_model import Images, ImageType
from src.common.logger import get_logger
from src.common.utils.image_path import resolve_stored_image_path, serialize_stored_image_path
from src.common.utils.utils_image import ImageUtils
from src.config.config import config_manager, global_config
from src.plugin_runtime.hook_schema_utils import build_object_schema
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.prompt.prompt_manager import prompt_manager
from src.services.llm_service import LLMServiceClient

logger = get_logger("emoji")

install(extra_lines=3)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
EmojiRegisterStatus = Literal["registered", "skipped", "failed"]
EMOJI_DIR = DATA_DIR / "emoji"  # 表情包存储目录
MAX_EMOJI_FOR_PROMPT = 20  # 最大允许的表情包描述数量于图片替换的 prompt 中
BYTES_PER_MIB = 1024 * 1024


def register_emoji_hook_specs(registry: HookSpecRegistry) -> list[HookSpec]:
    """注册表情包系统内置 Hook 规格。

    Args:
        registry: 目标 Hook 规格注册中心。

    Returns:
        List[HookSpec]: 实际注册的 Hook 规格列表。
    """

    emoji_schema = {
        "type": "object",
        "description": "当前表情包的序列化信息，主要包含 file_hash、description、emotions 等字段。",
    }
    string_array_schema = {
        "type": "array",
        "items": {"type": "string"},
    }
    return registry.register_hook_specs(
        [
            HookSpec(
                name="emoji.maisaka.before_select",
                description="Maisaka 表情发送工具选择表情前触发，可改写情绪、上下文和采样参数，或中止本次选择。",
                parameters_schema=build_object_schema(
                    {
                        "stream_id": {"type": "string", "description": "目标会话 ID。"},
                        "requested_emotion": {"type": "string", "description": "请求的目标情绪标签。"},
                        "reasoning": {"type": "string", "description": "本次发送表情的推理理由。"},
                        "context_texts": {
                            **string_array_schema,
                            "description": "最近聊天上下文文本列表。",
                        },
                        "sample_size": {"type": "integer", "description": "候选表情采样数量。"},
                        "abort_message": {
                            "type": "string",
                            "description": "当 Hook 主动中止时可附带的失败提示。",
                        },
                    },
                    required=["stream_id", "requested_emotion", "reasoning", "context_texts", "sample_size"],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="emoji.maisaka.after_select",
                description="Maisaka 已选出表情后触发，可替换选中的表情哈希、补充匹配情绪，或中止发送。",
                parameters_schema=build_object_schema(
                    {
                        "stream_id": {"type": "string", "description": "目标会话 ID。"},
                        "requested_emotion": {"type": "string", "description": "请求的目标情绪标签。"},
                        "reasoning": {"type": "string", "description": "本次发送表情的推理理由。"},
                        "context_texts": {
                            **string_array_schema,
                            "description": "最近聊天上下文文本列表。",
                        },
                        "sample_size": {"type": "integer", "description": "候选表情采样数量。"},
                        "selected_emoji": emoji_schema,
                        "selected_emoji_hash": {"type": "string", "description": "选中的表情哈希。"},
                        "matched_emotion": {"type": "string", "description": "最终命中的情绪标签。"},
                        "abort_message": {
                            "type": "string",
                            "description": "当 Hook 主动中止时可附带的失败提示。",
                        },
                    },
                    required=[
                        "stream_id",
                        "requested_emotion",
                        "reasoning",
                        "context_texts",
                        "sample_size",
                        "matched_emotion",
                    ],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
            HookSpec(
                name="emoji.register.after_build_description",
                description="表情包描述生成并通过内容审查后触发，可改写描述文本或拒绝本次注册。",
                parameters_schema=build_object_schema(
                    {
                        "emoji": emoji_schema,
                        "description": {"type": "string", "description": "当前生成出的表情包描述。"},
                        "image_format": {"type": "string", "description": "表情图片格式。"},
                    },
                    required=["emoji", "description", "image_format"],
                ),
                default_timeout_ms=5000,
                allow_abort=True,
                allow_kwargs_mutation=True,
            ),
        ]
    )


def _get_runtime_manager() -> Any:
    """获取插件运行时管理器。

    Returns:
        Any: 插件运行时管理器单例。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    return get_plugin_runtime_manager()


def _serialize_emoji_for_hook(emoji: Optional[MaiEmoji]) -> Optional[dict[str, Any]]:
    """将表情包对象序列化为 Hook 可传输载荷。

    Args:
        emoji: 待序列化的表情包对象。

    Returns:
        Optional[Dict[str, Any]]: 序列化后的字典；当表情为空时返回 ``None``。
    """

    if emoji is None:
        return None

    return {
        "file_hash": str(emoji.file_hash or "").strip(),
        "file_name": emoji.file_name,
        "full_path": str(emoji.full_path),
        "description": emoji.description,
        "emotions": [str(item).strip() for item in _normalize_emoji_tag_text(emoji.description)],
        "query_count": int(emoji.query_count),
    }


def _normalize_emoji_tag_text(raw_values: Any) -> list[str]:
    """将文本或标签列表转为去重的情绪标签列表。"""
    if isinstance(raw_values, str):
        if not raw_values:
            return []
        parts = re.split(r"[,，、;；\r\n\t]+", raw_values.strip())
        normalized_tags = [str(part).strip() for part in parts if str(part).strip()]
    elif isinstance(raw_values, list):
        normalized_tags: list[str] = []
        for value in raw_values:
            normalized_tags.extend(_normalize_emoji_tag_text(value))
    else:
        return []

    deduped_tags: list[str] = []
    seen: set[str] = set()
    for tag in normalized_tags:
        normalized_tag = tag.strip()
        if not normalized_tag:
            continue
        lowered = normalized_tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped_tags.append(normalized_tag)
    return deduped_tags


def _get_emoji_emotions(emoji: MaiEmoji) -> list[str]:
    """获取表情包情绪标签。"""
    return _normalize_emoji_tag_text(emoji.description)


def _ensure_directories() -> None:
    """确保表情包相关目录存在"""
    EMOJI_DIR.mkdir(parents=True, exist_ok=True)


def _is_available_emoji_record(record: Images) -> bool:
    """判断数据库记录对应的表情包文件当前是否可用。"""
    if record.no_file_flag:
        return False

    record_path = resolve_stored_image_path(record.full_path)
    return record_path.exists() and record_path.is_file()


def _is_known_emoji_maintenance_record(record: Images) -> bool:
    """判断维护扫描是否应把数据库记录对应文件视为已处理。"""
    return record.is_registered or record.is_banned or record.vlm_processed


def _resolve_existing_emoji_path(raw_path: str | Path | None) -> Optional[Path]:
    """将表情包路径归一化；路径为空或不存在时返回 ``None``。"""
    if not raw_path:
        return None

    record_path = resolve_stored_image_path(raw_path)
    if not record_path.exists() or not record_path.is_file():
        return None
    return record_path


def _is_vlm_task_configured() -> bool:
    """判断是否配置了可用于表情包识别和审核的视觉模型任务。"""

    try:
        vlm_models = config_manager.get_model_config().model_task_config.vlm.model_list
        return any(str(model_name).strip() for model_name in vlm_models)
    except Exception as exc:
        logger.warning(f"读取 VLM 模型配置失败，跳过表情包识别和审核: {exc}")
        return False


def _get_max_collected_emoji_bytes() -> int:
    """获取收集表情包的最大字节数；配置为 0 时不限制。"""

    max_size_mb = float(global_config.emoji.max_emoji_size_mb)
    if max_size_mb <= 0:
        return 0
    return int(max_size_mb * BYTES_PER_MIB)


def _is_collected_emoji_size_allowed(size_bytes: int) -> bool:
    """判断待收集表情包是否超过配置的大小上限。"""

    max_size_bytes = _get_max_collected_emoji_bytes()
    return max_size_bytes <= 0 or size_bytes <= max_size_bytes


emoji_manager_vlm = LLMServiceClient(task_name="vlm", request_type="emoji.see")
emoji_manager_emotion_judge_llm = LLMServiceClient(task_name="utils", request_type="emoji")


class EmojiManager:
    """
    表情包管理器
    """

    def __init__(self) -> None:
        """初始化表情包管理器。"""
        _ensure_directories()

        self._emoji_num: int = 0
        self.emojis: list[MaiEmoji] = []
        self._maintenance_wakeup_event: asyncio.Event = asyncio.Event()
        self._pending_description_tasks: dict[str, asyncio.Task[None]] = {}
        self._emoji_save_locks: dict[str, asyncio.Lock] = {}
        self._reload_callback_registered: bool = False

        config_manager.register_reload_callback(self.reload_runtime_config)
        self._reload_callback_registered = True

        logger.info("启动表情包管理器")

    def reload_runtime_config(self) -> None:
        """响应配置热重载，重置维护循环等待时间以应用最新配置。"""
        self._maintenance_wakeup_event.set()
        logger.info("[配置热重载] Emoji 模块配置已更新，将按新的检查间隔等待后执行维护")

    def shutdown(self) -> None:
        """清理 EmojiManager 生命周期资源。"""
        if not self._reload_callback_registered:
            return
        config_manager.unregister_reload_callback(self.reload_runtime_config)
        self._reload_callback_registered = False
        self._maintenance_wakeup_event.set()
        logger.info("[关闭] Emoji 模块已注销配置热重载回调")

    async def get_emoji_description(
        self,
        *,
        emoji_bytes: Optional[bytes] = None,
        emoji_hash: Optional[str] = None,
        session_id: str = "",
        wait_for_build: bool = True,
    ) -> Optional[tuple[str, list[str]]]:
        """
        根据表情包哈希获取表情包描述和情感列表的封装方法

        Args:
            emoji_bytes (Optional[bytes]): 表情包的字节数据，如果提供了字节数据但数据库中没有找到对应记录，则会尝试构建表情包描述
            emoji_hash (Optional[str]): 表情包的哈希值，如果提供了哈希值则优先使用哈希值查找表情包描述
            wait_for_build (bool): 未命中缓存时是否同步等待描述构建完成
        Returns:
            return (Optional[Tuple[str, List[str]]]): 如果找到对应的表情包，则返回包含描述和情感标签的元组；若没找到，则尝试构建表情包描述并返回，如果构建失败则返回 None
        Raises:
            ValueError: 如果既没有提供表情包字节数据，也没有提供表情包哈希值，则抛出异常
            Exception: 如果在缓存表情包的过程中发生错误，则抛出异常
        """
        # 先查找
        if emoji_hash is None:
            if emoji_bytes is None:
                raise ValueError("获取表情包描述失败: 既没有提供表情包字节数据，也没有提供表情包哈希值")
            emoji_hash = hashlib.sha256(emoji_bytes).hexdigest()

        if emoji := self.get_emoji_by_hash(emoji_hash):
            emoji_path = resolve_stored_image_path(emoji.full_path) if emoji.full_path else None
            if emoji_bytes and (emoji_path is None or not emoji_path.exists()):
                try:
                    restored_emoji = await self.ensure_emoji_saved(emoji_bytes, emoji_hash=emoji_hash)
                    emoji.full_path = restored_emoji.full_path
                    emoji.file_name = restored_emoji.file_name
                except Exception as e:
                    logger.warning(f"表情包缓存命中但本地文件缺失，回填失败: {e}")
            return emoji.description, _normalize_emoji_tag_text(emoji.description or "")
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji_hash, image_type=ImageType.EMOJI).limit(1)
                if result := session.exec(statement).first():
                    record_path = resolve_stored_image_path(result.full_path) if result.full_path else None
                    if emoji_bytes and (result.no_file_flag or record_path is None or not record_path.exists()):
                        try:
                            restored_emoji = await self.ensure_emoji_saved(emoji_bytes, emoji_hash=emoji_hash)
                            result.full_path = serialize_stored_image_path(restored_emoji.full_path)
                            result.no_file_flag = False
                        except Exception as e:
                            logger.warning(f"数据库命中表情包记录但本地文件缺失，回填失败: {e}")
                    cached_description = result.description or ""
                    cached_emotions = _normalize_emoji_tag_text(cached_description)
                    return (
                        cached_description,
                        cached_emotions,
                    )
        except Exception as e:
            logger.warning(f"从数据库查找表情包时出错: {e}，将尝试构建表情包描述")

        # 如果提供了字节数据但数据库中没有找到，尝试构建
        if not emoji_bytes:
            return None
        if not _is_vlm_task_configured():
            await self.ensure_emoji_saved(emoji_bytes, emoji_hash=emoji_hash)
            logger.info("未配置 VLM 模型，跳过表情包识别、打标签和审核")
            return None
        if not wait_for_build:
            await self.ensure_emoji_saved(emoji_bytes, emoji_hash=emoji_hash)
            self._schedule_description_build(emoji_hash, emoji_bytes, session_id=session_id)
            return None

        # 找不到尝试构建
        return await self._build_and_cache_emoji_description(emoji_hash, emoji_bytes, session_id=session_id)

    async def ensure_emoji_saved(
        self,
        emoji_bytes: bytes,
        *,
        emoji_hash: Optional[str] = None,
    ) -> MaiEmoji:
        """先缓存表情包文件与数据库记录，确保后续可按 hash 回填。"""
        if not _is_collected_emoji_size_allowed(len(emoji_bytes)):
            max_size_mb = global_config.emoji.max_emoji_size_mb
            raise ValueError(f"表情包大小超过收集上限 {max_size_mb:g} MB，跳过缓存")

        hash_str = emoji_hash or hashlib.sha256(emoji_bytes).hexdigest()

        save_lock = self._emoji_save_locks.setdefault(hash_str, asyncio.Lock())
        async with save_lock:
            return await self._ensure_emoji_saved_locked(emoji_bytes, hash_str)

    async def _ensure_emoji_saved_locked(self, emoji_bytes: bytes, hash_str: str) -> MaiEmoji:
        """按 hash 串行化缓存写入，避免同一临时文件被并发消费。"""
        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=hash_str, image_type=ImageType.EMOJI).limit(1)
                if record := session.exec(statement).first():
                    record_path = resolve_stored_image_path(record.full_path)
                    if not record.no_file_flag and record_path.exists():
                        return MaiEmoji.from_db_instance(record)
        except Exception as e:
            logger.error(f"缓存表情包前查询数据库时出错: {e}")
            raise e

        logger.info(f"表情包不存在于数据库中，准备缓存新表情包，哈希值: {hash_str}")
        tmp_file_path = EMOJI_DIR / f"{hash_str}.tmp"
        with tmp_file_path.open("wb") as file:
            file.write(emoji_bytes)

        emoji = MaiEmoji(full_path=tmp_file_path, image_bytes=emoji_bytes)
        await emoji.calculate_hash_format()

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if existing_record := session.exec(statement).first():
                    existing_record.full_path = serialize_stored_image_path(emoji.full_path)
                    existing_record.no_file_flag = False
                    session.add(existing_record)
                else:
                    image_record = emoji.to_db_instance()
                    image_record.is_registered = False
                    image_record.is_banned = False
                    image_record.no_file_flag = False
                    image_record.query_count = 0
                    session.add(image_record)
        except Exception as e:
            logger.error(f"缓存表情包记录到数据库时出错: {e}")
            raise e

        return emoji

    def _schedule_description_build(self, emoji_hash: str, emoji_bytes: bytes, *, session_id: str = "") -> None:
        """调度表情包描述后台构建任务。

        Args:
            emoji_hash: 表情包哈希值。
            emoji_bytes: 表情包字节数据。
        """
        if not _is_vlm_task_configured():
            logger.info("未配置 VLM 模型，跳过表情包后台识别任务")
            return

        if emoji_hash in self._pending_description_tasks:
            return

        task = asyncio.create_task(self._build_description_in_background(emoji_hash, emoji_bytes, session_id=session_id))
        self._pending_description_tasks[emoji_hash] = task
        task.add_done_callback(lambda finished_task: self._finalize_description_build(emoji_hash, finished_task))

    async def _build_description_in_background(
        self,
        emoji_hash: str,
        emoji_bytes: bytes,
        *,
        session_id: str = "",
    ) -> None:
        """在后台构建并缓存表情包描述。

        Args:
            emoji_hash: 表情包哈希值。
            emoji_bytes: 表情包字节数据。
        """
        try:
            logger.debug(f"表情包描述后台构建已开始，哈希值: {emoji_hash}")
            await self._build_and_cache_emoji_description(emoji_hash, emoji_bytes, session_id=session_id)
            logger.debug(f"表情包描述后台构建完成，哈希值: {emoji_hash}")
        except Exception as exc:
            logger.warning(f"表情包描述后台构建失败，哈希值: {emoji_hash}，错误: {exc}")

    def _finalize_description_build(self, emoji_hash: str, task: asyncio.Task[None]) -> None:
        """回收表情包描述后台构建任务。

        Args:
            emoji_hash: 表情包哈希值。
            task: 已完成的后台任务。
        """
        self._pending_description_tasks.pop(emoji_hash, None)
        try:
            task.result()
        except Exception as exc:
            logger.debug(f"表情包描述后台任务结束时捕获异常，哈希值: {emoji_hash}，错误: {exc}")

    async def _build_and_cache_emoji_description(
        self,
        emoji_hash: str,
        emoji_bytes: bytes,
        *,
        session_id: str = "",
    ) -> Optional[tuple[str, list[str]]]:
        """构建并缓存表情包描述（返回标签化结果，不再走额外识别流程）。"""
        # logger.info(f"Start building cached emoji description, hash={emoji_hash}")
        new_emoji = await self.ensure_emoji_saved(emoji_bytes, emoji_hash=emoji_hash)

        success_desc, new_emoji = await self.build_emoji_description(new_emoji, session_id=session_id)
        if not success_desc:
            logger.error("Build emoji description failed")
            return None

        # 情绪标签已在 build_emoji_description 内一次性生成，这里仅做兼容性兜底处理
        with get_db_session() as session:
            try:
                statement = select(Images).filter_by(image_hash=new_emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := session.exec(statement).first():
                    image_record.full_path = serialize_stored_image_path(new_emoji.full_path)
                    image_record.description = new_emoji.description
                    image_record.no_file_flag = False
                    session.add(image_record)
            except Exception as exc:
                logger.error(f"Update cached emoji description failed: {exc}")
        return new_emoji.description, _get_emoji_emotions(new_emoji)

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
                self.emojis = []
                removed_record_count = 0
                for record in results:
                    if record.image_type != ImageType.EMOJI:
                        continue
                    if not record.is_registered:
                        continue
                    if record.is_banned:
                        continue
                    try:
                        if record.no_file_flag or _resolve_existing_emoji_path(record.full_path) is None:
                            logger.warning(
                                f"[数据库] 已注册表情包缺少实际文件，删除破损注册记录: "
                                f"id={record.id}, path={record.full_path}"
                            )
                            session.delete(record)
                            removed_record_count += 1
                            continue
                        emoji = MaiEmoji.from_db_instance(record)
                        self.emojis.append(emoji)
                    except Exception as e:
                        logger.error(
                            f"[数据库] 加载表情包记录时出错，将删除异常记录: {e}\n"
                            f"记录ID: {record.id}, 路径: {record.full_path}"
                        )
                        session.delete(record)
                        removed_record_count += 1
                self._emoji_num = len(self.emojis)
                logger.info(
                    f"[数据库] 成功加载 {self._emoji_num} 个已注册表情包，"
                    f"清理破损注册记录 {removed_record_count} 条"
                )
        except Exception as e:
            logger.critical(f"[数据库] 加载表情包记录时发生不可恢复错误: {e}")
            self.emojis = []
            self._emoji_num = 0
            raise e

    def register_emoji_to_db(self, emoji: MaiEmoji) -> EmojiRegisterStatus:
        # sourcery skip: extract-method
        """Register an emoji in the database without moving its source file."""
        if not emoji or not isinstance(emoji, MaiEmoji):
            logger.error("[register_emoji] Invalid emoji object")
            return "failed"
        if not emoji.full_path.exists():
            logger.error(f"[register_emoji] Emoji file does not exist: {emoji.full_path}")
            return "failed"

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                existing_record = session.exec(statement).first()
                if existing_record:
                    if existing_record.is_banned:
                        logger.info(f"[register_emoji] Emoji is banned, skipping: {emoji.file_hash}")
                        return "skipped"
                    if existing_record.is_registered and _is_available_emoji_record(existing_record):
                        # logger.info(f"[register_emoji] Emoji already registered, skipping: {emoji.file_hash}")
                        return "skipped"

                    normalized_description = str(emoji.description or existing_record.description or "").strip()
                    normalized_emotions = _normalize_emoji_tag_text(normalized_description)
                    register_time = existing_record.register_time or datetime.now()

                    existing_record.full_path = serialize_stored_image_path(emoji.full_path)
                    existing_record.description = normalized_description
                    existing_record.query_count = max(int(existing_record.query_count), int(emoji.query_count))
                    existing_record.last_used_time = emoji.last_used_time or existing_record.last_used_time
                    existing_record.is_registered = True
                    existing_record.is_banned = False
                    existing_record.no_file_flag = False
                    existing_record.register_time = register_time
                    session.add(existing_record)

                    emoji.description = normalized_description
                    emoji.emotion = normalized_emotions
                    emoji.query_count = existing_record.query_count
                    emoji.last_used_time = existing_record.last_used_time
                    emoji.register_time = register_time

                    logger.info(
                        f"[register_emoji] Updated existing record and registered emoji, ID: {existing_record.id}, path: {emoji.full_path}"
                    )
                    return "registered"
        except Exception as e:
            logger.error(f"[register_emoji] Database query failed: {e}")
            return "failed"

        try:
            with get_db_session() as session:
                image_record = emoji.to_db_instance()
                image_record.is_registered = True
                image_record.is_banned = False
                image_record.no_file_flag = False
                image_record.register_time = datetime.now()
                session.add(image_record)
                session.flush()
                emoji.register_time = image_record.register_time
                record_id = image_record.id
                logger.info(f"[register_emoji] Registered emoji to database, ID: {record_id}, path: {emoji.full_path}")
        except Exception as e:
            logger.error(f"[register_emoji] Failed to write database record: {e}")
            return "failed"

        return "registered"

    def delete_emoji(self, emoji: MaiEmoji, keep_desc: bool = True) -> bool:
        """
        删除表情包的文件和数据库记录

        Args:
            emoji (MaiEmoji): 需要删除的表情包对象
            keep_desc (bool): 如果为 True，则删除文件后保留数据库描述缓存，并将 `no_file_flag`
                标记为 True；如果为 False，则同时删除数据库记录。默认为 True。
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
                    if keep_desc:
                        image_record.no_file_flag = True
                        session.add(image_record)
                        logger.info(f"[删除表情包] 成功修改数据库中的表情包记录: {emoji.file_name}")
                    else:
                        session.delete(image_record)
                        logger.info(f"[删除表情包] 成功删除数据库中的表情包记录: {emoji.file_name}")
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

        return self.update_emoji_usage_by_hash(emoji.file_hash, emoji=emoji)

    def update_emoji_usage_by_hash(
        self,
        emoji_hash: str,
        *,
        emoji: Optional[MaiEmoji] = None,
        log_missing: bool = True,
    ) -> bool:
        """按表情包哈希记录一次真实发送使用。"""

        normalized_hash = emoji_hash.strip()
        if not normalized_hash:
            logger.error("[更新表情包使用] 无效的表情包哈希")
            return False

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=normalized_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := session.exec(statement).first():
                    current_time = datetime.now()
                    image_record.query_count += 1
                    image_record.last_used_time = current_time
                    if emoji is None:
                        emoji = next((item for item in self.emojis if item.file_hash == normalized_hash), None)
                    if emoji is not None:
                        emoji.query_count = image_record.query_count
                        emoji.last_used_time = current_time
                    session.add(image_record)
                    # logger.info(f"[记录表情包使用] 成功记录表情包使用: {normalized_hash}")
                else:
                    if log_missing:
                        logger.error(f"[记录表情包使用] 未找到表情包记录: {normalized_hash}")
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
        根据哈希值从已注册表情包内存列表获取表情包对象

        Args:
            emoji_hash (str): 表情包的哈希值
        Returns:
            return (Optional[MaiEmoji]): 返回已注册表情包对象，如果未找到则返回 None
        """
        for emoji in self.emojis:
            if emoji.file_hash == emoji_hash:
                return emoji
        logger.debug(f"[获取表情包] 已注册表情包内存列表未命中，哈希值: {emoji_hash}")
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
                    # 按 file_hash 从内存列表移除（MaiEmoji 未定义 __eq__，
                    # 依赖身份比较的 remove 在跨实例调用时静默失败）
                    self.emojis = [e for e in self.emojis if e.file_hash != emoji.file_hash]
                    self._emoji_num = len(self.emojis)
                    logger.info(f"[封禁表情包] 成功封禁表情包: {emoji.file_name}")
                else:
                    logger.warning(f"[封禁表情包] 未找到表情包记录: {emoji.file_name}")
                    return False
        except Exception as e:
            logger.error(f"[封禁表情包] 封禁时出错: {e}")
            return False
        return True

    def unregister_emoji(self, emoji: MaiEmoji) -> bool:
        """取消表情包注册状态，但保留文件和识别结果。"""
        if not emoji or not emoji.file_hash:
            logger.error("[取消注册表情包] 无效的表情包对象")
            return False

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(image_hash=emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                if image_record := session.exec(statement).first():
                    image_record.is_registered = False
                    session.add(image_record)
                else:
                    logger.warning(f"[取消注册表情包] 未找到表情包记录: {emoji.file_name}")
                    return False
        except Exception as e:
            logger.error(f"[取消注册表情包] 取消注册时出错: {e}")
            return False

        self.emojis = [item for item in self.emojis if item.file_hash != emoji.file_hash]
        self._emoji_num = len(self.emojis)
        logger.info(f"[取消注册表情包] 成功取消注册表情包: {emoji.file_name}")
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
        logger.info(
            f"[获取表情包] 为[{emotion_label}]选中表情包: "
            f"{selected_emoji.file_name}({','.join(_get_emoji_emotions(selected_emoji))})，相似度: {similarity:.4f}",
        )
        return selected_emoji

    async def replace_an_emoji_by_llm(self, new_emoji: MaiEmoji, *, session_id: str = "") -> bool:
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
        emoji_replace_prompt_template.add_context("description", new_emoji.description or "无描述")
        emoji_replace_prompt = await prompt_manager.render_prompt(emoji_replace_prompt_template)

        decision_result = await emoji_manager_emotion_judge_llm.generate_response(
            emoji_replace_prompt,
            session_id=session_id,
        )
        decision = decision_result.response
        logger.info(f"[决策] 结果: {decision}")

        # 解析决策结果；兼容旧 prompt 的“删除编号X”，实际动作只取消注册。
        skip_decisions = ("不取消注册", "不删除", "do not unregister", "do not delete", "解除しない", "削除しない")
        if any(skip_text in decision for skip_text in skip_decisions):
            logger.info("[决策] 不取消注册任何表情包")
            return False
        try:
            match = re.search(
                r"(?:取消注册|删除)编号(\d+)|(?:unregister|delete)\s+number\s+(\d+)|番号(\d+)を(?:解除|削除)",
                decision,
                re.IGNORECASE,
            )
        except Exception as e:
            logger.error(f"[决策] 解析决策结果时出错: {e}")
            return False
        if match:
            matched_number = next(group for group in match.groups() if group)
            emoji_index = int(matched_number) - 1  # 转换为0-based索引
            # 检查索引是否有效
            if 0 <= emoji_index < len(selected_emojis):
                emoji_to_unregister = selected_emojis[emoji_index]
                logger.info(f"[决策] 取消注册表情包: {emoji_to_unregister.description}")
                if self.unregister_emoji(emoji_to_unregister):
                    register_status = self.register_emoji_to_db(new_emoji)
                    if register_status == "registered":
                        self.emojis.append(new_emoji)
                        self._emoji_num = len(self.emojis)
                        logger.info(f"[register_emoji] 成功替换并注册新表情包: {new_emoji.description}")
                        return True
                    if register_status == "skipped":
                        logger.info(f"[register_emoji] Replacement emoji was already registered: {new_emoji.description}")
                    else:
                        logger.error(f"[register_emoji] Failed to register replacement emoji: {new_emoji.description}")
                else:
                    logger.error("[错误] 取消注册表情包失败，无法完成替换")
            else:
                logger.error(f"[决策] 无效的表情包编号: {emoji_index + 1}")
        else:
            logger.error("[决策] 未能解析取消注册编号")
        return False

    async def review_emoji_for_registration(self, target_emoji: MaiEmoji, *, session_id: str = "") -> bool:
        """注册前审核表情包内容，审核关闭时直接通过。"""
        if not global_config.emoji.content_filtration:
            return True
        if not _is_vlm_task_configured():
            logger.warning(f"[表情包审查] 已启用内容过滤但未配置 VLM 模型，拒绝注册: {target_emoji.file_name}")
            return False

        image_format = target_emoji.image_format
        image_bytes = target_emoji.image_bytes or await asyncio.to_thread(
            target_emoji.read_image_bytes, target_emoji.full_path
        )
        image_base64 = ImageUtils.image_bytes_to_base64(image_bytes)
        try:
            review_prompt_template = prompt_manager.get_prompt("emoji_content_filtration")
            review_prompt = await prompt_manager.render_prompt(review_prompt_template)
            filtration_result = await emoji_manager_vlm.generate_response_for_image(
                review_prompt,
                image_base64,
                image_format,
                session_id=session_id,
            )
            llm_response = filtration_result.response
        except Exception as e:
            logger.error(f"[表情包审查] 调用视觉模型审查表情包时出错: {e}")
            return False

        if "否" in llm_response:
            logger.warning(f"[表情包审查] 表情包内容不符合要求，拒绝注册: {target_emoji.file_name}")
            return False
        return True

    async def build_emoji_description(self, target_emoji: MaiEmoji, *, session_id: str = "") -> tuple[bool, MaiEmoji]:
        """
        构建表情包描述

        Args:
            target_emoji (MaiEmoji): 目标表情包对象
        Returns:
            return (Tuple[bool, MaiEmoji]): 返回是否成功构建描述，及表情包对象
        """
        if not _is_vlm_task_configured():
            logger.info(
                f"[构建描述] 未配置 VLM 模型，跳过表情包识别和打标签: {target_emoji.file_name}"
            )
            return False, target_emoji

        if not target_emoji.file_hash or not target_emoji.image_format:
            # Should not happen, but just in case
            await target_emoji.calculate_hash_format()

        # 调用VLM生成描述
        image_format = target_emoji.image_format
        image_bytes = target_emoji.image_bytes or await asyncio.to_thread(
            target_emoji.read_image_bytes, target_emoji.full_path
        )
        image_base64 = ImageUtils.image_bytes_to_base64(image_bytes)
        try:
            if image_format == "gif":
                try:
                    image_bytes = await asyncio.to_thread(ImageUtils.gif_2_static_image, image_bytes)
                except Exception as e:
                    logger.error(f"[构建描述] 转换 GIF 图片时出错: {e}")
                    return False, target_emoji
                prompt: str = (
                    "这是一个动态图表情包，每一张图代表了动态图的一帧。"
                    "请只返回该表情包常见的情绪/场景标签，最多 5 个，"
                    "使用逗号分隔，标签可为中文或英文，不要附带解释。"
                )
                image_base64 = ImageUtils.image_bytes_to_base64(image_bytes)
                description_result = await emoji_manager_vlm.generate_response_for_image(
                    prompt,
                    image_base64,
                    "jpg",
                    session_id=session_id,
                )
                description = description_result.response
            else:
                prompt: str = (
                    "这是一个表情包图片，请提取该表情主要表达的情绪或语气标签，"
                    "最多 5 个，使用逗号分隔，返回纯文本标签列表，不要解释，不要输出其他内容。"
                )
                description_result = await emoji_manager_vlm.generate_response_for_image(
                    prompt,
                    image_base64,
                    image_format,
                    session_id=session_id,
                )
                description = description_result.response
        except Exception as e:
            logger.error(f"[构建描述] 调用视觉模型生成表情包描述时出错: {e}")
            return False, target_emoji

        if not description:
            logger.warning(f"[构建描述] 视觉模型返回空描述，跳过注册: {target_emoji.file_name}")
            return False, target_emoji

        normalized_description = str(description).strip()
        if not normalized_description:
            logger.warning(f"[构建描述] 视觉模型返回空标签，跳过注册: {target_emoji.file_name}")
            return False, target_emoji
        hook_result = await _get_runtime_manager().invoke_hook(
            "emoji.register.after_build_description",
            emoji=_serialize_emoji_for_hook(target_emoji),
            description=normalized_description,
            image_format=image_format,
        )
        if hook_result.aborted:
            logger.info(f"[构建描述] 表情包描述被 Hook 中止注册: {target_emoji.file_name}")
            return False, target_emoji

        normalized_description = str(hook_result.kwargs.get("description", description) or "").strip()
        if not normalized_description:
            logger.warning(f"[构建描述] Hook 返回空描述，拒绝注册: {target_emoji.file_name}")
            return False, target_emoji

        normalized_emotions = _normalize_emoji_tag_text(normalized_description)
        if not normalized_emotions:
            logger.warning(f"[构建描述] Hook 返回标签为空，拒绝注册: {target_emoji.file_name}")
            return False, target_emoji

        target_emoji.description = ",".join(normalized_emotions)
        target_emoji.emotion = normalized_emotions
        logger.info(f"理解表情包情绪: {target_emoji.description}")
        return True, target_emoji

    def _mark_emoji_vlm_processed(self, target_emoji: MaiEmoji) -> None:
        """记录表情包已经过 VLM 处理，避免自动维护任务反复消耗识别额度。"""
        if not target_emoji.file_hash:
            return

        try:
            with get_db_session() as session:
                statement = select(Images).filter_by(
                    image_hash=target_emoji.file_hash,
                    image_type=ImageType.EMOJI,
                ).limit(1)
                image_record = session.exec(statement).first()
                if image_record is None:
                    image_record = target_emoji.to_db_instance()
                    image_record.is_registered = False
                    image_record.is_banned = False
                    image_record.query_count = 0
                    image_record.register_time = None
                    image_record.last_used_time = None

                image_record.full_path = serialize_stored_image_path(target_emoji.full_path)
                if target_emoji.description:
                    image_record.description = target_emoji.description
                image_record.no_file_flag = False
                image_record.vlm_processed = True
                session.add(image_record)
        except Exception as exc:
            logger.error(f"[register_emoji] 标记表情包 VLM 处理状态失败: {exc}")

    def check_emoji_file_integrity(self) -> None:
        """
        检查表情包文件和数据库注册记录的一致性。

        该检查只维护数据库记录与内存可发送池，不主动删除 ``data/emoji`` 下的图片文件。
        """
        _ensure_directories()
        logger.info("[完整性检查] 开始检查表情包文件和注册记录一致性...")
        record_removal_count = 0
        available_emojis: list[MaiEmoji] = []

        with get_db_session() as session:
            statement = select(Images).filter_by(image_type=ImageType.EMOJI)
            records = session.exec(statement).all()
            for record in records:
                record_path = _resolve_existing_emoji_path(record.full_path)
                if record.no_file_flag or record_path is None:
                    if record.is_registered:
                        logger.warning(
                            f"[完整性检查] 已注册表情包缺少实际文件，删除破损注册记录: id={record.id}, path={record.full_path}"
                        )
                        session.delete(record)
                        record_removal_count += 1
                    else:
                        logger.debug(
                            f"[完整性检查] 未注册表情包缺少实际文件，保留为描述缓存: id={record.id}, path={record.full_path}"
                        )
                    continue
                if not record.is_registered or record.is_banned:
                    continue
                try:
                    available_emojis.append(MaiEmoji.from_db_instance(record))
                except Exception as exc:
                    logger.error(
                        f"[完整性检查] 加载表情包记录时出错，将删除异常记录: {exc}\n记录ID: {record.id}, 路径: {record.full_path}"
                    )
                    session.delete(record)
                    record_removal_count += 1

        self.emojis = available_emojis
        self._emoji_num = len(self.emojis)
        logger.info(
            f"[完整性检查] 表情包完整性检查完成，删除数据库记录 {record_removal_count} 条，"
            f"当前可发送表情包 {self._emoji_num} 个"
        )

    async def periodic_emoji_maintenance(self) -> None:
        """Run emoji maintenance tasks periodically."""
        while True:
            wait_seconds = max(global_config.emoji.check_interval * 60, 0)
            try:
                await asyncio.wait_for(self._maintenance_wakeup_event.wait(), timeout=wait_seconds)
                self._maintenance_wakeup_event.clear()
                continue
            except asyncio.TimeoutError:
                self._maintenance_wakeup_event.clear()

            _ensure_directories()
            if global_config.emoji.steal_emoji and (
                self._emoji_num < global_config.emoji.max_reg_num
                or (self._emoji_num > global_config.emoji.max_reg_num and global_config.emoji.do_replace)
            ):
                known_paths: set[Path] = set()
                with get_db_session() as session:
                    statement = select(Images).filter_by(image_type=ImageType.EMOJI)
                    for record in session.exec(statement).all():
                        if not _is_known_emoji_maintenance_record(record):
                            continue
                        if record_path := _resolve_existing_emoji_path(record.full_path):
                            known_paths.add(record_path)
                logger.info("[emoji_maintenance] Scanning data/emoji for new emojis...")
                for emoji_file in EMOJI_DIR.iterdir():
                    if not emoji_file.is_file():
                        continue
                    resolved_file = emoji_file.absolute().resolve()
                    if resolved_file in known_paths:
                        continue
                    if not _is_collected_emoji_size_allowed(emoji_file.stat().st_size):
                        logger.info(
                            f"[emoji_maintenance] Emoji file exceeds size limit "
                            f"{global_config.emoji.max_emoji_size_mb:g} MB, skipping: {emoji_file.name}"
                        )
                        continue
                    try:
                        register_status = await self.register_emoji_by_filename(emoji_file)
                    except Exception as e:
                        logger.error(f"[emoji_maintenance] Failed to process {emoji_file.name}: {e}")
                        register_status = "failed"
                    if register_status == "registered":
                        break
                    if register_status == "skipped":
                        logger.debug(f"[emoji_maintenance] Emoji already registered, keep file: {emoji_file.name}")
                    else:
                        logger.debug(f"[emoji_maintenance] Emoji not registered, keep file: {emoji_file.name}")

            try:
                await asyncio.to_thread(self.check_emoji_file_integrity)
            except Exception as e:
                logger.error(f"[emoji_maintenance] Maintenance task failed: {e}")

    async def register_emoji_by_filename(self, filename: Path | str) -> EmojiRegisterStatus:
        """Register an emoji file from ``data/emoji`` without moving or deleting it."""
        file_full_path = Path(filename).absolute().resolve()
        if not file_full_path.exists():
            logger.error(f"[register_emoji] Emoji file does not exist: {file_full_path}")
            return "failed"
        try:
            target_emoji = MaiEmoji(full_path=file_full_path)
        except Exception as e:
            logger.error(f"[register_emoji] Failed to create emoji object: {e}")
            return "failed"

        calc_success = await target_emoji.calculate_hash_format()
        if not calc_success:
            logger.error(f"[register_emoji] Failed to calculate hash and format: {file_full_path}")
            return "failed"
        file_full_path = target_emoji.full_path

        existing_record: Optional[Images] = None
        try:
            with get_db_session_manual() as session:
                statement = (
                    select(Images).filter_by(image_hash=target_emoji.file_hash, image_type=ImageType.EMOJI).limit(1)
                )
                existing_record = session.exec(statement).first()
        except Exception as e:
            logger.error(f"[register_emoji] Failed to query database: {e}")
            return "failed"

        if existing_record is not None:
            if existing_record.is_banned:
                logger.info(f"[register_emoji] Emoji is banned, skipping: {target_emoji.file_name}")
                return "skipped"

            if existing_record.is_registered and _is_available_emoji_record(existing_record):
                logger.info(f"[register_emoji] Emoji already registered, skipping: {target_emoji.file_name}")
                return "skipped"

            cached_description = str(existing_record.description or "").strip()
            if cached_description:
                normalized_emotions = _normalize_emoji_tag_text(cached_description)
                target_emoji.description = ",".join(normalized_emotions)
                target_emoji.emotion = normalized_emotions
                target_emoji.query_count = existing_record.query_count
                target_emoji.last_used_time = existing_record.last_used_time
                target_emoji.register_time = existing_record.register_time

        if existing_emoji := self.get_emoji_by_hash(target_emoji.file_hash):
            logger.info(f"[register_emoji] Emoji already loaded in memory, skipping: {existing_emoji.file_name}")
            return "skipped"

        if not target_emoji.description:
            desc_success, target_emoji = await self.build_emoji_description(target_emoji)
            self._mark_emoji_vlm_processed(target_emoji)
            if not desc_success:
                logger.error(f"[register_emoji] Failed to build emoji description: {file_full_path}")
                return "failed"

        if not await self.review_emoji_for_registration(target_emoji):
            self._mark_emoji_vlm_processed(target_emoji)
            logger.error(f"[register_emoji] Emoji did not pass content review: {file_full_path}")
            return "failed"

        if self._emoji_num >= global_config.emoji.max_reg_num and global_config.emoji.do_replace:
            logger.warning(
                f"[register_emoji] Emoji limit {global_config.emoji.max_reg_num} reached, trying replacement"
            )
            replaced = await self.replace_an_emoji_by_llm(target_emoji)
            if not replaced:
                logger.error("[register_emoji] Failed to replace existing emoji")
                return "failed"
            return "registered"

        register_status = self.register_emoji_to_db(target_emoji)
        if register_status == "registered":
            self.emojis.append(target_emoji)
            self._emoji_num = len(self.emojis)
            logger.info(f"[register_emoji] Registered new emoji: {target_emoji.file_name}")
        elif register_status == "failed":
            logger.error(f"[register_emoji] Failed to register emoji in database: {file_full_path}")
        else:
            logger.info(f"[register_emoji] Emoji already registered, skipping: {target_emoji.file_name}")
        return register_status

    def _calculate_emotion_similarity_list(self, text_emotion: str) -> list[tuple[MaiEmoji, float]]:
        """
        计算文本情感标签与所有表情包情感标签的相似度列表

        Args:
            text_emotion (str): 文本的情感标签
        Returns:
        return (List[Tuple[MaiEmoji, float]]): 返回表情包对象及其相似度的列表
        """
        normalized_text_emotion = str(text_emotion or "").strip().lower()
        if not normalized_text_emotion:
            return []

        similarity_list: list[tuple[MaiEmoji, float]] = []
        for emoji in self.emojis:
            candidate_emotions = _get_emoji_emotions(emoji)
            if not candidate_emotions:
                continue

            emotion_similarities = [
                1 - Levenshtein.distance(normalized_text_emotion, str(emotion).strip().lower()) / max(
                    len(normalized_text_emotion),
                    len(str(emotion).strip().lower()),
                )
                for emotion in candidate_emotions
                if emotion
            ]
            if not emotion_similarities:
                continue
            # 计算该表情包与输入标签的最接近匹配度
            similarity_list.append((emoji, max(emotion_similarities)))
        return similarity_list


emoji_manager = EmojiManager()
