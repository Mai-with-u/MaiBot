"""
插件Manifest工具模块

提供manifest文件的验证、生成和管理功能
"""

import re
from typing import Dict, Any, Tuple
from src.common.logger import get_logger
from src.config.config import MMC_VERSION

# if TYPE_CHECKING:
#     from src.plugin_system.base.base_plugin import BasePlugin

logger = get_logger("manifest_utils")


class VersionComparator:
    """版本号比较器

    支持语义化版本号比较，自动处理snapshot版本，并支持向前兼容性检查
    """

    # 版本兼容性映射表（硬编码）
    # 格式: {插件最大支持版本: [实际兼容的版本列表]}
    COMPATIBILITY_MAP = {
        # 0.8.x 系列向前兼容规则
        "0.8.0": ["0.8.1", "0.8.2", "0.8.3", "0.8.4", "0.8.5", "0.8.6", "0.8.7", "0.8.8", "0.8.9", "0.8.10"],
        "0.8.1": ["0.8.2", "0.8.3", "0.8.4", "0.8.5", "0.8.6", "0.8.7", "0.8.8", "0.8.9", "0.8.10"],
        "0.8.2": ["0.8.3", "0.8.4", "0.8.5", "0.8.6", "0.8.7", "0.8.8", "0.8.9", "0.8.10"],
        "0.8.3": ["0.8.4", "0.8.5", "0.8.6", "0.8.7", "0.8.8", "0.8.9", "0.8.10"],
        "0.8.4": ["0.8.5", "0.8.6", "0.8.7", "0.8.8", "0.8.9", "0.8.10"],
        "0.8.5": ["0.8.6", "0.8.7", "0.8.8", "0.8.9", "0.8.10"],
        "0.8.6": ["0.8.7", "0.8.8", "0.8.9", "0.8.10"],
        "0.8.7": ["0.8.8", "0.8.9", "0.8.10"],
        "0.8.8": ["0.8.9", "0.8.10"],
        "0.8.9": ["0.8.10"],
        # 可以根据需要添加更多兼容映射
        # "0.9.0": ["0.9.1", "0.9.2", "0.9.3"],  # 示例：0.9.x系列兼容
    }

    @staticmethod
    def normalize_version(version: str) -> str:
        """标准化版本号，移除snapshot标识

        Args:
            version: 原始版本号，如 "0.8.0-snapshot.1"

        Returns:
            str: 标准化后的版本号，如 "0.8.0"
        """
        if not version:
            return "0.0.0"

        # 移除snapshot部分
        normalized = re.sub(r"-.*$", "", version.strip())

        # 确保版本号格式正确
        if not re.match(r"^\d+(\.\d+){0,2}$", normalized):
            # 如果不是有效的版本号格式，返回默认版本
            return "0.0.0"

        # 尝试补全版本号
        parts = normalized.split(".")
        while len(parts) < 3:
            parts.append("0")
        normalized = ".".join(parts[:3])

        return normalized

    @staticmethod
    def parse_version(version: str) -> Tuple[int, int, int]:
        """解析版本号为元组

        Args:
            version: 版本号字符串

        Returns:
            Tuple[int, int, int]: (major, minor, patch)
        """
        normalized = VersionComparator.normalize_version(version)
        try:
            parts = normalized.split(".")
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            logger.warning(f"无法解析版本号: {version}，使用默认版本 0.0.0")
            return (0, 0, 0)

    @staticmethod
    def compare_versions(version1: str, version2: str) -> int:
        """比较两个版本号

        Args:
            version1: 第一个版本号
            version2: 第二个版本号

        Returns:
            int: -1 if version1 < version2, 0 if equal, 1 if version1 > version2
        """
        v1_tuple = VersionComparator.parse_version(version1)
        v2_tuple = VersionComparator.parse_version(version2)

        if v1_tuple < v2_tuple:
            return -1
        elif v1_tuple > v2_tuple:
            return 1
        else:
            return 0

    @staticmethod
    def check_forward_compatibility(current_version: str, max_version: str) -> Tuple[bool, str]:
        """检查向前兼容性（仅使用兼容性映射表）

        Args:
            current_version: 当前版本
            max_version: 插件声明的最大支持版本

        Returns:
            Tuple[bool, str]: (是否兼容, 兼容信息)
        """
        current_normalized = VersionComparator.normalize_version(current_version)
        max_normalized = VersionComparator.normalize_version(max_version)

        # 检查兼容性映射表
        if max_normalized in VersionComparator.COMPATIBILITY_MAP:
            compatible_versions = VersionComparator.COMPATIBILITY_MAP[max_normalized]
            if current_normalized in compatible_versions:
                return True, f"根据兼容性映射表，版本 {current_normalized} 与 {max_normalized} 兼容"

        return False, ""

    @staticmethod
    def is_version_in_range(version: str, min_version: str = "", max_version: str = "") -> Tuple[bool, str]:
        """检查版本是否在指定范围内，支持兼容性检查

        Args:
            version: 要检查的版本号
            min_version: 最小版本号（可选）
            max_version: 最大版本号（可选）

        Returns:
            Tuple[bool, str]: (是否兼容, 错误信息或兼容信息)
        """
        if not min_version and not max_version:
            return True, ""

        version_normalized = VersionComparator.normalize_version(version)

        # 检查最小版本
        if min_version:
            min_normalized = VersionComparator.normalize_version(min_version)
            if VersionComparator.compare_versions(version_normalized, min_normalized) < 0:
                return False, f"版本 {version_normalized} 低于最小要求版本 {min_normalized}"

        # 检查最大版本
        if max_version:
            max_normalized = VersionComparator.normalize_version(max_version)
            comparison = VersionComparator.compare_versions(version_normalized, max_normalized)

            if comparison > 0:
                # 严格版本检查失败，尝试兼容性检查
                is_compatible, compat_msg = VersionComparator.check_forward_compatibility(
                    version_normalized, max_normalized
                )

                if not is_compatible:
                    return False, f"版本 {version_normalized} 高于最大支持版本 {max_normalized}，且无兼容性映射"

                logger.info(f"版本兼容性检查：{compat_msg}")
                return True, compat_msg
        return True, ""

    @staticmethod
    def get_current_host_version() -> str:
        """获取当前主机应用版本

        Returns:
            str: 当前版本号
        """
        return VersionComparator.normalize_version(MMC_VERSION)

    @staticmethod
    def add_compatibility_mapping(base_version: str, compatible_versions: list) -> None:
        """动态添加兼容性映射

        Args:
            base_version: 基础版本（插件声明的最大支持版本）
            compatible_versions: 兼容的版本列表
        """
        base_normalized = VersionComparator.normalize_version(base_version)
        VersionComparator.COMPATIBILITY_MAP[base_normalized] = [
            VersionComparator.normalize_version(v) for v in compatible_versions
        ]
        logger.info(f"添加兼容性映射：{base_normalized} -> {compatible_versions}")

    @staticmethod
    def get_compatibility_info() -> Dict[str, list]:
        """获取当前的兼容性映射表

        Returns:
            Dict[str, list]: 兼容性映射表的副本
        """
        return VersionComparator.COMPATIBILITY_MAP.copy()


