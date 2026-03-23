"""Manifest 校验与解析。

集中负责插件 ``_manifest.json`` 的读取、结构校验、运行时兼容性判断，
以及插件依赖/Python 包依赖的解析逻辑。
"""

from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional, Tuple, Union

import json
import re
import tomllib

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.runner.manifest_validator")

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HTTP_URL_PATTERN = re.compile(r"^https?://.+$")


class VersionComparator:
    """语义化版本号比较器。"""

    @staticmethod
    def normalize_version(version: str) -> str:
        """将版本号规范化为三段式语义版本字符串。

        Args:
            version: 原始版本号字符串。

        Returns:
            str: 规范化后的 ``major.minor.patch`` 形式版本号。
                当输入为空或格式非法时返回 ``0.0.0``。
        """
        if not version:
            return "0.0.0"

        normalized = re.sub(r"-snapshot\.\d+", "", str(version).strip())
        if not re.match(r"^\d+(\.\d+){0,2}$", normalized):
            return "0.0.0"

        parts = normalized.split(".")
        while len(parts) < 3:
            parts.append("0")
        return ".".join(parts[:3])

    @staticmethod
    def parse_version(version: str) -> Tuple[int, int, int]:
        """将版本字符串解析为可比较的整数元组。

        Args:
            version: 原始版本号字符串。

        Returns:
            Tuple[int, int, int]: 三段式版本号对应的整数元组。
                当解析失败时返回 ``(0, 0, 0)``。
        """
        normalized = VersionComparator.normalize_version(version)
        try:
            parts = normalized.split(".")
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return (0, 0, 0)

    @staticmethod
    def compare(v1: str, v2: str) -> int:
        """比较两个版本号的大小关系。

        Args:
            v1: 第一个版本号。
            v2: 第二个版本号。

        Returns:
            int: ``-1`` 表示 ``v1 < v2``，``1`` 表示 ``v1 > v2``，
                ``0`` 表示两者相等。
        """
        t1 = VersionComparator.parse_version(v1)
        t2 = VersionComparator.parse_version(v2)
        if t1 < t2:
            return -1
        if t1 > t2:
            return 1
        return 0

    @staticmethod
    def is_in_range(version: str, min_version: str = "", max_version: str = "") -> Tuple[bool, str]:
        """判断版本号是否落在给定闭区间内。

        Args:
            version: 待检查的版本号。
            min_version: 允许的最小版本号，留空表示不限制下界。
            max_version: 允许的最大版本号，留空表示不限制上界。

        Returns:
            Tuple[bool, str]: 第一项表示是否满足要求，第二项为失败原因；
                当校验通过时第二项为空字符串。
        """
        if not min_version and not max_version:
            return True, ""

        normalized_version = VersionComparator.normalize_version(version)
        if min_version:
            normalized_min_version = VersionComparator.normalize_version(min_version)
            if VersionComparator.compare(normalized_version, normalized_min_version) < 0:
                return False, f"版本 {normalized_version} 低于最小要求 {normalized_min_version}"
        if max_version:
            normalized_max_version = VersionComparator.normalize_version(max_version)
            if VersionComparator.compare(normalized_version, normalized_max_version) > 0:
                return False, f"版本 {normalized_version} 高于最大支持 {normalized_max_version}"
        return True, ""

    @staticmethod
    def is_valid_semver(version: str) -> bool:
        """判断字符串是否为严格三段式语义版本号。

        Args:
            version: 待检查的版本号字符串。

        Returns:
            bool: 是否满足 ``X.Y.Z`` 格式。
        """
        return bool(_SEMVER_PATTERN.fullmatch(str(version or "").strip()))


