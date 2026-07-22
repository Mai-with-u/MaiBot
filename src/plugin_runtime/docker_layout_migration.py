"""迁移 Docker 旧布局中误放进插件源码目录的插件持久数据。"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import argparse
import json
import shutil


MIGRATION_VERSION = 1
DEFAULT_PLUGIN_SOURCE_ROOT = Path("/MaiMBot/plugins")
DEFAULT_PLUGIN_DATA_ROOT = Path("/MaiMBot/data/plugins")
DEFAULT_REPORT_PATH = Path("/MaiMBot/data/plugin_layout_migration_v1.json")
SOURCE_MARKERS = ("plugin.py", "_manifest.json")


@dataclass
class MigrationReport:
    """记录一次插件目录布局迁移的结果。"""

    version: int = MIGRATION_VERSION
    completed: bool = True
    moved: List[str] = field(default_factory=list)
    removed_empty: List[str] = field(default_factory=list)
    skipped_ambiguous: List[Dict[str, str]] = field(default_factory=list)
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    invalid_sources: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MigrationReport":
        """从已有报告恢复迁移结果，并严格校验迁移标记。"""

        if data.get("version") != MIGRATION_VERSION or data.get("completed") is not True:
            raise ValueError("插件目录迁移报告版本无效或迁移未完成")
        return cls(
            version=data["version"],
            completed=data["completed"],
            moved=list(data.get("moved", [])),
            removed_empty=list(data.get("removed_empty", [])),
            skipped_ambiguous=list(data.get("skipped_ambiguous", [])),
            conflicts=list(data.get("conflicts", [])),
            invalid_sources=list(data.get("invalid_sources", [])),
        )

    @property
    def requires_manual_review(self) -> bool:
        """返回迁移报告中是否存在需要人工确认的目录。"""

        return bool(self.skipped_ambiguous or self.conflicts or self.invalid_sources)


def _is_safe_plugin_id(plugin_id: str) -> bool:
    """判断插件 ID 是否可安全作为单层目录名使用。"""

    return bool(plugin_id) and plugin_id not in {".", ".."} and "/" not in plugin_id and "\\" not in plugin_id


def _has_source_marker(path: Path) -> bool:
    """判断目录中是否存在任一插件源码标志。"""

    return (path / ".git").exists() or any((path / marker).exists() for marker in SOURCE_MARKERS)


def _read_plugin_id(plugin_path: Path) -> str:
    """读取一个完整插件源码目录声明的插件 ID。"""

    manifest_path = plugin_path / "_manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as file_obj:
        manifest = json.load(file_obj)

    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not _is_safe_plugin_id(plugin_id.strip()):
        raise ValueError("_manifest.json 缺少可安全用作目录名的插件 ID")
    return plugin_id.strip()


def _discover_plugin_sources(source_root: Path, report: MigrationReport) -> Dict[str, List[Path]]:
    """扫描完整插件源码目录，并按 manifest 中的插件 ID 分组。"""

    plugin_sources: Dict[str, List[Path]] = {}
    for plugin_path in sorted(source_root.iterdir(), key=lambda path: path.name.casefold()):
        if plugin_path.is_symlink() or not plugin_path.is_dir():
            continue
        if not all((plugin_path / marker).is_file() for marker in SOURCE_MARKERS):
            continue

        try:
            plugin_id = _read_plugin_id(plugin_path)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            report.invalid_sources.append({"path": str(plugin_path), "reason": str(exc)})
            continue
        plugin_sources.setdefault(plugin_id, []).append(plugin_path)
    return plugin_sources


def _write_report(report_path: Path, report: MigrationReport) -> None:
    """原子写入迁移报告，避免中断时留下伪造的完成标记。"""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_name(f".{report_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(report_path)


def _load_existing_report(report_path: Path) -> Optional[MigrationReport]:
    """读取已有的一次性迁移报告。"""

    if not report_path.exists():
        return None
    with open(report_path, "r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)
    if not isinstance(data, dict):
        raise ValueError("插件目录迁移报告必须是 JSON 对象")
    return MigrationReport.from_dict(data)


def migrate_plugin_layout(
    source_root: Path,
    data_root: Path,
    report_path: Path,
) -> Optional[MigrationReport]:
    """尽力迁移能够明确识别的旧插件数据目录。

    返回 ``None`` 表示源码目录和数据目录仍指向同一位置，当前 Docker
    挂载尚未完成隔离，因此不会执行任何文件操作，也不会写入完成标记。
    """

    source_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    source_root = source_root.resolve()
    data_root = data_root.resolve()

    if source_root.samefile(data_root):
        return None

    existing_report = _load_existing_report(report_path)
    if existing_report is not None:
        return existing_report

    report = MigrationReport()
    plugin_sources = _discover_plugin_sources(source_root, report)

    for plugin_id in sorted(plugin_sources, key=str.casefold):
        source_paths = plugin_sources[plugin_id]
        if len(source_paths) != 1:
            report.conflicts.append(
                {
                    "plugin_id": plugin_id,
                    "reason": "多个源码目录声明了同一个插件 ID",
                    "paths": ", ".join(str(path) for path in source_paths),
                }
            )
            continue

        plugin_source_path = source_paths[0]
        old_data_path = source_root / plugin_id
        if not old_data_path.exists() and not old_data_path.is_symlink():
            continue
        if old_data_path == plugin_source_path:
            report.skipped_ambiguous.append(
                {
                    "plugin_id": plugin_id,
                    "path": str(old_data_path),
                    "reason": "源码目录名与插件 ID 相同，无法自动区分历史运行数据",
                }
            )
            continue
        if old_data_path.is_symlink():
            report.skipped_ambiguous.append(
                {
                    "plugin_id": plugin_id,
                    "path": str(old_data_path),
                    "reason": "旧目录是符号链接",
                }
            )
            continue
        if not old_data_path.is_dir():
            report.conflicts.append(
                {
                    "plugin_id": plugin_id,
                    "path": str(old_data_path),
                    "reason": "旧数据路径不是目录",
                }
            )
            continue
        if _has_source_marker(old_data_path):
            report.skipped_ambiguous.append(
                {
                    "plugin_id": plugin_id,
                    "path": str(old_data_path),
                    "reason": "旧目录包含插件源码标志",
                }
            )
            continue

        new_data_path = data_root / plugin_id
        if new_data_path.exists() or new_data_path.is_symlink():
            if new_data_path.is_symlink() or not new_data_path.is_dir():
                report.conflicts.append(
                    {
                        "plugin_id": plugin_id,
                        "path": str(new_data_path),
                        "reason": "新数据路径不是普通目录",
                    }
                )
                continue
            old_data_is_empty = not any(old_data_path.iterdir())
            new_data_is_empty = not any(new_data_path.iterdir())
            if old_data_is_empty:
                old_data_path.rmdir()
                report.removed_empty.append(plugin_id)
                continue
            if new_data_is_empty:
                new_data_path.rmdir()
                shutil.move(str(old_data_path), str(new_data_path))
                report.moved.append(plugin_id)
                continue
            report.conflicts.append(
                {
                    "plugin_id": plugin_id,
                    "path": str(old_data_path),
                    "reason": "新旧数据目录都非空，拒绝自动合并",
                }
            )
            continue

        shutil.move(str(old_data_path), str(new_data_path))
        report.moved.append(plugin_id)

    _write_report(report_path, report)
    return report


def _parse_args() -> argparse.Namespace:
    """解析 Docker 入口脚本传入的迁移路径。"""

    parser = argparse.ArgumentParser(description="迁移 Docker 旧插件数据目录")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_PLUGIN_SOURCE_ROOT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_PLUGIN_DATA_ROOT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    return parser.parse_args()


def main() -> None:
    """执行一次 Docker 插件目录布局迁移。"""

    args = _parse_args()
    report_already_exists = args.report_path.exists()
    report = migrate_plugin_layout(args.source_root, args.data_root, args.report_path)
    if report is None:
        print(
            "[插件目录迁移] 插件源码目录与数据目录仍指向同一位置，已跳过迁移；"
            "请更新 docker-compose.yml，增加独立的 /MaiMBot/data/plugins 挂载。"
        )
        return
    if report_already_exists:
        print(f"[插件目录迁移] 已完成过迁移，报告位于 {args.report_path}")
        return

    print(
        "[插件目录迁移] 自动迁移完成："
        f"移动 {len(report.moved)} 个，清理空目录 {len(report.removed_empty)} 个，"
        f"跳过歧义目录 {len(report.skipped_ambiguous)} 个，冲突 {len(report.conflicts)} 个。"
    )
    if report.requires_manual_review:
        print(f"[插件目录迁移] 存在需要人工确认的项目，详情见 {args.report_path}")


if __name__ == "__main__":
    main()
