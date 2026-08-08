"""回复效果独立 JSON 存储。"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List

import gzip
import json
import time

from sqlalchemy.exc import OperationalError
from sqlmodel import col, delete, select

from src.common.database.database import engine, get_db_session
from src.common.database.database_model import MaisakaReplyEffect
from src.common.logger import get_logger
from src.common.reply_effect_record_codec import decode_record_payload, encode_record_payload

from .models import ReplyEffectRecord, ReplyEffectStatus, reply_effect_record_from_dict
from .path_utils import BASE_DIR, build_reply_effect_chat_dir, normalize_preview_name

LEGACY_RETRYABLE_EVALUATION_ERRORS = {
    "回复效果评审连续两次校验失败：Connection error.",
    "回复效果评审连续两次校验失败：Request timed out.",
}
logger = get_logger("maisaka_reply_effect_storage")


class ReplyEffectStorage:
    """负责回复效果记录的独立 JSON 文件存储。"""

    _DEFAULT_MAX_RECORDS_PER_CHAT = 256
    _TRIM_COUNT = 100
    _CLEARED_EFFECT_IDS: set[str] = set()

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or BASE_DIR

    def create_record_file(self, record: ReplyEffectRecord) -> Path:
        """为新记录创建文件路径并写入初始 JSON。"""

        chat_dir_name = normalize_preview_name(record.session.platform_type_id)
        if chat_dir_name == "unknown":
            chat_dir = build_reply_effect_chat_dir(record.session.session_id, self._base_dir).resolve()
        else:
            chat_dir = (self._base_dir / chat_dir_name).resolve()
        chat_dir.mkdir(parents=True, exist_ok=True)
        timestamp_ms = int(time.time() * 1000)
        safe_effect_id = record.effect_id.replace("-", "")
        file_path = chat_dir / f"{timestamp_ms}_{safe_effect_id}.json.gz"
        record.file_path = file_path
        self.save_record(record)
        self._trim_overflow(chat_dir)
        return file_path

    def save_record(self, record: ReplyEffectRecord) -> None:
        """原子写入记录 JSON。"""

        if record.effect_id in self._CLEARED_EFFECT_IDS:
            if record.file_path is not None:
                record.file_path.unlink(missing_ok=True)
            return
        if record.file_path is None:
            self.create_record_file(record)
            return

        file_path = record.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = file_path.with_name(f".{file_path.name}.tmp")
        serialized = json.dumps(
            record.to_json_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        temp_path.write_bytes(
            gzip.compress(serialized, compresslevel=9, mtime=0),
        )
        temp_path.replace(file_path)
        self._save_database_summary(record)

    def load_finalized_records(self, *, exclude_effect_id: str = "") -> List[ReplyEffectRecord]:
        """读取 v2 已完成记录，供同场景相对基线计算。"""

        with get_db_session(auto_commit=False) as session:
            statement = select(MaisakaReplyEffect).where(MaisakaReplyEffect.status == "finalized")
            if exclude_effect_id:
                statement = statement.where(MaisakaReplyEffect.effect_id != exclude_effect_id)
            rows = session.exec(statement.order_by(col(MaisakaReplyEffect.finalized_at).desc()).limit(5000)).all()
        records: List[ReplyEffectRecord] = []
        for row in rows:
            try:
                payload = decode_record_payload(row.record_json, row.record_blob)
                if int(payload.get("schema_version", 0)) != 2:
                    continue
                records.append(reply_effect_record_from_dict(payload))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    def load_unfinished_records(self, session_id: str) -> List[ReplyEffectRecord]:
        """读取待结算记录，并恢复旧版本错误重试造成的瞬时失败。"""

        recoverable_statuses = {"pending", "evaluating", "evaluation_failed"}
        with get_db_session(auto_commit=False) as session:
            rows = session.exec(
                select(MaisakaReplyEffect)
                .where(
                    MaisakaReplyEffect.session_id == session_id,
                    col(MaisakaReplyEffect.status).in_(recoverable_statuses),
                )
                .order_by(col(MaisakaReplyEffect.created_at).asc())
            ).all()

        records: List[ReplyEffectRecord] = []
        for row in rows:
            try:
                payload = decode_record_payload(row.record_json, row.record_blob)
                if int(payload.get("schema_version", 0)) != 2:
                    continue
                record = reply_effect_record_from_dict(payload)
                if (
                    record.status == ReplyEffectStatus.EVALUATION_FAILED
                    and record.evaluation_error not in LEGACY_RETRYABLE_EVALUATION_ERRORS
                ):
                    continue
                record.file_path = self._find_record_file(record)
                records.append(record)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    def load_records_by_ids(self, effect_ids: set[str]) -> List[ReplyEffectRecord]:
        """加载恢复评审仍会引用的候选记录。"""

        if not effect_ids:
            return []
        with get_db_session(auto_commit=False) as session:
            rows = session.exec(
                select(MaisakaReplyEffect).where(col(MaisakaReplyEffect.effect_id).in_(effect_ids))
            ).all()
        records: List[ReplyEffectRecord] = []
        for row in rows:
            try:
                payload = decode_record_payload(row.record_json, row.record_blob)
                if int(payload.get("schema_version", 0)) != 2:
                    continue
                records.append(reply_effect_record_from_dict(payload))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return records

    @staticmethod
    def _save_database_summary(record: ReplyEffectRecord) -> None:
        payload = record.to_json_dict()
        scores = record.scores
        created_at = datetime.fromisoformat(record.created_at)
        finalized_at = datetime.fromisoformat(record.finalized_at) if record.finalized_at else None
        with get_db_session() as session:
            row = session.get(MaisakaReplyEffect, record.effect_id)
            if row is None:
                row = MaisakaReplyEffect(
                    effect_id=record.effect_id,
                    session_id=record.session.session_id,
                    created_at=created_at,
                    status=record.status.value,
                )
            row.session_name = record.session.session_name
            row.chat_type = record.session.chat_type
            row.status = record.status.value
            row.finalized_at = finalized_at
            row.strategy_primary = record.reply.strategy_primary
            row.model_name = record.reply.model_name
            row.request_fingerprint = record.reply.request_fingerprint
            row.prompt_fingerprint = record.reply.prompt_fingerprint
            row.scorer_version = record.scorer_version
            row.response_score = scores.response_score if scores else None
            row.reception_score = scores.reception_score if scores else None
            row.conversation_score = scores.conversation_score if scores else None
            row.raw_score = scores.raw_score if scores else None
            row.relative_score = scores.relative_score if scores else None
            row.confidence = scores.confidence if scores else 0.0
            row.record_json = "{}"
            row.record_blob = encode_record_payload(payload)
            session.add(row)
        ReplyEffectStorage._trim_database_records(record.session.session_id)

    @staticmethod
    def _trim_database_records(session_id: str) -> None:
        max_records = ReplyEffectStorage._get_max_records_per_chat()
        with get_db_session() as session:
            rows = session.exec(
                select(MaisakaReplyEffect)
                .where(MaisakaReplyEffect.session_id == session_id)
                .order_by(col(MaisakaReplyEffect.created_at).desc())
                .offset(max_records)
            ).all()
            for row in rows:
                session.delete(row)

    @staticmethod
    def read_json(file_path: Path) -> Dict[str, object]:
        """读取已保存的 JSON 文件。"""

        if file_path.suffix == ".gz":
            serialized = gzip.decompress(file_path.read_bytes()).decode("utf-8")
        else:
            serialized = file_path.read_text(encoding="utf-8")
        return json.loads(serialized)

    def clear_all_records(self) -> tuple[int, int, bool]:
        """删除全部数据库记录与诊断镜像，并回收数据库文件空间。"""

        with get_db_session() as session:
            effect_ids = list(session.exec(select(MaisakaReplyEffect.effect_id)).all())
            self._CLEARED_EFFECT_IDS.update(effect_ids)
            session.exec(delete(MaisakaReplyEffect))

        removed_files = 0
        for pattern in ("*.json", "*.json.gz"):
            for file_path in self._base_dir.rglob(pattern):
                if not file_path.is_file():
                    continue
                file_path.unlink()
                removed_files += 1

        space_reclaimed = True
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.commit()
                connection.exec_driver_sql("VACUUM")
        except OperationalError as exc:
            space_reclaimed = False
            logger.warning(f"评分数据已清空，但数据库空间暂时无法回收：{exc}")
        return len(effect_ids), removed_files, space_reclaimed

    @classmethod
    def restore_record_ids(cls, effect_ids: List[str]) -> None:
        """导入备份时允许被清空过的记录 ID 重新写入。"""

        cls._CLEARED_EFFECT_IDS.difference_update(effect_ids)

    def _find_record_file(self, record: ReplyEffectRecord) -> Path | None:
        """定位记录已有的诊断镜像，避免恢复后重复创建 JSON。"""

        chat_dir_name = normalize_preview_name(record.session.platform_type_id)
        if chat_dir_name == "unknown":
            chat_dir = build_reply_effect_chat_dir(record.session.session_id, self._base_dir).resolve()
        else:
            chat_dir = (self._base_dir / chat_dir_name).resolve()
        safe_effect_id = record.effect_id.replace("-", "")
        compressed_file = next(
            iter(sorted(chat_dir.glob(f"*_{safe_effect_id}.json.gz"), reverse=True)),
            None,
        )
        if compressed_file is not None:
            return compressed_file
        return next(iter(sorted(chat_dir.glob(f"*_{safe_effect_id}.json"), reverse=True)), None)

    def _trim_overflow(self, chat_dir: Path) -> None:
        """超过容量时删除最旧的回复效果记录。"""

        max_records = self._get_max_records_per_chat()
        files = [
            file_path
            for pattern in ("*.json", "*.json.gz")
            for file_path in chat_dir.glob(pattern)
            if file_path.is_file()
        ]
        if len(files) <= max_records:
            return

        sorted_files = sorted(files, key=lambda file_path: file_path.stat().st_mtime)
        overflow_count = len(files) - max_records
        trim_count = min(len(sorted_files), max(self._TRIM_COUNT, overflow_count))
        for old_file in sorted_files[:trim_count]:
            try:
                old_file.unlink()
            except FileNotFoundError:
                continue

    @classmethod
    def _get_max_records_per_chat(cls) -> int:
        try:
            from src.config.config import global_config

            configured_limit = global_config.log.maisaka_reply_effect_limit
            return max(1, int(configured_limit or cls._DEFAULT_MAX_RECORDS_PER_CHAT))
        except Exception:
            return cls._DEFAULT_MAX_RECORDS_PER_CHAT