class _StrictManifestModel(BaseModel):
    """Manifest 解析使用的严格基类模型。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ManifestAuthor(_StrictManifestModel):
    """插件作者信息。"""

    name: str = Field(description="作者名称")
    url: str = Field(description="作者主页地址")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """校验作者名称。

        Args:
            value: 原始作者名称。

        Returns:
            str: 规范化后的作者名称。

        Raises:
            ValueError: 当字段为空时抛出。
        """
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """校验作者主页地址。

        Args:
            value: 原始主页地址。

        Returns:
            str: 规范化后的主页地址。

        Raises:
            ValueError: 当字段为空或不是 HTTP/HTTPS URL 时抛出。
        """
        if not value:
            raise ValueError("不能为空")
        if not _HTTP_URL_PATTERN.fullmatch(value):
            raise ValueError("必须为 http:// 或 https:// 开头的 URL")
        return value


class ManifestUrls(_StrictManifestModel):
    """插件相关链接集合。"""

    repository: str = Field(description="插件仓库地址")
    homepage: Optional[str] = Field(default=None, description="插件主页地址")
    documentation: Optional[str] = Field(default=None, description="插件文档地址")
    issues: Optional[str] = Field(default=None, description="插件问题反馈地址")

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, value: str) -> str:
        """校验仓库地址。

        Args:
            value: 原始仓库地址。

        Returns:
            str: 规范化后的仓库地址。

        Raises:
            ValueError: 当字段为空或不是 HTTP/HTTPS URL 时抛出。
        """
        if not value:
            raise ValueError("不能为空")
        if not _HTTP_URL_PATTERN.fullmatch(value):
            raise ValueError("必须为 http:// 或 https:// 开头的 URL")
        return value

    @field_validator("homepage", "documentation", "issues")
    @classmethod
    def _validate_optional_url(cls, value: Optional[str]) -> Optional[str]:
        """校验可选链接字段。

        Args:
            value: 原始链接值。

        Returns:
            Optional[str]: 合法的链接值。

        Raises:
            ValueError: 当提供的值不是 HTTP/HTTPS URL 时抛出。
        """
        if value is None:
            return None
        if not value:
            raise ValueError("不能为空字符串")
        if not _HTTP_URL_PATTERN.fullmatch(value):
            raise ValueError("必须为 http:// 或 https:// 开头的 URL")
        return value


class ManifestVersionRange(_StrictManifestModel):
    """版本闭区间声明。"""

    min_version: str = Field(description="最小版本，闭区间")
    max_version: str = Field(description="最大版本，闭区间")

    @field_validator("min_version", "max_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        """校验版本号格式。

        Args:
            value: 原始版本号。

        Returns:
            str: 合法的版本号。

        Raises:
            ValueError: 当版本号不是严格三段式语义版本时抛出。
        """
        if not VersionComparator.is_valid_semver(value):
            raise ValueError("必须为严格三段式版本号，例如 1.0.0")
        return value

    @model_validator(mode="after")
    def _validate_range(self) -> "ManifestVersionRange":
        """校验版本区间上下界关系。

        Returns:
            ManifestVersionRange: 当前对象本身。

        Raises:
            ValueError: 当最小版本大于最大版本时抛出。
        """
        if VersionComparator.compare(self.min_version, self.max_version) > 0:
            raise ValueError("min_version 不能大于 max_version")
        return self


class ManifestI18n(_StrictManifestModel):
    """国际化配置。"""

    default_locale: str = Field(description="默认语言")
    locales_path: Optional[str] = Field(default=None, description="语言资源目录")
    supported_locales: List[str] = Field(default_factory=list, description="支持的语言列表")

    @field_validator("default_locale")
    @classmethod
    def _validate_default_locale(cls, value: str) -> str:
        """校验默认语言。

        Args:
            value: 原始默认语言。

        Returns:
            str: 规范化后的默认语言。

        Raises:
            ValueError: 当字段为空时抛出。
        """
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("locales_path")
    @classmethod
    def _validate_locales_path(cls, value: Optional[str]) -> Optional[str]:
        """校验语言资源目录。

        Args:
            value: 原始语言资源目录。

        Returns:
            Optional[str]: 合法的目录值。

        Raises:
            ValueError: 当值为空字符串时抛出。
        """
        if value is None:
            return None
        if not value:
            raise ValueError("不能为空字符串")
        return value

    @field_validator("supported_locales")
    @classmethod
    def _validate_supported_locales(cls, value: List[str]) -> List[str]:
        """校验支持语言列表。

        Args:
            value: 原始语言列表。

        Returns:
            List[str]: 去重后的语言列表。

        Raises:
            ValueError: 当列表项为空时抛出。
        """
        normalized_locales: List[str] = []
        for locale in value:
            normalized_locale = str(locale or "").strip()
            if not normalized_locale:
                raise ValueError("语言列表中存在空值")
            if normalized_locale not in normalized_locales:
                normalized_locales.append(normalized_locale)
        return normalized_locales

    @model_validator(mode="after")
    def _validate_default_locale_membership(self) -> "ManifestI18n":
        """校验默认语言是否位于支持列表中。

        Returns:
            ManifestI18n: 当前对象本身。

        Raises:
            ValueError: 当 ``supported_locales`` 非空但未包含 ``default_locale`` 时抛出。
        """
        if self.supported_locales and self.default_locale not in self.supported_locales:
            raise ValueError("default_locale 必须包含在 supported_locales 中")
        return self


class PluginDependencyDefinition(_StrictManifestModel):
    """插件级依赖声明。"""

    type: Literal["plugin"] = Field(description="依赖类型")
    id: str = Field(description="依赖插件 ID")
    version_spec: str = Field(description="版本约束表达式")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        """校验依赖插件 ID。

        Args:
            value: 原始依赖插件 ID。

        Returns:
            str: 合法的依赖插件 ID。

        Raises:
            ValueError: 当 ID 不符合规则时抛出。
        """
        if not _PLUGIN_ID_PATTERN.fullmatch(value):
            raise ValueError("必须使用小写字母/数字，并以点号或横线分隔，例如 github.author.plugin")
        return value

    @field_validator("version_spec")
    @classmethod
    def _validate_version_spec(cls, value: str) -> str:
        """校验插件依赖版本约束。

        Args:
            value: 原始版本约束表达式。

        Returns:
            str: 合法的版本约束表达式。

        Raises:
            ValueError: 当表达式无效时抛出。
        """
        if not value:
            raise ValueError("不能为空")
        try:
            SpecifierSet(value)
        except InvalidSpecifier as exc:
            raise ValueError(f"无效的版本约束: {exc}") from exc
        return value


class PythonPackageDependencyDefinition(_StrictManifestModel):
    """Python 包依赖声明。"""

    type: Literal["python_package"] = Field(description="依赖类型")
    name: str = Field(description="Python 包名")
    version_spec: str = Field(description="版本约束表达式")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """校验 Python 包名。

        Args:
            value: 原始包名。

        Returns:
            str: 合法的包名。

        Raises:
            ValueError: 当包名不合法时抛出。
        """
        if not _PACKAGE_NAME_PATTERN.fullmatch(value):
            raise ValueError("包名只能包含字母、数字、点号、下划线和横线")
        return value

    @field_validator("version_spec")
    @classmethod
    def _validate_version_spec(cls, value: str) -> str:
        """校验 Python 包版本约束。

        Args:
            value: 原始版本约束表达式。

        Returns:
            str: 合法的版本约束表达式。

        Raises:
            ValueError: 当表达式无效时抛出。
        """
        if not value:
            raise ValueError("不能为空")
        try:
            Requirement(f"placeholder{value}")
        except InvalidRequirement as exc:
            raise ValueError(f"无效的版本约束: {exc}") from exc
        return value


ManifestDependencyDefinition = Annotated[
    Union[PluginDependencyDefinition, PythonPackageDependencyDefinition],
    Field(discriminator="type"),
]


class PluginManifest(_StrictManifestModel):
    """插件 Manifest v2 强类型模型。"""

    manifest_version: Literal[2] = Field(description="Manifest 协议版本")
    version: str = Field(description="插件版本")
    name: str = Field(description="插件展示名称")
    description: str = Field(description="插件描述")
    author: ManifestAuthor = Field(description="插件作者信息")
    license: str = Field(description="插件协议")
    urls: ManifestUrls = Field(description="插件相关链接")
    host_application: ManifestVersionRange = Field(description="Host 兼容区间")
    sdk: ManifestVersionRange = Field(description="SDK 兼容区间")
    dependencies: List[ManifestDependencyDefinition] = Field(default_factory=list, description="依赖声明")
    capabilities: List[str] = Field(description="插件声明的能力请求")
    i18n: ManifestI18n = Field(description="国际化配置")
    id: str = Field(description="稳定插件 ID")

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        """校验插件版本号格式。

        Args:
            value: 原始插件版本号。

        Returns:
            str: 合法的插件版本号。

        Raises:
            ValueError: 当版本号不是严格三段式语义版本时抛出。
        """
        if not VersionComparator.is_valid_semver(value):
            raise ValueError("必须为严格三段式版本号，例如 1.0.0")
        return value

    @field_validator("name", "description", "license", "id")
    @classmethod
    def _validate_required_string(cls, value: str, info: Any) -> str:
        """校验必填字符串字段。

        Args:
            value: 原始字段值。
            info: Pydantic 字段上下文。

        Returns:
            str: 合法的字段值。

        Raises:
            ValueError: 当字段为空或格式不合法时抛出。
        """
        if not value:
            raise ValueError("不能为空")
        if info.field_name == "id" and not _PLUGIN_ID_PATTERN.fullmatch(value):
            raise ValueError("必须使用小写字母/数字，并以点号或横线分隔，例如 github.author.plugin")
        return value

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, value: List[str]) -> List[str]:
        """校验能力声明列表。

        Args:
            value: 原始能力声明列表。

        Returns:
            List[str]: 去重后的能力列表。

        Raises:
            ValueError: 当列表为空项或能力名为空时抛出。
        """
        normalized_capabilities: List[str] = []
        for capability in value:
            normalized_capability = str(capability or "").strip()
            if not normalized_capability:
                raise ValueError("capabilities 中存在空能力名")
            if normalized_capability not in normalized_capabilities:
                normalized_capabilities.append(normalized_capability)
        return normalized_capabilities

    @model_validator(mode="after")
    def _validate_dependencies(self) -> "PluginManifest":
        """校验依赖声明集合。

        Returns:
            PluginManifest: 当前对象本身。

        Raises:
            ValueError: 当依赖项重复或插件依赖自身时抛出。
        """
        plugin_dependency_ids: set[str] = set()
        python_package_names: set[str] = set()

        for dependency in self.dependencies:
            if isinstance(dependency, PluginDependencyDefinition):
                if dependency.id == self.id:
                    raise ValueError("dependencies 中的插件依赖不能依赖自身")
                if dependency.id in plugin_dependency_ids:
                    raise ValueError(f"存在重复的插件依赖声明: {dependency.id}")
                plugin_dependency_ids.add(dependency.id)
                continue

            normalized_package_name = canonicalize_name(dependency.name)
            if normalized_package_name in python_package_names:
                raise ValueError(f"存在重复的 Python 包依赖声明: {dependency.name}")
            python_package_names.add(normalized_package_name)

        return self

    @property
    def plugin_dependencies(self) -> List[PluginDependencyDefinition]:
        """返回插件级依赖列表。

        Returns:
            List[PluginDependencyDefinition]: 所有 ``type=plugin`` 的依赖项。
        """
        return [dependency for dependency in self.dependencies if isinstance(dependency, PluginDependencyDefinition)]

    @property
    def python_package_dependencies(self) -> List[PythonPackageDependencyDefinition]:
        """返回 Python 包依赖列表。

        Returns:
            List[PythonPackageDependencyDefinition]: 所有 ``type=python_package`` 的依赖项。
        """
        return [
            dependency
            for dependency in self.dependencies
            if isinstance(dependency, PythonPackageDependencyDefinition)
        ]

    @property
    def plugin_dependency_ids(self) -> List[str]:
        """返回插件级依赖的插件 ID 列表。

        Returns:
            List[str]: 所有插件级依赖的插件 ID。
        """
        return [dependency.id for dependency in self.plugin_dependencies]


class ManifestValidator:
    """严格的插件 Manifest v2 校验器。"""

    SUPPORTED_MANIFEST_VERSIONS = [2]

    def __init__(
        self,
        host_version: str = "",
        sdk_version: str = "",
        project_root: Optional[Path] = None,
    ) -> None:
        """初始化 Manifest 校验器。

        Args:
            host_version: 当前 Host 版本号；留空时自动从主程序 ``pyproject.toml`` 读取。
            sdk_version: 当前 SDK 版本号；留空时自动从运行环境中探测。
            project_root: 项目根目录；留空时自动推断。
        """
        self._project_root: Path = project_root or self._resolve_project_root()
        self._host_version: str = host_version or self._detect_default_host_version(self._project_root)
        self._sdk_version: str = sdk_version or self._detect_default_sdk_version(self._project_root)
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate(self, manifest: Dict[str, Any]) -> bool:
        """校验 manifest 数据，返回是否通过。

        Args:
            manifest: 待校验的 Manifest 原始字典。

        Returns:
            bool: 校验是否通过。
        """
        return self.parse_manifest(manifest) is not None

    def parse_manifest(self, manifest: Dict[str, Any]) -> Optional[PluginManifest]:
        """解析并校验 manifest 字典。

        Args:
            manifest: 待解析的 Manifest 原始字典。

        Returns:
            Optional[PluginManifest]: 解析成功时返回强类型 Manifest；失败时返回 ``None``。
        """
        self.errors.clear()
        self.warnings.clear()

        try:
            parsed_manifest = PluginManifest.model_validate(manifest)
        except ValidationError as exc:
            self.errors.extend(self._format_validation_errors(exc))
            self._log_errors()
            return None

        self._validate_runtime_compatibility(parsed_manifest)
        if self.errors:
            self._log_errors()
            return None

        return parsed_manifest

    def load_from_plugin_path(self, plugin_path: Path, require_entrypoint: bool = True) -> Optional[PluginManifest]:
        """从插件目录读取并解析 manifest。

        Args:
            plugin_path: 单个插件目录路径。
            require_entrypoint: 是否要求目录内存在 ``plugin.py`` 入口文件。

        Returns:
            Optional[PluginManifest]: 解析成功时返回强类型 Manifest；失败时返回 ``None``。
        """
        self.errors.clear()
        self.warnings.clear()

        manifest_path = plugin_path / "_manifest.json"
        entrypoint_path = plugin_path / "plugin.py"

        if not manifest_path.is_file():
            self.errors.append("缺少 _manifest.json")
            return None
        if require_entrypoint and not entrypoint_path.is_file():
            self.errors.append("缺少 plugin.py")
            return None

        try:
            with manifest_path.open("r", encoding="utf-8") as manifest_file:
                manifest_data = json.load(manifest_file)
        except Exception as exc:
            self.errors.append(f"manifest 解析失败: {exc}")
            self._log_errors()
            return None

        if not isinstance(manifest_data, dict):
            self.errors.append("manifest 顶层必须为 JSON 对象")
            self._log_errors()
            return None

        return self.parse_manifest(manifest_data)

    def iter_plugin_manifests(
        self,
        plugin_dirs: Iterable[Path],
        require_entrypoint: bool = True,
    ) -> Iterable[Tuple[Path, PluginManifest]]:
        """扫描插件根目录并迭代所有可成功解析的 Manifest。

        Args:
            plugin_dirs: 一个或多个插件根目录。
            require_entrypoint: 是否要求每个插件目录内存在 ``plugin.py``。

        Yields:
            Tuple[Path, PluginManifest]: ``(插件目录路径, 解析结果)`` 二元组。
        """
        for plugin_root in plugin_dirs:
            normalized_root = Path(plugin_root).resolve()
            if not normalized_root.is_dir():
                continue

            for candidate_path in sorted(entry.resolve() for entry in normalized_root.iterdir() if entry.is_dir()):
                parsed_manifest = self.load_from_plugin_path(candidate_path, require_entrypoint=require_entrypoint)
                if parsed_manifest is None:
                    continue
                yield candidate_path, parsed_manifest

    def build_plugin_dependency_map(
        self,
        plugin_dirs: Iterable[Path],
        require_entrypoint: bool = True,
    ) -> Dict[str, List[str]]:
        """扫描目录并构建 ``plugin_id -> 依赖插件 ID 列表`` 映射。

        Args:
            plugin_dirs: 一个或多个插件根目录。
            require_entrypoint: 是否要求每个插件目录内存在 ``plugin.py``。

        Returns:
            Dict[str, List[str]]: 所有成功解析到的插件依赖映射。
        """
        dependency_map: Dict[str, List[str]] = {}
        for _plugin_path, manifest in self.iter_plugin_manifests(plugin_dirs, require_entrypoint=require_entrypoint):
            dependency_map[manifest.id] = manifest.plugin_dependency_ids
        return dependency_map

    def read_plugin_id_from_plugin_path(self, plugin_path: Path, require_entrypoint: bool = True) -> Optional[str]:
        """从单个插件目录中读取规范化后的插件 ID。

        Args:
            plugin_path: 单个插件目录路径。
            require_entrypoint: 是否要求目录内存在 ``plugin.py``。

        Returns:
            Optional[str]: 解析成功时返回插件 ID，否则返回 ``None``。
        """
        manifest = self.load_from_plugin_path(plugin_path, require_entrypoint=require_entrypoint)
        if manifest is None:
            return None
        return manifest.id

    def get_unsatisfied_plugin_dependencies(
        self,
        manifest: PluginManifest,
        available_plugin_versions: Dict[str, str],
    ) -> List[str]:
        """返回当前 Manifest 尚未满足的插件依赖项。

        Args:
            manifest: 目标插件的强类型 Manifest。
            available_plugin_versions: 当前可用插件版本映射，键为插件 ID，值为插件版本。

        Returns:
            List[str]: 未满足依赖的错误描述列表。
        """
        unsatisfied_dependencies: List[str] = []
        for dependency in manifest.plugin_dependencies:
            dependency_version = available_plugin_versions.get(dependency.id)
            if not dependency_version:
                unsatisfied_dependencies.append(f"{dependency.id} (未找到依赖插件)")
                continue

            if not self._version_matches_specifier(dependency_version, dependency.version_spec):
                unsatisfied_dependencies.append(
                    f"{dependency.id} (需要 {dependency.version_spec}，当前 {dependency_version})"
                )

        return unsatisfied_dependencies

    def is_plugin_dependency_satisfied(
        self,
        dependency: PluginDependencyDefinition,
        plugin_version: str,
    ) -> bool:
        """判断单个插件依赖是否被指定版本满足。

        Args:
            dependency: 插件级依赖声明。
            plugin_version: 当前可用的插件版本号。

        Returns:
            bool: 是否满足版本约束。
        """
        return self._version_matches_specifier(plugin_version, dependency.version_spec)

    def _validate_runtime_compatibility(self, manifest: PluginManifest) -> None:
        """校验运行时版本兼容性与 Python 包依赖。

        Args:
            manifest: 已通过结构校验的强类型 Manifest。
        """
        host_ok, host_message = VersionComparator.is_in_range(
            self._host_version,
            manifest.host_application.min_version,
            manifest.host_application.max_version,
        )
        if not host_ok:
            self.errors.append(f"Host 版本不兼容: {host_message} (当前 Host: {self._host_version})")

        sdk_ok, sdk_message = VersionComparator.is_in_range(
            self._sdk_version,
            manifest.sdk.min_version,
            manifest.sdk.max_version,
        )
        if not sdk_ok:
            self.errors.append(f"SDK 版本不兼容: {sdk_message} (当前 SDK: {self._sdk_version})")

        self._validate_python_package_dependencies(manifest)

    def _validate_python_package_dependencies(self, manifest: PluginManifest) -> None:
        """校验 Python 包依赖与主程序运行环境是否冲突。

        Args:
            manifest: 已通过结构校验的强类型 Manifest。
        """
        host_requirements = self._load_host_dependency_requirements(self._project_root)

        for dependency in manifest.python_package_dependencies:
            normalized_package_name = canonicalize_name(dependency.name)
            package_specifier = self._build_specifier_set(dependency.version_spec)
            if package_specifier is None:
                self.errors.append(
                    f"Python 包依赖 {dependency.name} 的版本约束无效: {dependency.version_spec}"
                )
                continue

            installed_version = self._get_installed_package_version(dependency.name)
            host_requirement = host_requirements.get(normalized_package_name)

            if installed_version is not None and not self._version_matches_specifier(
                installed_version,
                dependency.version_spec,
            ):
                self.errors.append(
                    f"Python 包依赖冲突: {dependency.name} 需要 {dependency.version_spec}，"
                    f"当前运行环境为 {installed_version}"
                )
                continue

            if host_requirement is None:
                continue

            if not self._requirements_may_overlap(host_requirement.specifier, package_specifier):
                host_specifier = str(host_requirement.specifier or "")
                self.errors.append(
                    f"Python 包依赖冲突: {dependency.name} 需要 {dependency.version_spec}，"
                    f"主程序依赖约束为 {host_specifier or '任意版本'}"
                )

    def _log_errors(self) -> None:
        """输出当前累计的 Manifest 校验错误。"""
        for error_message in self.errors:
            logger.error(f"Manifest 校验失败: {error_message}")

    @classmethod
    def _resolve_project_root(cls) -> Path:
        """推断当前项目根目录。

        Returns:
            Path: 项目根目录路径。
        """
        return Path(__file__).resolve().parents[3]

    @classmethod
    @lru_cache(maxsize=None)
    def _detect_default_host_version(cls, project_root: Path) -> str:
        """从主程序 ``pyproject.toml`` 探测 Host 版本号。

        Args:
            project_root: 项目根目录。

        Returns:
            str: 探测到的 Host 版本号；失败时返回空字符串。
        """
        pyproject_path = project_root / "pyproject.toml"
        try:
            with pyproject_path.open("rb") as pyproject_file:
                pyproject_data = tomllib.load(pyproject_file)
        except Exception:
            return ""

        project_data = pyproject_data.get("project", {})
        if not isinstance(project_data, dict):
            return ""

        raw_version = str(project_data.get("version", "") or "").strip()
        if VersionComparator.is_valid_semver(raw_version):
            return raw_version
        return ""

    @classmethod
    @lru_cache(maxsize=None)
    def _detect_default_sdk_version(cls, project_root: Path) -> str:
        """探测当前运行环境中的 SDK 版本号。

        Args:
            project_root: 项目根目录。

        Returns:
            str: 探测到的 SDK 版本号；失败时返回空字符串。
        """
        try:
            raw_version = importlib_metadata.version("maibot-plugin-sdk")
            if VersionComparator.is_valid_semver(raw_version):
                return raw_version
        except importlib_metadata.PackageNotFoundError:
            pass

        sdk_pyproject_path = project_root / "packages" / "maibot-plugin-sdk" / "pyproject.toml"
        try:
            with sdk_pyproject_path.open("rb") as pyproject_file:
                pyproject_data = tomllib.load(pyproject_file)
        except Exception:
            return ""

        project_data = pyproject_data.get("project", {})
        if not isinstance(project_data, dict):
            return ""

        raw_version = str(project_data.get("version", "") or "").strip()
        if VersionComparator.is_valid_semver(raw_version):
            return raw_version
        return ""

    @classmethod
    @lru_cache(maxsize=None)
    def _load_host_dependency_requirements(cls, project_root: Path) -> Dict[str, Requirement]:
        """加载主程序 ``pyproject.toml`` 中声明的依赖约束。

        Args:
            project_root: 项目根目录。

        Returns:
            Dict[str, Requirement]: 以规范化包名为键的 Requirement 映射。
        """
        pyproject_path = project_root / "pyproject.toml"
        try:
            with pyproject_path.open("rb") as pyproject_file:
                pyproject_data = tomllib.load(pyproject_file)
        except Exception:
            return {}

        project_data = pyproject_data.get("project", {})
        if not isinstance(project_data, dict):
            return {}

        raw_dependencies = project_data.get("dependencies", [])
        if not isinstance(raw_dependencies, list):
            return {}

        requirements: Dict[str, Requirement] = {}
        for raw_dependency in raw_dependencies:
            dependency_text = str(raw_dependency or "").strip()
            if not dependency_text:
                continue

            try:
                requirement = Requirement(dependency_text)
            except InvalidRequirement:
                continue

            requirements[canonicalize_name(requirement.name)] = requirement

        return requirements

    @staticmethod
    def _get_installed_package_version(package_name: str) -> Optional[str]:
        """获取当前运行环境中指定 Python 包的安装版本。

        Args:
            package_name: 待查询的包名。

        Returns:
            Optional[str]: 已安装版本号；未安装时返回 ``None``。
        """
        try:
            return importlib_metadata.version(package_name)
        except importlib_metadata.PackageNotFoundError:
            return None

    @staticmethod
    def _build_specifier_set(version_spec: str) -> Optional[SpecifierSet]:
        """构造版本约束对象。

        Args:
            version_spec: 版本约束字符串。

        Returns:
            Optional[SpecifierSet]: 构造成功时返回约束对象，否则返回 ``None``。
        """
        try:
            return SpecifierSet(version_spec)
        except InvalidSpecifier:
            return None

    @staticmethod
    def _version_matches_specifier(version: str, version_spec: str) -> bool:
        """判断版本是否满足给定约束。

        Args:
            version: 待判断的版本号。
            version_spec: 版本约束表达式。

        Returns:
            bool: 是否满足约束。
        """
        try:
            normalized_version = Version(version)
            specifier_set = SpecifierSet(version_spec)
        except (InvalidVersion, InvalidSpecifier):
            return False
        return specifier_set.contains(normalized_version, prereleases=True)

    @classmethod
    def _requirements_may_overlap(cls, left: SpecifierSet, right: SpecifierSet) -> bool:
        """粗略判断两个版本约束是否存在交集。

        Args:
            left: 左侧版本约束。
            right: 右侧版本约束。

        Returns:
            bool: 若可能存在交集则返回 ``True``，否则返回 ``False``。
        """
        candidate_versions = cls._build_candidate_versions(left, right)
        for candidate_version in candidate_versions:
            if left.contains(candidate_version, prereleases=True) and right.contains(candidate_version, prereleases=True):
                return True
        return False

    @classmethod
    def _build_candidate_versions(cls, left: SpecifierSet, right: SpecifierSet) -> List[Version]:
        """为两个版本约束构造一组用于交集探测的候选版本。

        Args:
            left: 左侧版本约束。
            right: 右侧版本约束。

        Returns:
            List[Version]: 去重后的候选版本列表。
        """
        candidate_versions: List[Version] = [Version("0.0.0")]
        for specifier in tuple(left) + tuple(right):
            for candidate_version in cls._expand_candidate_versions(specifier.version):
                if candidate_version not in candidate_versions:
                    candidate_versions.append(candidate_version)
        return candidate_versions

    @staticmethod
    def _expand_candidate_versions(raw_version: str) -> List[Version]:
        """根据边界版本扩展出一组邻近候选版本。

        Args:
            raw_version: 约束中出现的边界版本字符串。

        Returns:
            List[Version]: 可用于交集探测的候选版本列表。
        """
        normalized_text = raw_version.replace("*", "0")
        try:
            boundary_version = Version(normalized_text)
        except InvalidVersion:
            return []

        release_parts = list(boundary_version.release[:3])
        while len(release_parts) < 3:
            release_parts.append(0)
        major, minor, patch = release_parts[:3]

        candidates = {
            Version(f"{major}.{minor}.{patch}"),
            Version(f"{major}.{minor}.{patch + 1}"),
        }
        if patch > 0:
            candidates.add(Version(f"{major}.{minor}.{patch - 1}"))
        elif minor > 0:
            candidates.add(Version(f"{major}.{minor - 1}.999"))
        elif major > 0:
            candidates.add(Version(f"{major - 1}.999.999"))

        return sorted(candidates)

    @classmethod
    def _format_validation_errors(cls, exc: ValidationError) -> List[str]:
        """将 Pydantic 校验错误转换为中文错误列表。

        Args:
            exc: Pydantic 抛出的校验异常。

        Returns:
            List[str]: 中文错误描述列表。
        """
        error_messages: List[str] = []
        for error in exc.errors():
            location = cls._format_error_location(error.get("loc", ()))
            error_type = str(error.get("type", ""))
            error_input = error.get("input")
            error_context = error.get("ctx", {}) or {}

            if error_type == "missing":
                error_messages.append(f"缺少必需字段: {location}")
            elif error_type == "extra_forbidden":
                error_messages.append(f"存在未声明字段: {location}")
            elif error_type == "literal_error":
                expected_values = error_context.get("expected")
                error_messages.append(f"字段 {location} 的值不合法，必须为 {expected_values}")
            elif error_type == "model_type":
                error_messages.append(f"字段 {location} 必须为对象")
            elif error_type.endswith("_type"):
                error_messages.append(f"字段 {location} 的类型不正确")
            elif error_type == "value_error":
                error_messages.append(f"字段 {location} 校验失败: {error_context.get('error')}")
            else:
                error_messages.append(f"字段 {location} 校验失败: {error.get('msg', error_input)}")

        return error_messages

    @staticmethod
    def _format_error_location(location: Tuple[Any, ...]) -> str:
        """格式化校验错误字段路径。

        Args:
            location: Pydantic 提供的字段路径元组。

        Returns:
            str: 点号连接后的字段路径。
        """
        return ".".join(str(item) for item in location) if location else "<root>"
