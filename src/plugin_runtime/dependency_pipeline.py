"""插件 Python 依赖流水线。

负责在 Host 侧统一完成以下工作：
1. 扫描插件 Manifest；
2. 检测插件与主程序、插件与插件之间的 Python 依赖冲突；
3. 自动补装缺失的 Python 依赖，沿用已有包并让插件尝试加载；
4. 产出最终的拒绝加载列表，供运行时使用。
"""

from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import asyncio
import shutil
import subprocess
import sys
import tempfile
import tomllib

from packaging.utils import canonicalize_name

from src.common.paths import get_instance_root, project_metadata_path
from src.common.logger import get_logger
from src.plugin_runtime.compat_policy import is_force_plugin_compatibility_enabled
from src.plugin_runtime.runner.manifest_validator import ManifestValidator, PluginManifest


logger = get_logger("plugin_runtime.dependency_pipeline")


@dataclass(frozen=True)
class PackageDependencyUsage:
    """记录单个插件对某个 Python 包的依赖声明。"""

    package_name: str
    plugin_id: str
    version_spec: str


@dataclass(frozen=True)
class CombinedPackageRequirement:
    """表示一个已经合并后的 Python 包安装需求。"""

    package_name: str
    plugin_ids: Tuple[str, ...]
    requirement_text: str
    version_spec: str


@dataclass(frozen=True)
class PackageIndexSettings:
    """主程序 ``pyproject.toml`` 中与依赖安装相关的 uv 配置。"""

    index_urls: Tuple[str, ...]
    constraints: Tuple[str, ...]


@dataclass(frozen=True)
class DependencyPipelinePlan:
    """表示一次依赖分析后得到的计划。"""

    blocked_plugin_reasons: Dict[str, str]
    install_requirements: Tuple[CombinedPackageRequirement, ...]


@dataclass(frozen=True)
class DependencyPipelineResult:
    """表示一次依赖流水线执行后的结果。"""

    blocked_plugin_reasons: Dict[str, str]
    environment_changed: bool
    install_requirements: Tuple[CombinedPackageRequirement, ...]


