"""Manifest 校验与版本兼容性

从旧系统的 ManifestValidator / VersionComparator 对齐移植，
适配新 plugin_runtime 的 _manifest.json 格式。
"""

from typing import Any

import re

from src.common.logger import get_logger

logger = get_logger("plugin_runtime.runner.manifest_validator")


class VersionComparator:
    """语义化版本号比较器"""

    @staticmethod
    def normalize_version(version: str) -> str:
        if not version:
            return "0.0.0"
        normalized = re.sub(r"-snapshot\.\d+", "", version.strip())
        if not re.match(r"^\d+(\.\d+){0,2}$", normalized):
            return "0.0.0"
        parts = normalized.split(".")
        while len(parts) < 3:
            parts.append("0")
        return ".".join(parts[:3])

    @staticmethod
    def parse_version(version: str) -> tuple[int, int, int]:
        normalized = VersionComparator.normalize_version(version)
        try:
            parts = normalized.split(".")
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return (0, 0, 0)

    @staticmethod
    def compare(v1: str, v2: str) -> int:
        t1 = VersionComparator.parse_version(v1)
        t2 = VersionComparator.parse_version(v2)
        if t1 < t2:
            return -1
        elif t1 > t2:
            return 1
        return 0

    @staticmethod
    def is_in_range(version: str, min_version: str = "", max_version: str = "") -> tuple[bool, str]:
        if not min_version and not max_version:
            return True, ""
        vn = VersionComparator.normalize_version(version)
        if min_version:
            mn = VersionComparator.normalize_version(min_version)
            if VersionComparator.compare(vn, mn) < 0:
                return False, f"版本 {vn} 低于最小要求 {mn}"
        if max_version:
            mx = VersionComparator.normalize_version(max_version)
            if VersionComparator.compare(vn, mx) > 0:
                return False, f"版本 {vn} 高于最大支持 {mx}"
        return True, ""


class ManifestValidator:
    """_manifest.json 校验器"""

    REQUIRED_FIELDS = ["name", "version", "description", "author"]
    RECOMMENDED_FIELDS = ["license", "keywords", "categories"]
    SUPPORTED_MANIFEST_VERSIONS = [1, 2]

    def __init__(self, host_version: str = ""):
        self._host_version = host_version
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def validate(self, manifest: dict[str, Any]) -> bool:
        """校验 manifest 数据，返回是否通过（errors 为空即通过）。"""
        self.errors.clear()
        self.warnings.clear()

        self._check_required_fields(manifest)
        self._check_manifest_version(manifest)
        self._check_author(manifest)
        self._check_host_compatibility(manifest)
        self._check_recommended(manifest)

        if self.errors:
            for e in self.errors:
                logger.error(f"Manifest 校验失败: {e}")
        if self.warnings:
            for w in self.warnings:
                logger.warning(f"Manifest 警告: {w}")

        return len(self.errors) == 0

    def _check_required_fields(self, manifest: dict[str, Any]) -> None:
        for field in self.REQUIRED_FIELDS:
            if field not in manifest:
                self.errors.append(f"缺少必需字段: {field}")
            elif not manifest[field]:
                self.errors.append(f"必需字段不能为空: {field}")

    def _check_manifest_version(self, manifest: dict[str, Any]) -> None:
        mv = manifest.get("manifest_version")
        if mv is not None and mv not in self.SUPPORTED_MANIFEST_VERSIONS:
            self.errors.append(
                f"不支持的 manifest_version: {mv}，支持: {self.SUPPORTED_MANIFEST_VERSIONS}"
            )

    def _check_author(self, manifest: dict[str, Any]) -> None:
        author = manifest.get("author")
        if author is None:
            return
        if isinstance(author, dict):
            if "name" not in author or not author["name"]:
                self.errors.append("author 对象缺少 name 字段")
        elif isinstance(author, str):
            if not author.strip():
                self.errors.append("author 不能为空")
        else:
            self.errors.append("author 应为字符串或 {name, url} 对象")

    def _check_host_compatibility(self, manifest: dict[str, Any]) -> None:
        host_app = manifest.get("host_application")
        if not isinstance(host_app, dict) or not self._host_version:
            return
        min_v = host_app.get("min_version", "")
        max_v = host_app.get("max_version", "")
        ok, msg = VersionComparator.is_in_range(self._host_version, min_v, max_v)
        if not ok:
            self.errors.append(f"Host 版本不兼容: {msg} (当前 Host: {self._host_version})")

    def _check_recommended(self, manifest: dict[str, Any]) -> None:
        for field in self.RECOMMENDED_FIELDS:
            if field not in manifest or not manifest[field]:
                self.warnings.append(f"建议填写字段: {field}")
