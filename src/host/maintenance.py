"""宿主维护：修改所有 Bot 共用的 Python 环境前先停止全部 Bot，完成后按期望状态恢复。"""

from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import asyncio
import json
import sys

from src.common.paths import PACKAGED_INSTALL, RESOURCE_ROOT, SOURCE_ROOT
from src.config.shared_models import CATALOG_FILENAME
from src.plugin_runtime.dependency_pipeline import CombinedPackageRequirement, PluginDependencyPipeline

from .layout import run_in_bot
from .layout import create_bot as create_workspace_bot
from .registry import BotRecord
from .supervisor import BotSupervisor
from .update_locks import update_environment


_ANALYSIS_TIMEOUT = 120.0


def _analyze_bot(directory: Path, shared_catalog: Optional[Path] = None) -> List[Dict[str, Any]]:
    """在 Bot 自己的实例目录中读取完整插件依赖声明；只读，Bot 运行时也可以执行。"""
    output = directory / "temp" / f"plugin-requirements-{uuid4().hex}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_in_bot(
            directory, ["src.plugin_runtime.requirements_report", "--output", str(output)],
            timeout=_ANALYSIS_TIMEOUT, purpose="插件依赖分析", shared_catalog=shared_catalog,
        )
        return json.loads(output.read_text(encoding="utf-8"))
    finally:
        output.unlink(missing_ok=True)


