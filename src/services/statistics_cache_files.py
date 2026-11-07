"""统计缓存文件。

统计缓存体积大（几百 KB）、每轮统计都会更新；放在 local_store.json 里时，
任何一个键的写入都要把整个文件重写一遍。所以每份统计缓存单独成文件，
放在实例目录的 data/statistics/ 下。

多实例：路径取自 get_instance_root()，每个 Bot 进程只读写自己实例目录里的文件，
不依赖进程的工作目录。写入先落临时文件再原子替换，临时文件名带进程号和线程号；
即使同一实例目录意外跑了两个进程，读到的也总是一份完整文件（后写入的覆盖先写入的）。
"""

from pathlib import Path
from typing import Any

import json
import os
import threading

from src.common.logger import get_logger
from src.common.paths import get_instance_root

logger = get_logger("statistics_cache")

STATISTICS_CACHE_DIRECTORY = Path("data") / "statistics"

_MISSING = object()


class StatisticsCacheFile:
    """一份统计缓存：内存里留一份，落盘为紧凑 JSON。"""

    def __init__(self, filename: str, *, legacy_key: str | None = None):
        self.filename = filename
        # 旧版本存放在 local_storage 中的键；首次读取时迁到独立文件后删除
        self.legacy_key = legacy_key
        self._lock = threading.Lock()
        self._value: Any = _MISSING

    @property
    def path(self) -> Path:
        return get_instance_root() / STATISTICS_CACHE_DIRECTORY / self.filename

    def load(self) -> Any | None:
        """读取缓存；文件不存在或损坏时返回 None。"""
        with self._lock:
            if self._value is not _MISSING:
                return self._value
            value = self._read_file()
            if value is _MISSING:
                value = self._migrate_legacy()
            self._value = value
            return value

    def save(self, value: Any) -> bool:
        """写入缓存；落盘失败只记日志，内存里的值照样更新，不影响本轮统计。"""
        with self._lock:
            self._value = value
            return self._write_file(value)

    def _write_file(self, value: Any) -> bool:
        path = self.path
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, separators=(",", ":"))
            os.replace(temporary, path)
            return True
        except (OSError, TypeError, ValueError) as error:
            logger.warning(f"写入统计缓存 {path} 失败: {error}")
            temporary.unlink(missing_ok=True)
            return False

    def clear_memory(self) -> None:
        """丢掉内存里的值，下次读取时重新读文件（测试与外部替换文件后使用）。"""
        with self._lock:
            self._value = _MISSING

    def _read_file(self) -> Any:
        path = self.path
        try:
            with path.open(encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            return _MISSING
        except (OSError, ValueError) as error:
            logger.warning(f"读取统计缓存 {path} 失败，将重新统计: {error}")
            return None

    def _migrate_legacy(self) -> Any | None:
        if not self.legacy_key:
            return None
        from src.manager.local_store_manager import local_storage

        if self.legacy_key not in local_storage:
            return None
        value = local_storage[self.legacy_key]
        if self._write_file(value):
            # 迁移成功后才删旧键，避免落盘失败时丢掉累计数据
            del local_storage[self.legacy_key]
            logger.info(f"已把统计缓存 {self.legacy_key} 从 local_store 迁到 {self.path}")
        return value


# 「自部署以来」的累计统计，供下一轮增量叠加
full_statistics_cache = StatisticsCacheFile("full_statistics.json", legacy_key="last_full_statistics")
# 首页统计卡片
dashboard_statistics_cache = StatisticsCacheFile(
    "dashboard_cache.json", legacy_key="webui_dashboard_statistics_cache"
)
# 详细统计页的快照；重启后先显示上一轮结果，不再空等第一轮统计
detailed_statistics_cache = StatisticsCacheFile("detailed_snapshot.json")
