"""人物迁移维护入口；导入配置或运行机器人前执行。"""

from pathlib import Path
from typing import Any

import argparse
import importlib
import json

from src.services.person_migration_plan import dumps, loads
from src.services.person_migration_service import MigrationExtension, execute, preview, recover


def add_person_migration_command(commands: Any) -> None:
    command = commands.add_parser("person-migrate", help="在线预览、合并人物数据或恢复中断任务")
    command.add_argument("--directory", "-d", type=Path, default=Path.cwd(), help="Bot 实例目录")
    actions = command.add_subparsers(dest="migration_action", required=True)
    plan = actions.add_parser("preview", help="生成含冲突列表的迁移计划")
    plan.add_argument("--platform", required=True)
    plan.add_argument("--source", required=True)
    plan.add_argument("--target", required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--decisions", type=Path, help="冲突 ID 到 source/target 选择的 JSON 文件")
    plan.add_argument("--extension", action="append", default=[], help="迁移扩展工厂 module:function，可重复")
    apply = actions.add_parser("execute", help="执行已确认全部冲突的计划")
    apply.add_argument("--plan", type=Path, required=True)
    restore = actions.add_parser("recover", help="恢复被中断任务")
    restore.add_argument("--job", type=Path, required=True)


def run_person_migration(args: argparse.Namespace) -> int:
    root = args.directory.expanduser().resolve()
    if args.migration_action == "preview":
        extensions = []
        for value in args.extension:
            module, separator, function = value.partition(":")
            if not separator:
                raise ValueError("扩展参数必须为 module:function")
            factory = vars(importlib.import_module(module))[function]
            extension = factory(root)
            if not isinstance(extension, MigrationExtension):
                raise ValueError("扩展工厂必须返回 MigrationExtension")
            extensions.append(extension)
        decisions = loads(args.decisions.read_text(encoding="utf-8")) if args.decisions else {}
        plan = preview(root, args.platform, args.source, args.target, decisions=decisions, extensions=extensions)
        args.output.write_text(dumps(plan), encoding="utf-8")
        review_path = args.output.with_suffix(".review.json")
        review_path.write_text(json.dumps({"identity": plan["identity"], "counts": {
            item["name"]: item["counts"] for item in plan["databases"]
        }, "conflicts": plan["conflicts"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"计划已保存: {args.output.resolve()}")
        print(f"影响范围和冲突: {review_path.resolve()}")
        for database in plan["databases"]:
            print(f"{database['name']}: {database['counts']}")
        print(f"冲突: {len(plan['conflicts'])}；全部确认: {plan['ready']}")
        return 0
    if args.migration_action == "execute":
        job = execute(loads(args.plan.read_text(encoding="utf-8")), expected_root=root)
        print(f"人物合并完成，执行记录: {job}")
        return 0
    recover(args.job, expected_root=root)
    print("人物迁移已恢复，可重新预览")
    return 0
