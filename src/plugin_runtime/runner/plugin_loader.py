"""插件加载器

在 Runner 进程中负责发现和加载插件。
插件通过 SDK 编写，不再 import src.*。
支持：manifest 校验、依赖解析（拓扑排序）、生命周期钩子。
兼容旧版 src.plugin_system 插件（通过导入钩子 + LegacyPluginAdapter）。
"""

from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import contextlib
import importlib
import importlib.util
import json
import os
import sys

from src.common.logger import get_logger
from src.plugin_runtime.runner.manifest_validator import ManifestValidator

logger = get_logger("plugin_runtime.runner.plugin_loader")

PluginCandidate = Tuple[Path, Dict[str, Any], Path]


class PluginMeta:
    """加载后的插件元数据"""

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: str,
        module_name: str,
        plugin_instance: Any,
        manifest: Dict[str, Any],
    ) -> None:
        """初始化插件元数据。

        Args:
            plugin_id: 插件 ID。
            plugin_dir: 插件目录绝对路径。
            module_name: 插件入口模块名。
            plugin_instance: 插件实例对象。
            manifest: 解析后的 manifest 内容。
        """
        self.plugin_id = plugin_id
        self.plugin_dir = plugin_dir
        self.module_name = module_name
        self.instance = plugin_instance
        self.manifest = manifest
        self.version = manifest.get("version", "1.0.0")
        self.capabilities_required = manifest.get("capabilities", [])
        self.dependencies: List[str] = self._extract_dependencies(manifest)

    @staticmethod
    def _extract_dependencies(manifest: Dict[str, Any]) -> List[str]:
        """从 manifest 中提取依赖列表。

        Args:
            manifest: 插件 manifest。

        Returns:
            List[str]: 规范化后的依赖插件 ID 列表。
        """
        raw = manifest.get("dependencies", [])
        result: List[str] = []
        for dep in raw:
            if isinstance(dep, str):
                result.append(dep.strip())
            elif isinstance(dep, dict):
                if name := str(dep.get("name", "")).strip():
                    result.append(name)
        return result