class HostMaintenance:
    def __init__(self, supervisor: BotSupervisor) -> None:
        self.supervisor = supervisor
        self._lock = asyncio.Lock()
        self.update_requested = False
        self.dependency_error = ""
        self._failed_dependency_snapshot: Optional[str] = None
        self._dependency_task: Optional[asyncio.Task[None]] = None
        supervisor.prepare_dependencies = self.prepare_plugin_dependencies

    @property
    def update_root(self) -> Path:
        return self.supervisor.workspace.root if PACKAGED_INSTALL else SOURCE_ROOT

    async def check_update(self) -> Dict[str, Any]:
        from src.common import self_update

        check = await asyncio.to_thread(self_update.check_for_update, self.update_root)
        return {**asdict(check), "can_apply": check.can_apply,
                "blocker_messages": [self_update.BLOCKER_MESSAGES.get(code, code) for code in check.blockers]}

    async def manage_bot(self, bot_id: str, action: str, name: str = "") -> Dict[str, Any]:
        if self.busy:
            raise RuntimeError("宿主正在执行其他维护操作")
        async with self._lock:
            return await self.supervisor.manage(bot_id, action, name)

    async def create_bot(self, name: str) -> BotRecord:
        if self.busy:
            raise RuntimeError("宿主正在执行其他维护操作")
        async with self._lock:
            return await asyncio.to_thread(create_workspace_bot, self.supervisor.workspace, name)

    async def transfer_data(self, bot_id: str, operation: Any, *args: Any) -> None:
        if self.busy:
            raise RuntimeError("宿主正在执行其他维护操作")
        try:
            async with self._lock:
                await self.supervisor.run_file_operation(bot_id, operation, *args)
        finally:
            bot = await asyncio.to_thread(self.supervisor.workspace.registry.get_bot, bot_id)
            journal = bot.directory / "temp" / "data-import" / "transaction.json"
            if bot.desired_state == "running" and not await asyncio.to_thread(journal.exists) and not self.supervisor._start_blocker():
                await self.supervisor.start(bot_id)

    async def prepare_update(self, target: str) -> Dict[str, Any]:
        from src.common import self_update
        from src.common.package_update import ensure_other_instances_stopped

        if self.busy:
            raise RuntimeError("宿主正在执行其他维护操作")
        async with self._lock:
            environment = update_environment()
            await asyncio.to_thread(ensure_other_instances_stopped, environment, self.supervisor.workspace.root)
            # 登记阶段先完成来源、快进和本地改动检查，不通过时不打断运行中的 Bot。
            await asyncio.to_thread(self_update.request_update, target, self.update_root)
            try:
                await self.supervisor.enter_maintenance()
            except BaseException:
                await asyncio.to_thread(self_update.clear_pending_update, self.update_root)
                raise
            self.update_requested = True
            return {"status": "restarting", "target": target}

    @property
    def busy(self) -> bool:
        return self._lock.locked() or self.update_requested

    async def analyze_plugin_dependencies(self) -> Dict[str, Any]:
        """汇总各 Bot 完整声明，分别显示缺失依赖和与当前环境不兼容的声明。"""
        bots = [bot for bot in await asyncio.to_thread(self.supervisor.workspace.registry.list_bots)
                if bot.initialization == "initialized"]
        analyze = partial(_analyze_bot, shared_catalog=self.supervisor.workspace.host_directory / CATALOG_FILENAME)
        reports = await asyncio.gather(*(asyncio.to_thread(analyze, bot.directory) for bot in bots))
        requirements: Dict[str, Dict[str, Any]] = {}
        conflicts: List[str] = []
        per_bot = []
        for bot, items in zip(bots, reports, strict=True):
            per_bot.append({"bot_id": bot.bot_id, "name": bot.name, "requirements": [item["requirement"] for item in items]})
            for item in items:
                origins = [f"{bot.name} ({bot.bot_id}) / {plugin}" for plugin in item["plugin_ids"]]
                merged = requirements.setdefault(item["requirement"], {**item, "plugin_ids": []})
                merged["plugin_ids"] = sorted({*merged["plugin_ids"], *origins})
                if item.get("installed_version") is not None and not item["satisfied"]:
                    conflicts.append(f"{', '.join(origins)} 要求 {item['requirement']}，当前安装 {item['installed_version']}")
        complete = list(requirements.values())
        return {"bots": per_bot, "requirements": [item for item in complete if item.get("installed_version") is None],
                "all_requirements": complete, "conflicts": conflicts}

    async def prepare_plugin_dependencies(self) -> Dict[str, Any]:
        return await self.install_plugin_dependencies(automatic=True)

    def queue_plugin_dependencies(self) -> None:
        """控制通道回执之后执行；维护不能依赖即将停止的 Worker 等待结果。"""
        if self._dependency_task is not None and not self._dependency_task.done():
            return

        async def run() -> None:
            try:
                await self.prepare_plugin_dependencies()
            except Exception as exc:
                self.dependency_error = str(exc)
                print(f"插件依赖准备失败: {exc}", file=sys.stderr, flush=True)

        self._dependency_task = asyncio.create_task(run())

    async def finish(self) -> None:
        """退出前等待环境操作结束，不能在包安装中途终止宿主。"""
        if self._dependency_task is not None:
            await self._dependency_task
        async with self._lock:
            pass

    async def install_plugin_dependencies(self, *, automatic: bool = False) -> Dict[str, Any]:
        """串行准备共享环境；同一失败声明只自动执行一次，显式重试会重新解析。"""
        async with self._lock:
            if self.update_requested:
                raise RuntimeError("宿主正在更新")
            report = await self.analyze_plugin_dependencies()
            snapshot = json.dumps(report["bots"], sort_keys=True)
            if automatic and snapshot == self._failed_dependency_snapshot and (report["requirements"] or report["conflicts"]):
                raise RuntimeError(self.dependency_error)
            try:
                result = await self._install_plugin_dependencies(report)
            except Exception as exc:
                self._failed_dependency_snapshot = snapshot
                self.dependency_error = str(exc)
                raise
            self._failed_dependency_snapshot = None
            self.dependency_error = ""
            return result

    async def _install_plugin_dependencies(self, report: Dict[str, Any]) -> Dict[str, Any]:
        if report["conflicts"]:
            raise RuntimeError("插件依赖与共享环境冲突，未修改环境：\n" + "\n".join(report["conflicts"]))
        if not report["requirements"]:
            return {**report, "installed": False}
        requirements = [
            CombinedPackageRequirement(
                package_name=item["package"], plugin_ids=tuple(item["plugin_ids"]),
                requirement_text=item["requirement"], version_spec="",
            )
            for item in report["all_requirements"]
        ]
        pipeline = PluginDependencyPipeline(project_root=RESOURCE_ROOT, force_plugin_compatibility=False)
        succeeded, error = await pipeline.resolve_requirements(requirements)
        if not succeeded:
            origins = "\n".join(f"{item['requirement']}: {', '.join(item['plugin_ids'])}" for item in report["all_requirements"])
            raise RuntimeError(f"依赖联合解析失败，Bot 未停止、环境未修改：{error}\n声明来源：\n{origins}")
        await self.supervisor.enter_maintenance()
        try:
            # 解析期间 Bot 仍可修改插件。停止后重读完整声明，避免按过期快照安装。
            current = await self.analyze_plugin_dependencies()
            if current["all_requirements"] != report["all_requirements"] or current["bots"] != report["bots"]:
                raise RuntimeError("解析期间插件依赖发生变化，已取消安装，请重新检查")
            succeeded, error = await pipeline.install_requirements(requirements)
            if not succeeded:
                raise RuntimeError(f"安装插件依赖失败: {error}")
            verified = await self.analyze_plugin_dependencies()
            if verified["requirements"] or verified["conflicts"]:
                raise RuntimeError("安装后依赖检查未通过: " + json.dumps(verified, ensure_ascii=False))
        finally:
            self.supervisor.leave_maintenance()
        return {**report, "installed": True}