class PluginDependencyPipeline:
    """插件依赖流水线。

    该类不负责插件启停，只负责对插件目录进行依赖分析，并在必要时
    使用 ``uv`` 为可加载插件补齐缺失的 Python 依赖。
    """

    def __init__(self, project_root: Optional[Path] = None) -> None:
        """初始化依赖流水线。

        Args:
            project_root: 项目根目录；留空时自动推断。
        """

        self._project_root: Path = project_root or get_instance_root()
        self._manifest_validator: ManifestValidator = ManifestValidator(
            project_root=self._project_root,
            validate_python_package_dependencies=False,
            log_errors=False,
            log_compat_warnings=False,
            force_plugin_compatibility=is_force_plugin_compatibility_enabled(),
        )

    async def execute(
        self,
        plugin_dirs: Iterable[Path],
        initial_blocked_plugin_reasons: Optional[Dict[str, str]] = None,
    ) -> DependencyPipelineResult:
        """执行完整的依赖分析与自动安装流程。

        Args:
            plugin_dirs: 需要扫描的插件根目录集合。
            initial_blocked_plugin_reasons: 依赖分析前已确定需要隔离的插件及原因。

        Returns:
            DependencyPipelineResult: 最终的阻止加载结果与环境变更状态。
        """

        plan = await asyncio.to_thread(
            self.build_plan, plugin_dirs,
            initial_blocked_plugin_reasons=initial_blocked_plugin_reasons,
        )
        if not plan.install_requirements:
            return DependencyPipelineResult(
                blocked_plugin_reasons=dict(plan.blocked_plugin_reasons),
                environment_changed=False,
                install_requirements=plan.install_requirements,
            )

        install_succeeded, error_message = await self._install_requirements(plan.install_requirements)
        if install_succeeded:
            return DependencyPipelineResult(
                blocked_plugin_reasons=dict(plan.blocked_plugin_reasons),
                environment_changed=True,
                install_requirements=plan.install_requirements,
            )

        affected_plugin_ids = sorted(
            {
                plugin_id
                for requirement in plan.install_requirements
                for plugin_id in requirement.plugin_ids
            }
        )
        # 安装失败记录错误，插件仍尝试加载，实际导入或执行异常由运行时报告。
        logger.error(
            f"自动安装 Python 依赖失败，插件仍将尝试加载 ({', '.join(affected_plugin_ids)}): {error_message}"
        )

        return DependencyPipelineResult(
            blocked_plugin_reasons=dict(plan.blocked_plugin_reasons),
            environment_changed=False,
            install_requirements=plan.install_requirements,
        )

    def build_plan(
        self,
        plugin_dirs: Iterable[Path],
        initial_blocked_plugin_reasons: Optional[Dict[str, str]] = None,
    ) -> DependencyPipelinePlan:
        """构建依赖分析计划。

        Args:
            plugin_dirs: 需要扫描的插件根目录集合。
            initial_blocked_plugin_reasons: 依赖分析前已确定需要隔离的插件及原因。

        Returns:
            DependencyPipelinePlan: 分析后的阻止加载列表与安装计划。
        """

        manifests = self._collect_manifests(plugin_dirs)
        blocked_plugin_reasons = {
            str(plugin_id or "").strip(): str(reason or "").strip()
            for plugin_id, reason in (initial_blocked_plugin_reasons or {}).items()
            if str(plugin_id or "").strip() and str(reason or "").strip()
        }
        for plugin_id, reason in self._detect_host_conflicts(manifests).items():
            self._append_block_reason(blocked_plugin_reasons, plugin_id, reason)
        plugin_conflict_reasons = self._detect_plugin_conflicts(manifests, blocked_plugin_reasons)
        for plugin_id, reason in plugin_conflict_reasons.items():
            self._append_block_reason(blocked_plugin_reasons, plugin_id, reason)

        install_requirements = self._build_install_requirements(manifests, blocked_plugin_reasons)
        return DependencyPipelinePlan(
            blocked_plugin_reasons=blocked_plugin_reasons,
            install_requirements=install_requirements,
        )

    def _collect_manifests(self, plugin_dirs: Iterable[Path]) -> Dict[str, PluginManifest]:
        """收集所有可成功解析的插件 Manifest。

        Args:
            plugin_dirs: 需要扫描的插件根目录集合。

        Returns:
            Dict[str, PluginManifest]: 以插件 ID 为键的 Manifest 映射。
        """

        manifests: Dict[str, PluginManifest] = {}
        for _plugin_path, manifest in self._manifest_validator.iter_plugin_manifests(plugin_dirs, enabled_only=True):
            manifests[manifest.id] = manifest
        return manifests

    def _detect_host_conflicts(self, manifests: Dict[str, PluginManifest]) -> Dict[str, str]:
        """检测待补装依赖与主程序约束之间的冲突。

        Args:
            manifests: 当前已解析到的插件 Manifest 映射。

        Returns:
            Dict[str, str]: 需要被阻止加载的插件及原因。
        """

        host_requirements = self._manifest_validator.load_host_dependency_requirements()
        blocked_plugin_reasons: Dict[str, str] = {}

        for manifest in manifests.values():
            for dependency in manifest.python_package_dependencies:
                package_specifier = self._manifest_validator.build_specifier_set(dependency.version_spec)
                if package_specifier is None:
                    self._append_block_reason(
                        blocked_plugin_reasons,
                        manifest.id,
                        f"Python 包依赖声明无效: {dependency.name}{dependency.version_spec}",
                    )
                    continue

                # 已有依赖直接沿用，不因插件声明的版本范围提前阻止加载。
                if self._manifest_validator.get_installed_package_version(dependency.name) is not None:
                    continue

                normalized_package_name = canonicalize_name(dependency.name)
                host_requirement = host_requirements.get(normalized_package_name)
                if host_requirement is None:
                    continue

                if self._manifest_validator.requirements_may_overlap(
                    host_requirement.specifier,
                    package_specifier,
                ):
                    continue

                host_specifier_text = str(host_requirement.specifier or "") or "任意版本"
                self._append_block_reason(
                    blocked_plugin_reasons,
                    manifest.id,
                    (
                        f"Python 包依赖与主程序冲突: {dependency.name} 需要 "
                        f"{dependency.version_spec}，主程序约束为 {host_specifier_text}"
                    ),
                )

        return blocked_plugin_reasons

    def _detect_plugin_conflicts(
        self,
        manifests: Dict[str, PluginManifest],
        blocked_plugin_reasons: Dict[str, str],
    ) -> Dict[str, str]:
        """检测待补装 Python 包在插件之间的约束冲突。

        Args:
            manifests: 当前已解析到的插件 Manifest 映射。
            blocked_plugin_reasons: 已经因为其他原因被阻止加载的插件。

        Returns:
            Dict[str, str]: 新增的插件冲突原因映射。
        """

        blocked_by_plugin_conflicts: Dict[str, str] = {}
        dependency_usages = self._collect_package_usages(manifests, blocked_plugin_reasons)

        for _package_name, usages in dependency_usages.items():
            display_package_name = usages[0].package_name
            if self._manifest_validator.get_installed_package_version(display_package_name) is not None:
                continue
            for index, left_usage in enumerate(usages):
                for right_usage in usages[index + 1 :]:
                    left_specifier = self._manifest_validator.build_specifier_set(left_usage.version_spec)
                    right_specifier = self._manifest_validator.build_specifier_set(right_usage.version_spec)
                    if left_specifier is None or right_specifier is None:
                        continue

                    if self._manifest_validator.requirements_may_overlap(left_specifier, right_specifier):
                        continue

                    left_reason = (
                        f"Python 包依赖冲突: 与插件 {right_usage.plugin_id} 在 {display_package_name} 上的约束不兼容 "
                        f"({left_usage.version_spec} vs {right_usage.version_spec})"
                    )
                    right_reason = (
                        f"Python 包依赖冲突: 与插件 {left_usage.plugin_id} 在 {display_package_name} 上的约束不兼容 "
                        f"({right_usage.version_spec} vs {left_usage.version_spec})"
                    )
                    self._append_block_reason(blocked_by_plugin_conflicts, left_usage.plugin_id, left_reason)
                    self._append_block_reason(blocked_by_plugin_conflicts, right_usage.plugin_id, right_reason)

        return blocked_by_plugin_conflicts

    def _collect_package_usages(
        self,
        manifests: Dict[str, PluginManifest],
        blocked_plugin_reasons: Dict[str, str],
    ) -> Dict[str, List[PackageDependencyUsage]]:
        """收集所有未被阻止加载插件的包依赖声明。

        Args:
            manifests: 当前已解析到的插件 Manifest 映射。
            blocked_plugin_reasons: 已经被阻止加载的插件及原因。

        Returns:
            Dict[str, List[PackageDependencyUsage]]: 按规范化包名分组后的依赖声明。
        """

        dependency_usages: Dict[str, List[PackageDependencyUsage]] = {}
        for manifest in manifests.values():
            if manifest.id in blocked_plugin_reasons:
                continue

            for dependency in manifest.python_package_dependencies:
                normalized_package_name = canonicalize_name(dependency.name)
                dependency_usages.setdefault(normalized_package_name, []).append(
                    PackageDependencyUsage(
                        package_name=dependency.name,
                        plugin_id=manifest.id,
                        version_spec=dependency.version_spec,
                    )
                )

        return dependency_usages

    def _build_install_requirements(
        self,
        manifests: Dict[str, PluginManifest],
        blocked_plugin_reasons: Dict[str, str],
    ) -> Tuple[CombinedPackageRequirement, ...]:
        """构建需要安装到当前环境的 Python 包需求列表。

        Args:
            manifests: 当前已解析到的插件 Manifest 映射。
            blocked_plugin_reasons: 已经被阻止加载的插件及原因。

        Returns:
            Tuple[CombinedPackageRequirement, ...]: 需要补装的缺失依赖列表。
        """

        combined_requirements: List[CombinedPackageRequirement] = []
        dependency_usages = self._collect_package_usages(manifests, blocked_plugin_reasons)

        for usages in dependency_usages.values():
            merged_specifier_text = self._merge_specifier_texts([usage.version_spec for usage in usages])
            package_name = usages[0].package_name
            requirement_text = f"{package_name}{merged_specifier_text}"
            installed_version = self._manifest_validator.get_installed_package_version(package_name)
            if installed_version is not None:
                continue

            combined_requirements.append(
                CombinedPackageRequirement(
                    package_name=package_name,
                    plugin_ids=tuple(sorted({usage.plugin_id for usage in usages})),
                    requirement_text=requirement_text,
                    version_spec=merged_specifier_text,
                )
            )

        return tuple(sorted(combined_requirements, key=lambda requirement: canonicalize_name(requirement.package_name)))

    @staticmethod
    def _merge_specifier_texts(specifier_texts: Sequence[str]) -> str:
        """合并多个版本约束文本。

        Args:
            specifier_texts: 需要合并的版本约束文本序列。

        Returns:
            str: 合并后的版本约束文本。
        """

        merged_parts: List[str] = []
        for specifier_text in specifier_texts:
            for part in str(specifier_text or "").split(","):
                normalized_part = part.strip()
                if not normalized_part or normalized_part in merged_parts:
                    continue
                merged_parts.append(normalized_part)
        return f"{','.join(merged_parts)}" if merged_parts else ""

    async def _install_requirements(self, requirements: Sequence[CombinedPackageRequirement]) -> Tuple[bool, str]:
        """安装指定的 Python 包需求列表。

        Args:
            requirements: 需要安装的依赖列表。

        Returns:
            Tuple[bool, str]: 安装是否成功，以及错误摘要。
        """

        requirement_texts = [requirement.requirement_text for requirement in requirements]
        if not requirement_texts:
            return True, ""

        logger.info(f"开始自动安装插件 Python 依赖: {', '.join(requirement_texts)}")
        try:
            index_settings = await asyncio.to_thread(self._load_package_index_settings)
            installed_constraints = await asyncio.to_thread(self._load_installed_package_constraints)
        except Exception as exc:
            return False, f"读取依赖安装配置或当前环境版本失败，已取消自动安装: {exc}"

        # 与主程序一致：按索引优先级逐个源单独安装，前一个源找不到或连不上就换下一个源，任意一个成功即可。
        # 不能让 uv 同时查询多个索引，否则任一索引网络异常（如国内访问 pypi.org 超时）都会让整次安装失败。
        index_urls: Sequence[Optional[str]] = index_settings.index_urls or (None,)
        install_errors: List[str] = []
        with tempfile.TemporaryDirectory(prefix="maibot-plugin-deps-") as temp_dir:
            # 新包的间接依赖也可能要求升级或降级 Pillow 等宿主依赖。
            # 固定全部已安装版本，让此类冲突在解析阶段失败，禁止进入卸载阶段。
            constraint_path = await asyncio.to_thread(
                self._write_constraint_file,
                Path(temp_dir),
                (*index_settings.constraints, *installed_constraints),
            )
            for attempt_index, index_url in enumerate(index_urls):
                source_name = index_url or "默认源"
                command = self._build_install_command(requirement_texts, index_url, constraint_path)

                try:
                    completed_process = await asyncio.to_thread(
                        subprocess.run,
                        command,
                        capture_output=True,
                        check=False,
                        cwd=self._project_root,
                        encoding="utf-8",
                        errors="replace",
                        text=True,
                    )
                except Exception as exc:
                    return False, str(exc)

                if completed_process.returncode == 0:
                    logger.info(f"插件 Python 依赖自动安装完成（源: {source_name}）")
                    return True, ""

                output = self._summarize_install_error(completed_process.stdout, completed_process.stderr)
                output = output or f"命令执行失败，退出码 {completed_process.returncode}"
                install_errors.append(f"[{source_name}] {output}")
                if attempt_index + 1 < len(index_urls):
                    logger.warning(f"从 {source_name} 安装插件 Python 依赖失败，改用下一个源重试: {output}")

        return False, "；".join(install_errors)

    @staticmethod
    def _load_installed_package_constraints() -> Tuple[str, ...]:
        """快照当前解释器的已安装版本，同时约束直接依赖与间接依赖。"""

        constraints = set()
        for distribution in importlib_metadata.distributions():
            package_name = distribution.metadata["Name"]
            if not package_name:
                raise ValueError(f"已安装包缺少 Name 元数据: {distribution}")
            constraints.add(f"{canonicalize_name(package_name)}=={distribution.version}")
        return tuple(sorted(constraints))

    def _load_package_index_settings(self) -> PackageIndexSettings:
        """读取主程序 ``pyproject.toml`` 中 ``[tool.uv]`` 的索引顺序与约束依赖。

        索引顺序与 uv 保持一致：非默认索引按声明顺序在前，``default = true`` 的索引排在最后。

        Returns:
            PackageIndexSettings: 按优先级排列的索引地址与约束依赖。
        """

        with project_metadata_path(self._project_root).open("rb") as pyproject_file:
            uv_config: Dict[str, Any] = tomllib.load(pyproject_file).get("tool", {}).get("uv", {})

        raw_indexes: List[Dict[str, Any]] = uv_config.get("index", [])
        index_urls = [str(index["url"]) for index in raw_indexes if not index.get("default", False)]
        index_urls.extend(str(index["url"]) for index in raw_indexes if index.get("default", False))
        return PackageIndexSettings(
            index_urls=tuple(index_urls),
            constraints=tuple(str(constraint) for constraint in uv_config.get("constraint-dependencies", [])),
        )

    @staticmethod
    def _write_constraint_file(directory: Path, constraints: Sequence[str]) -> Optional[Path]:
        """把主程序的约束依赖写入临时约束文件。

        Args:
            directory: 临时文件所在目录。
            constraints: 约束依赖文本序列。

        Returns:
            Optional[Path]: 约束文件路径；没有约束依赖时返回 ``None``。
        """

        if not constraints:
            return None
        constraint_path = directory / "constraints.txt"
        constraint_path.write_text("\n".join(constraints) + "\n", encoding="utf-8")
        return constraint_path

    @staticmethod
    def _build_install_command(
        requirement_texts: Sequence[str],
        index_url: Optional[str],
        constraint_path: Optional[Path],
    ) -> List[str]:
        """构造依赖安装命令。

        Args:
            requirement_texts: 待安装的依赖文本序列。
            index_url: 本次安装只使用的索引地址；为 ``None`` 时沿用安装工具自身的索引配置。
            constraint_path: 主程序约束依赖文件路径；为 ``None`` 时不附加约束。

        Returns:
            List[str]: 适用于 ``subprocess.run`` 的命令参数列表。
        """

        if shutil.which("uv"):
            command = ["uv", "pip", "install", "--python", sys.executable]
            if index_url:
                # uv 会把 pyproject.toml 中的索引与命令行索引合并，需要 --no-config 才能只查这一个源；
                # 被跳过的 constraint-dependencies 由约束文件显式传入。
                command.extend(["--no-config", "--index-url", index_url])
        else:
            command = [sys.executable, "-m", "pip", "install"]
            if index_url:
                command.extend(["--index-url", index_url])
        if constraint_path is not None:
            command.extend(["--constraint", str(constraint_path)])
        return [*command, *requirement_texts]

    @staticmethod
    def _summarize_install_error(stdout: str, stderr: str) -> str:
        """提炼安装失败输出。

        Args:
            stdout: 标准输出内容。
            stderr: 标准错误内容。

        Returns:
            str: 简短的错误摘要。
        """

        merged_output = "\n".join(part.strip() for part in (stderr, stdout) if part and part.strip()).strip()
        if not merged_output:
            return ""
        lines = [line.strip() for line in merged_output.splitlines() if line.strip()]
        return " | ".join(lines[-5:])

    @staticmethod
    def _append_block_reason(
        blocked_plugin_reasons: Dict[str, str],
        plugin_id: str,
        reason: str,
    ) -> None:
        """向阻止加载映射中追加原因。

        Args:
            blocked_plugin_reasons: 待更新的阻止加载映射。
            plugin_id: 目标插件 ID。
            reason: 需要追加的原因文本。
        """

        existing_reason = blocked_plugin_reasons.get(plugin_id)
        if existing_reason is None:
            blocked_plugin_reasons[plugin_id] = reason
            return

        existing_parts = [part.strip() for part in existing_reason.split("；") if part.strip()]
        if reason in existing_parts:
            return
        blocked_plugin_reasons[plugin_id] = f"{existing_reason}；{reason}"
