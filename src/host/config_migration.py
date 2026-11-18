"""在 Worker 升级删除旧字段前，迁移宿主接管的配置；成功落盘后才清理来源。"""

from pathlib import Path
from typing import Dict, List

import ssl

from src.config.shared_settings import WEBUI_DEFAULTS, validate_shared_webui

from .config import _validate


MAIM_NETWORK_FIELDS = (
    "ws_server_host", "ws_server_port", "api_server_host", "api_server_port",
    "api_server_use_wss", "api_server_cert_file", "api_server_key_file",
)
WEBUI_HOST_FIELDS = (
    "enabled", "host", "port", "secure_cookie", "anti_crawler_mode", "allowed_ips",
    "trusted_proxies", "trust_xff", "mode", "webui_style", "enforce_public_outbound_url",
)


def strip_host_settings(document: dict) -> None:
    """启动迁移与旧包导入共用来源字段清理，清理已由宿主接管的字段。"""
    document.pop("http", None)
    document.get("files", {}).pop("transfer_base_url", None)
    for key in MAIM_NETWORK_FIELDS:
        document.get("maim_message", {}).pop(key, None)
    for key in WEBUI_HOST_FIELDS:
        document.get("webui", {}).pop(key, None)
    if "webui" in document and not document["webui"]:
        document.pop("webui")


def plan_host_settings(host_path: Path, host: dict, documents: Dict[Path, dict]) -> None:
    """在内存中校验并迁移网络配置，调用方统一提交。"""
    webui = host["webui"]
    candidates: Dict[str, Dict[str, List[str]]] = {key: {} for key in ("public_base_url", "ssl_certfile", "ssl_keyfile")}
    # 未发布改动并入同一迁移计划；已存在公共值时不再从 Bot 覆盖。
    old_webui = [doc.unwrap().get("webui", {}) for doc in documents.values()]
    for settings in old_webui:
        validate_shared_webui({**WEBUI_DEFAULTS, **settings})
    for key, default in WEBUI_DEFAULTS.items():
        values = [settings[key] for settings in old_webui if key in settings]
        if key not in webui:
            if key == "webui_style" and len(set(values)) > 1:
                raise ValueError("旧 Bot 的界面风格不一致，请统一风格后迁移公共设置")
            webui[key] = (any(values) if key == "enforce_public_outbound_url" else values[0]) if values else default
    validate_shared_webui(host.unwrap()["webui"])
    sources = []
    for path, doc in documents.items():
        directory = path.parent.parent
        files = doc.get("files", {})
        message = doc.get("maim_message", {})
        values = {"public_base_url": files.get("transfer_base_url", "")}
        if message.get("api_server_use_wss", False):
            for target, source in (("ssl_certfile", "api_server_cert_file"), ("ssl_keyfile", "api_server_key_file")):
                value = message.get(source, "")
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{path}: 已启用 WSS，但 {source} 未填写；旧配置未清理")
                certificate = Path(value)
                values[target] = str((directory / certificate).resolve())
        for key, value in values.items():
            if not isinstance(value, str):
                raise ValueError(f"{path}: {key} 必须是字符串；旧配置未清理")
            value = value.strip().rstrip("/") if key == "public_base_url" else value
            if value:
                candidates[key].setdefault(value, []).append(str(path))
        before = doc.unwrap()
        strip_host_settings(doc)
        if doc.unwrap() != before:
            sources.append(path)

    # 先验证全部来源，避免处理到后一个 Bot 才发现冲突，前一个却已丢失配置。
    for key, options in candidates.items():
        current = webui.get(key, "")
        if current:
            options.setdefault(current, []).append(str(host_path))
        if len(options) > 1:
            locations = "; ".join(f"{value}: {', '.join(paths)}" for value, paths in options.items())
            raise ValueError(f"宿主配置迁移冲突 {key}（{locations}）；请统一配置后重试，旧字段未清理")
        if options:
            webui[key] = next(iter(options))
    resolved = _validate(host.unwrap(), host_path)
    if resolved.ssl_certfile and sources:
        # 验证证书与私钥确实匹配，不能写入一个无法启动的 TLS 配置后删除来源。
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(resolved.ssl_certfile, resolved.ssl_keyfile)