class ManifestValidator:
    """Manifest文件验证器"""

    # 必需字段（必须存在且不能为空）
    REQUIRED_FIELDS = ["manifest_version", "name", "version", "description", "author"]

    # 可选字段（可以不存在或为空）
    OPTIONAL_FIELDS = [
        "license",
        "host_application",
        "homepage_url",
        "repository_url",
        "keywords",
        "categories",
        "default_locale",
        "locales_path",
        "plugin_info",
    ]

    # 建议填写的字段（会给出警告但不会导致验证失败）
    RECOMMENDED_FIELDS = ["license", "keywords", "categories"]

    SUPPORTED_MANIFEST_VERSIONS = [1]

    def __init__(self):
        self.validation_errors = []
        self.validation_warnings = []

    def validate_manifest(self, manifest_data: Dict[str, Any]) -> bool:
        """验证manifest数据

        Args:
            manifest_data: manifest数据字典

        Returns:
            bool: 是否验证通过（只有错误会导致验证失败，警告不会）
        """
        self.validation_errors.clear()
        self.validation_warnings.clear()

        # 检查必需字段
        for field in self.REQUIRED_FIELDS:
            if field not in manifest_data:
                self.validation_errors.append(f"缺少必需字段: {field}")
            elif not manifest_data[field]:
                self.validation_errors.append(f"必需字段不能为空: {field}")

        # 检查manifest版本
        if "manifest_version" in manifest_data:
            version = manifest_data["manifest_version"]
            if version not in self.SUPPORTED_MANIFEST_VERSIONS:
                self.validation_errors.append(
                    f"不支持的manifest版本: {version}，支持的版本: {self.SUPPORTED_MANIFEST_VERSIONS}"
                )

        # 检查作者信息格式
        if "author" in manifest_data:
            author = manifest_data["author"]
            if isinstance(author, dict):
                if "name" not in author or not author["name"]:
                    self.validation_errors.append("作者信息缺少name字段或为空")
                # url字段是可选的
                if "url" in author and author["url"]:
                    url = author["url"]
                    if not (url.startswith("http://") or url.startswith("https://")):
                        self.validation_warnings.append("作者URL建议使用完整的URL格式")
            elif isinstance(author, str):
                if not author.strip():
                    self.validation_errors.append("作者信息不能为空")
            else:
                self.validation_errors.append("作者信息格式错误，应为字符串或包含name字段的对象")
        # 检查主机应用版本要求（可选）
        if "host_application" in manifest_data:
            host_app = manifest_data["host_application"]
            if isinstance(host_app, dict):
                min_version = host_app.get("min_version", "")
                max_version = host_app.get("max_version", "")

                # 验证版本字段格式
                for version_field in ["min_version", "max_version"]:
                    if version_field in host_app and not host_app[version_field]:
                        self.validation_warnings.append(f"host_application.{version_field}为空")

                # 检查当前主机版本兼容性
                if min_version or max_version:
                    current_version = VersionComparator.get_current_host_version()
                    is_compatible, error_msg = VersionComparator.is_version_in_range(
                        current_version, min_version, max_version
                    )

                    if not is_compatible:
                        self.validation_errors.append(f"版本兼容性检查失败: {error_msg} (当前版本: {current_version})")
                    else:
                        logger.debug(
                            f"版本兼容性检查通过: 当前版本 {current_version} 符合要求 [{min_version}, {max_version}]"
                        )
            else:
                self.validation_errors.append("host_application格式错误，应为对象")

        # 检查URL格式（可选字段）
        for url_field in ["homepage_url", "repository_url"]:
            if url_field in manifest_data and manifest_data[url_field]:
                url: str = manifest_data[url_field]
                if not (url.startswith("http://") or url.startswith("https://")):
                    self.validation_warnings.append(f"{url_field}建议使用完整的URL格式")

        # 检查数组字段格式（可选字段）
        for list_field in ["keywords", "categories"]:
            if list_field in manifest_data:
                field_value = manifest_data[list_field]
                if field_value is not None and not isinstance(field_value, list):
                    self.validation_errors.append(f"{list_field}应为数组格式")
                elif isinstance(field_value, list):
                    # 检查数组元素是否为字符串
                    for i, item in enumerate(field_value):
                        if not isinstance(item, str):
                            self.validation_warnings.append(f"{list_field}[{i}]应为字符串")

        # 检查建议字段（给出警告）
        for field in self.RECOMMENDED_FIELDS:
            if field not in manifest_data or not manifest_data[field]:
                self.validation_warnings.append(f"建议填写字段: {field}")

        # 检查plugin_info结构（可选）
        if "plugin_info" in manifest_data:
            plugin_info = manifest_data["plugin_info"]
            if isinstance(plugin_info, dict):
                # 检查components数组
                if "components" in plugin_info:
                    components = plugin_info["components"]
                    if not isinstance(components, list):
                        self.validation_errors.append("plugin_info.components应为数组格式")
                    else:
                        for i, component in enumerate(components):
                            if not isinstance(component, dict):
                                self.validation_errors.append(f"plugin_info.components[{i}]应为对象")
                            else:
                                # 检查组件必需字段
                                for comp_field in ["type", "name", "description"]:
                                    if comp_field not in component or not component[comp_field]:
                                        self.validation_errors.append(
                                            f"plugin_info.components[{i}]缺少必需字段: {comp_field}"
                                        )
            else:
                self.validation_errors.append("plugin_info应为对象格式")

        return len(self.validation_errors) == 0

    def get_validation_report(self) -> str:
        """获取验证报告"""
        report = []

        if self.validation_errors:
            report.append("❌ 验证错误:")
            report.extend(f"  - {error}" for error in self.validation_errors)
        if self.validation_warnings:
            report.append("⚠️ 验证警告:")
            report.extend(f"  - {warning}" for warning in self.validation_warnings)
        if not self.validation_errors and not self.validation_warnings:
            report.append("✅ Manifest文件验证通过")

        return "\n".join(report)