class PluginLoader:
    """插件加载器

    扫描插件目录，加载符合 SDK 规范的插件。
    每个插件目录须包含：
    - _manifest.json: 插件元数据
    - plugin.py: 插件入口模块（导出 create_plugin 工厂函数）
    """

    def __init__(self, host_version: str = "") -> None:
        """初始化插件加载器。

        Args:
            host_version: Host 版本号，用于 manifest 兼容性校验。
        """
        self._loaded_plugins: Dict[str, PluginMeta] = {}
        self._failed_plugins: Dict[str, str] = {}
        self._manifest_validator = ManifestValidator(host_version=host_version)
        self._compat_hook_installed = False

    def discover_and_load(self, plugin_dirs: List[str]) -> List[PluginMeta]:
        """扫描多个目录并加载所有插件。

        Args:
            plugin_dirs: 插件目录列表。

        Returns:
            List[PluginMeta]: 成功加载的插件元数据列表，按依赖顺序排列。
        """
        candidates, duplicate_candidates = self._discover_candidates(plugin_dirs)
        self._record_duplicate_candidates(duplicate_candidates)

        # 第二阶段：依赖解析（拓扑排序）
        load_order, failed_deps = self._resolve_dependencies(candidates)
        self._record_failed_dependencies(failed_deps)

        # 第三阶段：按依赖顺序加载
        return self._load_plugins_in_order(load_order, candidates)

    def discover_candidates(self, plugin_dirs: List[str]) -> Tuple[Dict[str, PluginCandidate], Dict[str, List[Path]]]:
        """扫描插件目录并返回候选插件。

        Args:
            plugin_dirs: 需要扫描的插件根目录列表。

        Returns:
            Tuple[Dict[str, PluginCandidate], Dict[str, List[Path]]]:
                候选插件映射和重复插件 ID 冲突映射。
        """
        return self._discover_candidates(plugin_dirs)

    def _discover_candidates(self, plugin_dirs: List[str]) -> Tuple[Dict[str, PluginCandidate], Dict[str, List[Path]]]:
        """扫描插件目录并收集候选插件。"""
        candidates: Dict[str, PluginCandidate] = {}
        duplicate_candidates: Dict[str, List[Path]] = {}

        for base_dir_str in plugin_dirs:
            base_dir = Path(base_dir_str)
            if not base_dir.is_dir():
                logger.warning(f"插件目录不存在: {base_dir}")
                continue

            for plugin_dir in sorted(entry for entry in base_dir.iterdir() if entry.is_dir()):
                discovered = self._discover_single_candidate(plugin_dir)
                if discovered is None:
                    continue

                plugin_id, candidate = discovered
                if plugin_id in duplicate_candidates:
                    duplicate_candidates[plugin_id].append(candidate[0])
                    continue

                previous = candidates.get(plugin_id)
                if previous is not None:
                    duplicate_candidates[plugin_id] = [previous[0], candidate[0]]
                    candidates.pop(plugin_id, None)
                    continue

                candidates[plugin_id] = candidate

        return candidates, duplicate_candidates

    def _discover_single_candidate(self, plugin_dir: Path) -> Optional[Tuple[str, PluginCandidate]]:
        """发现并校验单个插件目录。"""
        manifest_path = plugin_dir / "_manifest.json"
        plugin_path = plugin_dir / "plugin.py"

        if not manifest_path.exists() or not plugin_path.exists():
            return None

        try:
            with manifest_path.open("r", encoding="utf-8") as manifest_file:
                manifest: Dict[str, Any] = json.load(manifest_file)
        except Exception as e:
            self._failed_plugins[plugin_dir.name] = f"manifest 解析失败: {e}"
            logger.error(f"插件 {plugin_dir.name} manifest 解析失败: {e}")
            return None

        if not self._manifest_validator.validate(manifest):
            errors = "; ".join(self._manifest_validator.errors)
            self._failed_plugins[plugin_dir.name] = f"manifest 校验失败: {errors}"
            return None

        plugin_id = str(manifest.get("name", plugin_dir.name)).strip() or plugin_dir.name
        return plugin_id, (plugin_dir, manifest, plugin_path)

    def _record_duplicate_candidates(self, duplicate_candidates: Dict[str, List[Path]]) -> None:
        """记录重复插件 ID 错误。"""
        for plugin_id, conflict_dirs in duplicate_candidates.items():
            unique_dirs = sorted({str(path) for path in conflict_dirs})
            reason = f"检测到重复插件 ID: {plugin_id} -> {', '.join(unique_dirs)}"
            self._failed_plugins[plugin_id] = reason
            logger.error(reason)

    def _record_failed_dependencies(self, failed_deps: Dict[str, str]) -> None:
        """记录依赖解析失败信息。"""
        for plugin_id, reason in failed_deps.items():
            self._failed_plugins[plugin_id] = reason
            logger.error(f"插件 {plugin_id} 依赖解析失败: {reason}")

    def _load_plugins_in_order(
        self,
        load_order: List[str],
        candidates: Dict[str, PluginCandidate],
    ) -> List[PluginMeta]:
        """按依赖顺序加载插件。"""
        results: List[PluginMeta] = []
        for plugin_id in load_order:
            plugin_dir, manifest, plugin_path = candidates[plugin_id]
            try:
                if meta := self._load_single_plugin(plugin_id, plugin_dir, manifest, plugin_path):
                    results.append(meta)
            except Exception as e:
                self._failed_plugins[plugin_id] = str(e)
                logger.error(f"加载插件失败 [{plugin_id}]: {e}", exc_info=True)

        return results

    def get_plugin(self, plugin_id: str) -> Optional[PluginMeta]:
        """获取已加载的插件"""
        return self._loaded_plugins.get(plugin_id)

    def set_loaded_plugin(self, meta: PluginMeta) -> None:
        """登记一个已经完成初始化的插件。

        Args:
            meta: 待登记的插件元数据。
        """
        self._loaded_plugins[meta.plugin_id] = meta

    def remove_loaded_plugin(self, plugin_id: str) -> Optional[PluginMeta]:
        """移除一个已加载插件的元数据。

        Args:
            plugin_id: 待移除的插件 ID。

        Returns:
            Optional[PluginMeta]: 被移除的插件元数据；不存在时返回 ``None``。
        """
        return self._loaded_plugins.pop(plugin_id, None)

    def purge_plugin_modules(self, plugin_id: str, plugin_dir: str) -> List[str]:
        """清理指定插件目录下的模块缓存。

        Args:
            plugin_id: 插件 ID。
            plugin_dir: 插件目录绝对路径。

        Returns:
            List[str]: 已从 ``sys.modules`` 中移除的模块名列表。
        """
        removed_modules: List[str] = []
        plugin_path = Path(plugin_dir).resolve()
        synthetic_module_name = f"_maibot_plugin_{plugin_id}"

        for module_name, module in list(sys.modules.items()):
            if module_name == synthetic_module_name:
                removed_modules.append(module_name)
                sys.modules.pop(module_name, None)
                continue

            module_file = getattr(module, "__file__", None)
            if module_file is None:
                continue

            try:
                module_path = Path(module_file).resolve()
            except Exception:
                continue

            if module_path.is_relative_to(plugin_path):
                removed_modules.append(module_name)
                sys.modules.pop(module_name, None)

        importlib.invalidate_caches()
        return removed_modules

    def list_plugins(self) -> List[str]:
        """列出所有已加载的插件 ID"""
        return list(self._loaded_plugins.keys())

    @property
    def failed_plugins(self) -> Dict[str, str]:
        """返回当前记录的失败插件原因映射。"""
        return dict(self._failed_plugins)

    # ──── 依赖解析 ────────────────────────────────────────────

    def resolve_dependencies(
        self,
        candidates: Dict[str, PluginCandidate],
        extra_available: Optional[Set[str]] = None,
    ) -> Tuple[List[str], Dict[str, str]]:
        """解析候选插件的依赖顺序。

        Args:
            candidates: 待加载的候选插件集合。
            extra_available: 视为已满足的外部依赖插件 ID 集合。

        Returns:
            Tuple[List[str], Dict[str, str]]: 可加载顺序和失败原因映射。
        """
        return self._resolve_dependencies(candidates, extra_available=extra_available)

    def load_candidate(self, plugin_id: str, candidate: PluginCandidate) -> Optional[PluginMeta]:
        """加载单个候选插件模块。

        Args:
            plugin_id: 插件 ID。
            candidate: 候选插件三元组。

        Returns:
            Optional[PluginMeta]: 加载成功的插件元数据；失败时返回 ``None``。
        """
        plugin_dir, manifest, plugin_path = candidate
        return self._load_single_plugin(plugin_id, plugin_dir, manifest, plugin_path)

    def _resolve_dependencies(
        self,
        candidates: Dict[str, PluginCandidate],
        extra_available: Optional[Set[str]] = None,
    ) -> Tuple[List[str], Dict[str, str]]:
        """拓扑排序解析加载顺序，返回 (有序列表, 失败项 {id: reason})。"""
        available = set(candidates.keys())
        satisfied_dependencies = set(extra_available or set())
        dep_graph: Dict[str, Set[str]] = {}
        failed: Dict[str, str] = {}

        for pid, (_, manifest, _) in candidates.items():
            raw_deps = manifest.get("dependencies", [])
            resolved: Set[str] = set()
            missing: List[str] = []
            for dep in raw_deps:
                dep_name = dep if isinstance(dep, str) else str(dep.get("name", ""))
                dep_name = dep_name.strip()
                if not dep_name or dep_name == pid:
                    continue
                if dep_name in available:
                    resolved.add(dep_name)
                elif dep_name in satisfied_dependencies:
                    continue
                else:
                    missing.append(dep_name)
            if missing:
                failed[pid] = f"缺少依赖: {', '.join(missing)}"
            dep_graph[pid] = resolved

        # 移除失败项
        for pid in failed:
            dep_graph.pop(pid, None)

        # Kahn 拓扑排序
        indegree = {pid: len(deps) for pid, deps in dep_graph.items()}
        reverse: Dict[str, Set[str]] = {pid: set() for pid in dep_graph}
        for pid, deps in dep_graph.items():
            for d in deps:
                if d in reverse:
                    reverse[d].add(pid)

        queue = deque(sorted(pid for pid, deg in indegree.items() if deg == 0))
        sorted_order: List[str] = []

        while queue:
            current = queue.popleft()
            sorted_order.append(current)
            for dependent in sorted(reverse.get(current, [])):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)

        cycle_plugins = {pid for pid, deg in indegree.items() if deg > 0}
        for pid in cycle_plugins:
            failed[pid] = "检测到循环依赖"

        return sorted_order, failed

    # ──── 单个插件加载 ────────────────────────────────────────

    def _load_single_plugin(
        self,
        plugin_id: str,
        plugin_dir: Path,
        manifest: Dict[str, Any],
        plugin_path: Path,
    ) -> Optional[PluginMeta]:
        """加载单个插件"""
        # 确保兼容层导入钩子已安装（旧版插件可能 import src.plugin_system）
        self._ensure_compat_hook()

        # 动态导入插件模块
        module_name = f"_maibot_plugin_{plugin_id}"
        spec = importlib.util.spec_from_file_location(module_name, str(plugin_path))
        if spec is None or spec.loader is None:
            logger.error(f"无法创建模块 spec: {plugin_path}")
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        plugin_parent_dir = plugin_dir.parent
        try:
            with self._temporary_sys_path_entry(plugin_parent_dir):
                spec.loader.exec_module(module)

                # 优先使用新版 create_plugin 工厂函数
                create_plugin = getattr(module, "create_plugin", None)
                if create_plugin is not None:
                    instance = create_plugin()
                    self._validate_sdk_plugin_contract(plugin_id, instance)
                    logger.info(f"插件 {plugin_id} v{manifest.get('version', '?')} 加载成功")
                    return PluginMeta(
                        plugin_id=plugin_id,
                        plugin_dir=str(plugin_dir),
                        module_name=module_name,
                        plugin_instance=instance,
                        manifest=manifest,
                    )

                # 回退：检测旧版 @register_plugin 标记的 BasePlugin 子类
                instance = self._try_load_legacy_plugin(module, plugin_id)
                if instance is not None:
                    logger.info(
                        f"插件 {plugin_id} v{manifest.get('version', '?')} 通过旧版兼容层加载成功（请尽快迁移到 maibot_sdk）"
                    )
                    return PluginMeta(
                        plugin_id=plugin_id,
                        plugin_dir=str(plugin_dir),
                        module_name=module_name,
                        plugin_instance=instance,
                        manifest=manifest,
                    )
        except Exception:
            sys.modules.pop(module_name, None)
            raise

        logger.error(f"插件 {plugin_id} 缺少 create_plugin 工厂函数且未检测到旧版 BasePlugin")
        return None

    @staticmethod
    def _validate_sdk_plugin_contract(plugin_id: str, instance: Any) -> None:
        """校验 SDK 插件的基础契约。

        Args:
            plugin_id: 当前插件 ID。
            instance: ``create_plugin()`` 返回的插件实例。

        Raises:
            TypeError: 当插件未覆盖必需生命周期方法或订阅声明不合法时抛出。
        """

        try:
            from maibot_sdk.plugin import MaiBotPlugin
        except ImportError:
            return

        if not isinstance(instance, MaiBotPlugin):
            return

        if type(instance).on_load is MaiBotPlugin.on_load:
            raise TypeError(f"插件 {plugin_id} 必须实现 on_load()")
        if type(instance).on_unload is MaiBotPlugin.on_unload:
            raise TypeError(f"插件 {plugin_id} 必须实现 on_unload()")
        if type(instance).on_config_update is MaiBotPlugin.on_config_update:
            raise TypeError(f"插件 {plugin_id} 必须实现 on_config_update()")

        instance.get_config_reload_subscriptions()

    @staticmethod
    @contextlib.contextmanager
    def _temporary_sys_path_entry(path: Path) -> Iterator[None]:
        """临时将路径放入 sys.path 头部，并在离开作用域后恢复。"""
        normalized_path = os.path.normpath(str(path))
        existing_paths = {os.path.normpath(entry) for entry in sys.path}
        inserted = normalized_path not in existing_paths
        if inserted:
            sys.path.insert(0, normalized_path)

        try:
            yield
        finally:
            if inserted:
                with contextlib.suppress(ValueError):
                    sys.path.remove(normalized_path)

    # ──── 旧版插件兼容 ────────────────────────────────────────

    def _ensure_compat_hook(self) -> None:
        """安装旧版 src.plugin_system 导入钩子（幂等）"""
        if self._compat_hook_installed:
            return
        try:
            from maibot_sdk.compat._import_hook import install_hook

            install_hook()
            self._compat_hook_installed = True
        except ImportError:
            logger.debug("maibot_sdk.compat 不可用，跳过导入钩子安装")

    @staticmethod
    def _try_load_legacy_plugin(module: Any, plugin_id: str) -> Optional[Any]:
        """尝试从模块中发现旧版 BasePlugin 子类并包装为 LegacyPluginAdapter"""
        # 方式 1: @register_plugin 装饰器设置的标记
        legacy_cls = getattr(module, "_legacy_plugin_class", None)

        # 方式 2: 扫描模块中所有 BasePlugin 子类
        if legacy_cls is None:
            try:
                from maibot_sdk.compat.base.base_plugin import BasePlugin as LegacyBasePlugin
            except ImportError:
                return None

            for attr_name in dir(module):
                obj = getattr(module, attr_name, None)
                if isinstance(obj, type) and issubclass(obj, LegacyBasePlugin) and obj is not LegacyBasePlugin:
                    legacy_cls = obj
                    break

        if legacy_cls is None:
            return None

        try:
            from maibot_sdk.compat.legacy_adapter import LegacyPluginAdapter

            legacy_instance = legacy_cls()
            return LegacyPluginAdapter(legacy_instance)
        except Exception as e:
            logger.error(f"旧版插件 {plugin_id} 适配失败: {e}", exc_info=True)
            return None
