"""公共设置：统一读写网络、访问与保留策略，迁移由工作区初始化负责。"""

from dataclasses import asdict
from typing import Any, Callable, Dict

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictInt

import hashlib
import ssl
import tomlkit

from src.common.process_lease import ProcessLease
from src.config.shared_settings import validate_retention, validate_shared_webui

from .config import _validate, config_path
from .config_transaction import write_private
from .layout import Workspace


class CommonSettingsUpdate(BaseModel):
    revision: str
    webui: Dict[str, Any]
    retention: Dict[str, Any]


class AppearanceUpdate(BaseModel):
    webui_style: StrictInt = Field(ge=0, le=2)


def register_common_settings(app: FastAPI, workspace: Workspace, require_session: Callable) -> None:
    path = config_path(workspace.root)

    def snapshot() -> Dict[str, Any]:
        content = path.read_bytes()
        document = tomlkit.parse(content.decode("utf-8")).unwrap()
        network = _validate(document, path)
        config = asdict(network)
        config["enabled"] = config.pop("webui_enabled")
        config.update(validate_shared_webui(document["webui"]))
        return {
            "revision": hashlib.sha256(content).hexdigest(),
            "webui": config,
            "retention": validate_retention(document["retention"]),
            "restart_required": network != workspace.config,
        }

    @app.get("/api/host/settings", dependencies=[Depends(require_session)])
    def get_settings() -> Dict[str, Any]:
        return snapshot()

    @app.put("/api/host/settings", dependencies=[Depends(require_session)])
    def save_settings(body: CommonSettingsUpdate) -> Dict[str, Any]:
        with ProcessLease(workspace.host_directory / "model-config.lock"):
            current = snapshot()
            if current["revision"] != body.revision:
                raise HTTPException(409, "公共设置已被修改，请重新加载后保存")
            try:
                if set(body.webui) != set(current["webui"]):
                    raise ValueError("公共网络设置字段不完整或含未知字段")
                retention = validate_retention(body.retention)
                shared_webui = validate_shared_webui(body.webui)
                config = _validate({"webui": body.webui}, path)
                if config.ssl_certfile:
                    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(config.ssl_certfile, config.ssl_keyfile)
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc)) from exc
            document = tomlkit.parse(path.read_text(encoding="utf-8"))
            normalized = asdict(config)
            normalized["enabled"] = normalized.pop("webui_enabled")
            normalized.update(shared_webui)
            document["webui"].update(normalized)
            document["retention"] = retention
            write_private(path, tomlkit.dumps(document))
            return snapshot()

    @app.patch("/api/host/settings/appearance", dependencies=[Depends(require_session)])
    def save_appearance(body: AppearanceUpdate) -> Dict[str, Any]:
        # 外观只写自己的字段，不携带网络和清理策略的旧快照。
        with ProcessLease(workspace.host_directory / "model-config.lock"):
            document = tomlkit.parse(path.read_text(encoding="utf-8"))
            document["webui"]["webui_style"] = body.webui_style
            write_private(path, tomlkit.dumps(document))
            return snapshot()