# class ManifestGenerator:
#     """Manifest文件生成器"""

#     def __init__(self):
#         self.template = {
#             "manifest_version": 1,
#             "name": "",
#             "version": "1.0.0",
#             "description": "",
#             "author": {"name": "", "url": ""},
#             "license": "MIT",
#             "host_application": {"min_version": "1.0.0", "max_version": "4.0.0"},
#             "homepage_url": "",
#             "repository_url": "",
#             "keywords": [],
#             "categories": [],
#             "default_locale": "zh-CN",
#             "locales_path": "_locales",
#         }

#     def generate_from_plugin(self, plugin_instance: BasePlugin) -> Dict[str, Any]:
#         """从插件实例生成manifest

#         Args:
#             plugin_instance: BasePlugin实例

#         Returns:
#             Dict[str, Any]: 生成的manifest数据
#         """
#         manifest = self.template.copy()

#         # 基本信息
#         manifest["name"] = plugin_instance.plugin_name
#         manifest["version"] = plugin_instance.plugin_version
#         manifest["description"] = plugin_instance.plugin_description

#         # 作者信息
#         if plugin_instance.plugin_author:
#             manifest["author"]["name"] = plugin_instance.plugin_author

#         # 组件信息
#         components = []
#         plugin_components = plugin_instance.get_plugin_components()

