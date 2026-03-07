"""插件加载器

在 Runner 进程中负责发现和加载插件。
插件通过 SDK 编写，不再 import src.*。
支持：manifest 校验、依赖解析（拓扑排序）、生命周期钩子。
"""

from collections import deque
from typing import Any

import importlib
import importlib.util
import json
import os
import sys

from src.common.logger import get_logger
from src.plugin_runtime.runner.manifest_validator import ManifestValidator

logger = get_logger("plugin_runtime.runner.plugin_loader")


class PluginMeta:
    """加载后的插件元数据"""

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: str,
        plugin_instance: Any,
        manifest: dict[str, Any],
    ):
        self.plugin_id = plugin_id
        self.plugin_dir = plugin_dir
        self.instance = plugin_instance
        self.manifest = manifest
        self.version = manifest.get("version", "1.0.0")
        self.capabilities_required = manifest.get("capabilities", [])
        self.dependencies: list[str] = self._extract_dependencies(manifest)

    @staticmethod
    def _extract_dependencies(manifest: dict[str, Any]) -> list[str]:
        raw = manifest.get("dependencies", [])
        result: list[str] = []
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

    def __init__(self, host_version: str = ""):
        self._loaded_plugins: dict[str, PluginMeta] = {}
        self._failed_plugins: dict[str, str] = {}
        self._manifest_validator = ManifestValidator(host_version=host_version)

    def discover_and_load(self, plugin_dirs: list[str]) -> list[PluginMeta]:
        """扫描多个目录并加载所有插件（含依赖排序和 manifest 校验）

        Args:
            plugin_dirs: 插件目录列表

        Returns:
            成功加载的插件元数据列表（按依赖顺序）
        """
        # 第一阶段：发现并校验 manifest
        candidates: dict[str, tuple[str, dict[str, Any], str]] = {}  # id -> (dir, manifest, plugin_path)
        for base_dir in plugin_dirs:
            if not os.path.isdir(base_dir):
                logger.warning(f"插件目录不存在: {base_dir}")
                continue

            for entry in os.listdir(base_dir):
                plugin_dir = os.path.join(base_dir, entry)
                if not os.path.isdir(plugin_dir):
                    continue

                manifest_path = os.path.join(plugin_dir, "_manifest.json")
                plugin_path = os.path.join(plugin_dir, "plugin.py")

                if not os.path.exists(manifest_path) or not os.path.exists(plugin_path):
                    continue

                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                except Exception as e:
                    self._failed_plugins[entry] = f"manifest 解析失败: {e}"
                    logger.error(f"插件 {entry} manifest 解析失败: {e}")
                    continue

                if not self._manifest_validator.validate(manifest):
                    errors = "; ".join(self._manifest_validator.errors)
                    self._failed_plugins[entry] = f"manifest 校验失败: {errors}"
                    continue

                plugin_id = manifest.get("name", entry)
                candidates[plugin_id] = (plugin_dir, manifest, plugin_path)

        # 第二阶段：依赖解析（拓扑排序）
        load_order, failed_deps = self._resolve_dependencies(candidates)

        for pid, reason in failed_deps.items():
            self._failed_plugins[pid] = reason
            logger.error(f"插件 {pid} 依赖解析失败: {reason}")

        # 第三阶段：按依赖顺序加载
        results = []
        for plugin_id in load_order:
            plugin_dir, manifest, plugin_path = candidates[plugin_id]
            try:
                if meta := self._load_single_plugin(plugin_id, plugin_dir, manifest, plugin_path):
                    self._loaded_plugins[meta.plugin_id] = meta
                    results.append(meta)
            except Exception as e:
                self._failed_plugins[plugin_id] = str(e)
                logger.error(f"加载插件失败 [{plugin_id}]: {e}", exc_info=True)

        return results

    def get_plugin(self, plugin_id: str) -> PluginMeta | None:
        """获取已加载的插件"""
        return self._loaded_plugins.get(plugin_id)

    def list_plugins(self) -> list[str]:
        """列出所有已加载的插件 ID"""
        return list(self._loaded_plugins.keys())

    @property
    def failed_plugins(self) -> dict[str, str]:
        return dict(self._failed_plugins)

    # ──── 依赖解析 ────────────────────────────────────────────

    def _resolve_dependencies(
        self,
        candidates: dict[str, tuple[str, dict[str, Any], str]],
    ) -> tuple[list[str], dict[str, str]]:
        """拓扑排序解析加载顺序，返回 (有序列表, 失败项 {id: reason})。"""
        available = set(candidates.keys())
        dep_graph: dict[str, set[str]] = {}
        failed: dict[str, str] = {}

        for pid, (_, manifest, _) in candidates.items():
            raw_deps = manifest.get("dependencies", [])
            resolved: set[str] = set()
            missing: list[str] = []
            for dep in raw_deps:
                dep_name = dep if isinstance(dep, str) else str(dep.get("name", ""))
                dep_name = dep_name.strip()
                if not dep_name or dep_name == pid:
                    continue
                if dep_name in available:
                    resolved.add(dep_name)
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
        reverse: dict[str, set[str]] = {pid: set() for pid in dep_graph}
        for pid, deps in dep_graph.items():
            for d in deps:
                if d in reverse:
                    reverse[d].add(pid)

        queue = deque(sorted(pid for pid, deg in indegree.items() if deg == 0))
        sorted_order: list[str] = []

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
        plugin_dir: str,
        manifest: dict[str, Any],
        plugin_path: str,
    ) -> PluginMeta | None:
        """加载单个插件"""
        # 动态导入插件模块
        module_name = f"_maibot_plugin_{plugin_id}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_path)
        if spec is None or spec.loader is None:
            logger.error(f"无法创建模块 spec: {plugin_path}")
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # 调用工厂函数创建插件实例
        create_plugin = getattr(module, "create_plugin", None)
        if create_plugin is None:
            logger.error(f"插件 {plugin_id} 缺少 create_plugin 工厂函数")
            return None

        instance = create_plugin()

        logger.info(f"插件 {plugin_id} v{manifest.get('version', '?')} 加载成功")

        return PluginMeta(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            plugin_instance=instance,
            manifest=manifest,
        )
