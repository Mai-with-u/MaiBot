"""回复效果独立 JSON 存储。"""

from pathlib import Path
from typing import Dict

import json
import time

from .models import ReplyEffectRecord
from .path_utils import BASE_DIR, build_reply_effect_chat_dir, normalize_preview_name


class ReplyEffectStorage:
    """负责回复效果记录的独立 JSON 文件存储。"""

    _DEFAULT_MAX_RECORDS_PER_CHAT = 256
    _TRIM_COUNT = 100

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
        file_path = chat_dir / f"{timestamp_ms}_{safe_effect_id}.json"
        record.file_path = file_path
        self.save_record(record)
        self._trim_overflow(chat_dir)
        return file_path

    def save_record(self, record: ReplyEffectRecord) -> None:
        """原子写入记录 JSON。"""

        if record.file_path is None:
            self.create_record_file(record)
            return

        file_path = record.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = file_path.with_name(f".{file_path.name}.tmp")
        temp_path.write_text(
            json.dumps(record.to_json_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temp_path.replace(file_path)

    @staticmethod
    def read_json(file_path: Path) -> Dict[str, object]:
        """读取已保存的 JSON 文件。"""

        return json.loads(file_path.read_text(encoding="utf-8"))

    def _trim_overflow(self, chat_dir: Path) -> None:
        """超过容量时删除最旧的回复效果记录。"""

        max_records = self._get_max_records_per_chat()
        files = [file_path for file_path in chat_dir.glob("*.json") if file_path.is_file()]
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