#         for component_info, component_class in plugin_components:
#             component_data: Dict[str, Any] = {
#                 "type": component_info.component_type.value,
#                 "name": component_info.name,
#                 "description": component_info.description,
#             }

#             # 添加激活模式信息（对于Action组件）
#             if hasattr(component_class, "focus_activation_type"):
#                 activation_modes = []
#                 if hasattr(component_class, "focus_activation_type"):
#                     activation_modes.append(component_class.focus_activation_type.value)
#                 if hasattr(component_class, "normal_activation_type"):
#                     activation_modes.append(component_class.normal_activation_type.value)
#                 component_data["activation_modes"] = list(set(activation_modes))

#             # 添加关键词信息
#             if hasattr(component_class, "activation_keywords"):
#                 keywords = getattr(component_class, "activation_keywords", [])
#                 if keywords:
#                     component_data["keywords"] = keywords

#             components.append(component_data)

#         manifest["plugin_info"] = {"is_built_in": True, "plugin_type": "general", "components": components}

#         return manifest

#     def save_manifest(self, manifest_data: Dict[str, Any], plugin_dir: str) -> bool:
#         """保存manifest文件

#         Args:
#             manifest_data: manifest数据
#             plugin_dir: 插件目录

#         Returns:
#             bool: 是否保存成功
#         """
#         try:
#             manifest_path = os.path.join(plugin_dir, "_manifest.json")
#             with open(manifest_path, "w", encoding="utf-8") as f:
#                 json.dump(manifest_data, f, ensure_ascii=False, indent=2)
#             logger.info(f"Manifest文件已保存: {manifest_path}")
#             return True
#         except Exception as e:
#             logger.error(f"保存manifest文件失败: {e}")
#             return False


# def validate_plugin_manifest(plugin_dir: str) -> bool:
#     """验证插件目录中的manifest文件

#     Args:
#         plugin_dir: 插件目录路径

#     Returns:
#         bool: 是否验证通过
#     """
#     manifest_path = os.path.join(plugin_dir, "_manifest.json")

#     if not os.path.exists(manifest_path):
#         logger.warning(f"未找到manifest文件: {manifest_path}")
#         return False

#     try:
#         with open(manifest_path, "r", encoding="utf-8") as f:
#             manifest_data = json.load(f)

#         validator = ManifestValidator()
#         is_valid = validator.validate_manifest(manifest_data)

#         logger.info(f"Manifest验证结果:\n{validator.get_validation_report()}")

#         return is_valid

#     except Exception as e:
#         logger.error(f"读取或验证manifest文件失败: {e}")
#         return False


# def generate_plugin_manifest(plugin_instance: BasePlugin, save_to_file: bool = True) -> Optional[Dict[str, Any]]:
#     """为插件生成manifest文件

#     Args:
#         plugin_instance: BasePlugin实例
#         save_to_file: 是否保存到文件

#     Returns:
#         Optional[Dict[str, Any]]: 生成的manifest数据
#     """
#     try:
#         generator = ManifestGenerator()
#         manifest_data = generator.generate_from_plugin(plugin_instance)

#         if save_to_file and plugin_instance.plugin_dir:
#             generator.save_manifest(manifest_data, plugin_instance.plugin_dir)

#         return manifest_data

#     except Exception as e:
#         logger.error(f"生成manifest文件失败: {e}")
#         return None
