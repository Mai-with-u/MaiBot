"""宿主按 Bot 路由数据传输；Bot 停止时接口和任务仍然可用。"""
from functools import partial
from typing import Callable, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, Response

import asyncio

from src.common.paths import SOURCE_ROOT
from src.common.version import read_project_version

from .maintenance import HostMaintenance
from .transfer_service import TransferService
from .transfer_storage import import_archive, portable_config


class TransferResponse(Response):
    def __init__(self, app: FastAPI, path: str) -> None:
        super().__init__()
        self.app, self.path = app, path

    async def __call__(self, scope, receive, send) -> None:
        await self.app({**scope, "path": self.path, "raw_path": self.path.encode(), "root_path": ""}, receive, send)


def register_data_transfer(app: FastAPI, maintenance: HostMaintenance, require_session: Callable) -> None:
    services: Dict[str, FastAPI] = {}
    engines: Dict[str, TransferService] = {}
    workspace = maintenance.supervisor.workspace
    version = read_project_version(SOURCE_ROOT)

    @app.api_route("/api/host/bots/{bot_id}/proxy/api/webui/data-transfer/{transfer_path:path}",
                   methods=["GET", "POST", "DELETE"], dependencies=[Depends(require_session)])
    async def dispatch(request: Request, bot_id: str, transfer_path: str) -> Response:
        try:
            bot = await asyncio.to_thread(workspace.registry.get_bot, bot_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(404, "未找到 Bot") from exc
        if bot_id not in services:
            service = TransferService(bot.directory, workspace.host_directory / "transfers" / bot_id,
                                      version,
                                      run_operation=partial(maintenance.transfer_data, bot_id),
                                      prepare_export=partial(portable_config, bot, workspace.host_directory))
            service.apply_import = partial(import_archive, bot, workspace.host_directory, service)
            inner = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
            inner.include_router(service.router)
            services[bot_id] = inner
            engines[bot_id] = service
        await asyncio.to_thread(engines[bot_id].cleanup_transfer_temp_files)
        return TransferResponse(services[bot_id], "/data-transfer/" + transfer_path)
