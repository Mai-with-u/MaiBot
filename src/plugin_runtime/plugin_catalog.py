"""插件磁盘信息索引：只在初始化和文件变更时读取，查询不访问文件系统。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import asyncio
import json
import tomllib

from src.common.logger import get_logger
from src.config.file_watcher import FileChange, FileWatcher
from src.plugin_runtime.runner.manifest_validator import is_reserved_plugin_directory

logger = get_logger("plugin_runtime.catalog")
RECEIPT_NAME = ".maibot-release.json"
_METADATA_FILES = {"_manifest.json", "config.toml", RECEIPT_NAME}


@dataclass(frozen=True)
class PluginCatalogEntry:
    """一次完整读取的插件元数据；发布后只替换记录，不原地修改。"""

    plugin_id: str
    path: Path
    manifest: Dict[str, Any]
    builtin: bool
    enabled: Optional[bool]
    metadata_error: str
    release: Optional[Dict[str, Any]]

    @property
    def group_name(self) -> str:
        plugin_type = str(self.manifest.get("plugin_type", self.manifest.get("type", "extension"))).strip().lower()
        return "builtin" if self.builtin or plugin_type == "adapter" else "third_party"


def _resource_path(plugin_path: Path, filename: str) -> Path:
    """索引刷新时检查文件边界，查询阶段不重复解析已验证的路径。"""
    path = plugin_path / filename
    if path.is_symlink():
        raise ValueError(f"插件元数据文件不能是符号链接: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(plugin_path):
        raise ValueError(f"插件元数据文件超出插件目录: {path}")
    return resolved


def _read_entry(path: Path, root: Path, builtin: bool) -> Optional[PluginCatalogEntry]:
    if is_reserved_plugin_directory(path) or not path.is_dir():
        return None
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(root):
        logger.warning(f"拒绝索引包含链接或超出插件根目录的路径: {path}")
        return None
    path = resolved
    try:
        manifest_path = _resource_path(path, "_manifest.json")
        if not manifest_path.is_file():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning(f"插件清单无效或不可读取: {path}", exc_info=True)
        return None
    if not isinstance(manifest, dict) or not all(
        isinstance(manifest.get(key), str) and manifest[key].strip() for key in ("id", "name", "version")
    ):
        logger.warning(f"插件清单缺少有效的 id、name 或 version: {manifest_path}")
        return None
    plugin_id = str(manifest["id"]).strip()
    enabled: Optional[bool] = True
    errors: List[str] = []
    try:
        config_path = _resource_path(path, "config.toml")
        if config_path.is_file():
            with config_path.open("rb") as handle:
                config = tomllib.load(handle)
            section = config.get("plugin", {})
            if isinstance(section, dict):
                value = section.get("enabled", True)
                enabled = (
                    value.strip().lower() not in {"false", "0", "no", "off", "disabled"}
                    if isinstance(value, str)
                    else bool(value)
                )
    except (OSError, ValueError) as exc:
        enabled = None
        errors.append(f"读取插件 {plugin_id} 启用配置失败：{exc}")
    release = None
    try:
        receipt_path = _resource_path(path, RECEIPT_NAME)
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError(f"插件安装记录必须是 JSON 对象: {receipt_path}")
            release = receipt
    except (OSError, ValueError) as exc:
        errors.append(f"读取插件 {plugin_id} 安装记录失败：{exc}")
    return PluginCatalogEntry(plugin_id, path, manifest, builtin, enabled, "; ".join(errors), release)


class PluginCatalog:
    """维护静态索引；文件变更与版本替换共用事务锁，刷新结果原子发布。"""

    def __init__(self, on_changed: Callable[[], None], update_lock: asyncio.Lock) -> None:
        self._on_changed = on_changed
        self._update_lock = update_lock
        self._refresh_lock = asyncio.Lock()
        self._roots: Dict[Path, bool] = {}
        self._entries: Dict[Path, PluginCatalogEntry] = {}
        self._initialized = False
        self._refresh_error: Optional[Exception] = None
        self._watcher: Optional[FileWatcher] = None
        self._pending_paths: Set[Path] = set()
        self._refresh_task: Optional[asyncio.Task[None]] = None

    @property
    def entries(self) -> Tuple[PluginCatalogEntry, ...]:
        if self._refresh_error is not None:
            raise RuntimeError("插件目录索引更新失败") from self._refresh_error
        if not self._initialized:
            raise RuntimeError("插件目录索引尚未初始化")
        return tuple(self._entries.values())

    async def start(self, builtin_dirs: Sequence[Path], plugin_dirs: Sequence[Path]) -> None:
        """先接管元数据事件再扫描，配置热更新仍由运行时在加载结束后订阅。"""
        self._roots = {path.resolve(): True for path in builtin_dirs}
        self._roots.update({path.resolve(): False for path in plugin_dirs})
        if self._watcher is None and self._roots:
            self._watcher = FileWatcher(self._roots, debounce_ms=600)
            self._watcher.subscribe(self._handle_changes)
            await self._watcher.start()
        await self.refresh()

    async def stop(self) -> None:
        if self._watcher is not None:
            await self._watcher.stop()
            self._watcher = None
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
            self._refresh_task = None
        self._pending_paths.clear()

    def _read_entries(self, paths: Optional[Sequence[Path]]) -> Dict[Path, PluginCatalogEntry]:
        entries = {} if paths is None else self._entries.copy()
        candidates = (
            [(path, root, builtin) for root, builtin in self._roots.items() for path in root.iterdir()]
            if paths is None
            else [(path, path.parent, self._roots[path.parent]) for path in paths if path.parent in self._roots]
        )
        for path, root, builtin in candidates:
            entry = _read_entry(path, root, builtin)
            if entry is None:
                entries.pop(path, None)
            else:
                entries[path] = entry
        return entries

    async def refresh(self, paths: Optional[Sequence[Path]] = None) -> None:
        """只读取受影响的目录；整次读取成功后一次性替换索引。"""
        async with self._refresh_lock:
            try:
                entries = await asyncio.to_thread(self._read_entries, paths)
            except Exception as exc:
                self._refresh_error = exc
                self._on_changed()
                raise
            changed = not self._initialized or entries != self._entries or self._refresh_error is not None
            self._entries = entries
            self._initialized = True
            # 意外错误需要完整重建确认恢复，其他插件的局部更新不能掩盖该错误。
            if paths is None:
                self._refresh_error = None
            if changed:
                self._on_changed()

    async def refresh_plugins(self, plugin_ids: Sequence[str]) -> None:
        paths = [entry.path for entry in self._entries.values() if entry.plugin_id in plugin_ids]
        if paths:
            await self.refresh(paths)

    async def _handle_changes(self, changes: Sequence[FileChange]) -> None:
        for change in changes:
            for root in self._roots:
                if not change.path.is_relative_to(root):
                    continue
                parts = change.path.relative_to(root).parts
                if len(parts) == 1 or (len(parts) == 2 and parts[1] in _METADATA_FILES):
                    path = root / parts[0]
                    if not is_reserved_plugin_directory(path):
                        self._pending_paths.add(path)
        # 回调只登记工作，不在文件监听器的回调限时内等待插件事务。
        if self._pending_paths and (self._refresh_task is None or self._refresh_task.done()):
            self._refresh_task = asyncio.create_task(self._drain_changes())

    async def _drain_changes(self) -> None:
        try:
            while self._pending_paths:
                paths = list(self._pending_paths)
                self._pending_paths.clear()
                async with self._update_lock:
                    await self.refresh(paths)
        except Exception:
            logger.exception("插件目录索引更新失败")
