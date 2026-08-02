"""插件配置相关 WebUI 路由。"""

from pathlib import Path
from typing import Any, Dict, Optional, cast

import asyncio

from fastapi import APIRouter, Cookie, HTTPException
import tomlkit

from src.common.logger import get_logger
from src.common.runtime_loop import run_on_main_loop
from src.plugin_runtime.protocol.envelope import InspectPluginConfigResultPayload
from src.webui.utils.toml_utils import save_toml_with_format

from .schemas import UpdatePluginConfigRequest, UpdatePluginRawConfigRequest
from .support import (
    backup_file,
    deep_merge,
    find_plugin_path_by_id,
    get_plugin_config_path,
    normalize_dotted_keys,
    require_plugin_token,
    resolve_plugin_file_path,
)

logger = get_logger("webui.plugin_routes")

router = APIRouter()

_PLUGIN_RUNTIME_TOGGLE_TIMEOUT_SECONDS = 16.0
_PLUGIN_RUNTIME_TOGGLE_POLL_INTERVAL_SECONDS = 0.1


def _to_builtin_data(obj: Any) -> Any:
    """将 TOML 对象递归转换为内建 Python 数据。

    Args:
        obj: 原始对象。

    Returns:
        Any: 转换后的内建数据结构。
    """

    if hasattr(obj, "unwrap"):
        try:
            obj = obj.unwrap()
        except Exception:
            pass

    if isinstance(obj, dict):
        return {str(key): _to_builtin_data(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_to_builtin_data(value) for value in obj]
    return obj


def _merge_plugin_config_patch(base_config: Dict[str, Any], patch_config: Dict[str, Any]) -> Dict[str, Any]:
    """以现有配置为基线合并本次插件配置改动。

    Args:
        base_config: 当前完整配置。
        patch_config: 本次提交的局部配置改动。

    Returns:
        Dict[str, Any]: 合并后的完整配置。
    """

    merged_config = cast(Dict[str, Any], _to_builtin_data(base_config))
    deep_merge(merged_config, patch_config)
    return merged_config


def _build_schema_from_current_config(plugin_id: str, current_config: Any) -> Dict[str, Any]:
    """根据当前配置内容自动推断一个兜底 Schema。

    Args:
        plugin_id: 插件 ID。
        current_config: 当前配置对象。

    Returns:
        Dict[str, Any]: 可供前端渲染的兜底 Schema。
    """

    schema: Dict[str, Any] = {
        "plugin_id": plugin_id,
        "plugin_info": {
            "name": plugin_id,
            "version": "",
            "description": "",
            "author": "",
        },
        "sections": {},
        "layout": {"type": "auto", "tabs": []},
        "_note": "插件未加载，仅返回当前配置结构",
    }

    for section_name, section_data in current_config.items():
        if not isinstance(section_data, dict):
            continue
        section_fields: Dict[str, Any] = {}
        for field_name, field_value in section_data.items():
            field_type = type(field_value).__name__
            ui_type = "text"
            item_type = None
            item_fields = None

            if isinstance(field_value, bool):
                ui_type = "switch"
            elif isinstance(field_value, (int, float)):
                ui_type = "number"
            elif isinstance(field_value, list):
                ui_type = "list"
                if field_value:
                    first_item = field_value[0]
                    if isinstance(first_item, dict):
                        item_type = "object"
                        item_fields = {
                            key: {
                                "type": "number" if isinstance(value, (int, float)) else "string",
                                "label": key,
                                "default": "" if isinstance(value, str) else 0,
                            }
                            for key, value in first_item.items()
                        }
                    elif isinstance(first_item, (int, float)):
                        item_type = "number"
                    else:
                        item_type = "string"
                else:
                    item_type = "string"
            elif isinstance(field_value, dict):
                ui_type = "json"

            section_fields[field_name] = {
                "name": field_name,
                "type": field_type,
                "default": field_value,
                "description": field_name,
                "label": field_name,
                "ui_type": ui_type,
                "required": False,
                "hidden": False,
                "disabled": False,
                "order": 0,
                "item_type": item_type,
                "item_fields": item_fields,
                "min_items": None,
                "max_items": None,
                "placeholder": None,
                "hint": None,
                "icon": None,
                "example": None,
                "choices": None,
                "min": None,
                "max": None,
                "step": None,
                "pattern": None,
                "max_length": None,
                "input_type": None,
                "rows": 3,
                "group": None,
                "depends_on": None,
                "depends_value": None,
            }

        schema["sections"][section_name] = {
            "name": section_name,
            "title": section_name,
            "description": None,
            "icon": None,
            "collapsed": False,
            "order": 0,
            "fields": section_fields,
        }

    return schema


def _coerce_scalar_value(field_schema: Dict[str, Any], value: Any) -> Any:
    """根据字段 Schema 规范化单个字段值。

    Args:
        field_schema: 单个字段 Schema。
        value: 当前字段值。

    Returns:
        Any: 规范化后的字段值。
    """

    field_type = str(field_schema.get("type", "") or "").lower()
    if field_type == "boolean" and isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in {"1", "true", "yes", "on"}:
            return True
        if normalized_value in {"0", "false", "no", "off"}:
            return False
    if field_type == "integer" and isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    if field_type == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if field_type == "array" and isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _coerce_config_by_plugin_schema(schema: Dict[str, Any], config_data: Dict[str, Any]) -> None:
    """根据插件配置 Schema 就地规范化配置值类型。

    Args:
        schema: 插件配置 Schema。
        config_data: 待规范化的配置字典。
    """

    sections = schema.get("sections")
    if not isinstance(sections, dict):
        return

    for section_name, section_schema in sections.items():
        if not isinstance(section_schema, dict):
            continue
        if section_name not in config_data or not isinstance(config_data[section_name], dict):
            continue

        section_fields = section_schema.get("fields")
        if not isinstance(section_fields, dict):
            continue

        section_config = cast(Dict[str, Any], config_data[section_name])
        for field_name, field_schema in section_fields.items():
            if field_name not in section_config or not isinstance(field_schema, dict):
                continue
            section_config[field_name] = _coerce_scalar_value(field_schema, section_config[field_name])


def _build_toml_document(config_data: Dict[str, Any]) -> tomlkit.TOMLDocument:
    """将普通字典转换为 TOML 文档对象。

    Args:
        config_data: 原始配置字典。

    Returns:
        tomlkit.TOMLDocument: 解析后的 TOML 文档。
    """

    if not config_data:
        return tomlkit.document()
    return tomlkit.parse(tomlkit.dumps(config_data))


def _load_plugin_config_from_disk(plugin_path: Path) -> Dict[str, Any]:
    """从磁盘读取插件配置。

    Args:
        plugin_path: 插件目录。

    Returns:
        Dict[str, Any]: 当前配置字典；文件不存在时返回空字典。
    """

    config_path = resolve_plugin_file_path(plugin_path, "config.toml")
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as file_obj:
        loaded_config = tomlkit.load(file_obj).unwrap()
    return loaded_config if isinstance(loaded_config, dict) else {}


def _load_plugin_raw_config_from_disk(plugin_id: str, plugin_path: Path) -> str:
    """从磁盘读取插件原始 TOML 配置文本。"""

    config_path = get_plugin_config_path(plugin_id, plugin_path)
    if not config_path.exists():
        return ""

    with open(config_path, "r", encoding="utf-8") as file_obj:
        return file_obj.read()


async def _inspect_plugin_config_via_runtime(
    plugin_id: str,
    config_data: Optional[Dict[str, Any]] = None,
    *,
    use_provided_config: bool = False,
) -> InspectPluginConfigResultPayload | None:
    """通过插件运行时解析配置元数据。

    Args:
        plugin_id: 插件 ID。
        config_data: 可选的配置内容。
        use_provided_config: 是否优先使用传入配置而不是磁盘配置。

    Returns:
        InspectPluginConfigResultPayload | None: 运行时可用时返回解析结果，否则返回 ``None``。

    Raises:
        ValueError: 插件运行时明确拒绝解析请求时抛出。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    runtime_manager = get_plugin_runtime_manager()
    return await run_on_main_loop(
        runtime_manager.inspect_plugin_config(
            plugin_id,
            config_data,
            use_provided_config=use_provided_config,
        )
    )


async def _validate_plugin_config_via_runtime(plugin_id: str, config_data: Dict[str, Any]) -> Dict[str, Any] | None:
    """通过插件运行时对配置进行校验。

    Args:
        plugin_id: 插件 ID。
        config_data: 待校验的配置内容。

    Returns:
        Dict[str, Any] | None: 校验成功时返回规范化后的配置；若运行时不可用则返回
        ``None``，由调用方自行回退到静态 Schema 方案。

    Raises:
        ValueError: 插件运行时明确判定配置非法时抛出。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    runtime_manager = get_plugin_runtime_manager()
    return await run_on_main_loop(runtime_manager.validate_plugin_config(plugin_id, config_data))


async def _wait_for_plugin_runtime_toggle(
    plugin_id: str,
    enabled: bool,
    *,
    timeout_seconds: float = _PLUGIN_RUNTIME_TOGGLE_TIMEOUT_SECONDS,
    poll_interval_seconds: float = _PLUGIN_RUNTIME_TOGGLE_POLL_INTERVAL_SECONDS,
) -> Optional[str]:
    """等待配置监听器把插件启停状态同步到运行时。

    WebUI 写入 ``enabled`` 后，插件运行时会通过文件监听器异步完成加载或卸载。
    这里等待运行时给出终态，避免接口返回后前端立即刷新，把启用前遗留的
    ``inactive`` 状态误判成加载失败。

    Args:
        plugin_id: 插件 ID。
        enabled: 目标启用状态。
        timeout_seconds: 最长等待时间。
        poll_interval_seconds: 状态轮询间隔。

    Returns:
        Optional[str]: 运行时终态；超时则返回 ``None``。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    runtime_manager = get_plugin_runtime_manager()
    terminal_statuses = {"success", "failed"} if enabled else {"inactive"}
    deadline = asyncio.get_running_loop().time() + timeout_seconds

    while True:
        runtime_status = runtime_manager.get_plugin_load_statuses().get(plugin_id)
        if runtime_status in terminal_statuses:
            return runtime_status
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(poll_interval_seconds)


@router.get("/config/{plugin_id}/bundle")
async def get_plugin_config_bundle(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """一次性返回插件配置页初始化所需的数据，避免重复运行时解析。"""

    require_plugin_token(maibot_session)
    logger.info(f"获取插件配置初始化数据: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        try:
            runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
        except ValueError as exc:
            logger.warning(f"插件 {plugin_id} 配置初始化数据读取失败，将回退到磁盘内容: {exc}")
            runtime_snapshot = None

        if runtime_snapshot is not None:
            current_config = dict(runtime_snapshot.normalized_config)
        else:
            current_config = _load_plugin_config_from_disk(plugin_path) if config_path.exists() else {}

        schema = (
            dict(runtime_snapshot.config_schema)
            if runtime_snapshot is not None and runtime_snapshot.config_schema
            else _build_schema_from_current_config(plugin_id, current_config)
        )
        message = "配置文件不存在，已返回默认配置" if runtime_snapshot is not None and not config_path.exists() else ""

        return {
            "success": True,
            "schema": schema,
            "config": current_config,
            "raw_config": _load_plugin_raw_config_from_disk(plugin_id, plugin_path),
            "message": message,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件配置初始化数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}/schema")
async def get_plugin_config_schema(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """按插件 ID 返回配置 Schema。

    Args:
        plugin_id: 插件 ID。
        maibot_session: 当前会话令牌。

    Returns:
        Dict[str, Any]: 包含 Schema 的响应字典。
    """

    require_plugin_token(maibot_session)
    logger.info(f"获取插件配置 Schema: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        try:
            runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
        except ValueError as exc:
            logger.warning(f"插件 {plugin_id} 配置 Schema 解析失败，将回退到弱推断: {exc}")
            runtime_snapshot = None

        if runtime_snapshot is not None and runtime_snapshot.config_schema:
            return {"success": True, "schema": dict(runtime_snapshot.config_schema)}

        current_config: Any = (
            dict(runtime_snapshot.normalized_config)
            if runtime_snapshot is not None
            else _load_plugin_config_from_disk(plugin_path)
        )

        return {"success": True, "schema": _build_schema_from_current_config(plugin_id, current_config)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件配置 Schema 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}/raw")
async def get_plugin_config_raw(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """获取插件原始 TOML 配置内容。

    Args:
        plugin_id: 插件 ID。
        maibot_session: 当前会话令牌。

    Returns:
        Dict[str, Any]: 包含原始配置文本的响应字典。
    """

    require_plugin_token(maibot_session)
    logger.info(f"获取插件原始配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        if not config_path.exists():
            return {"success": True, "config": "", "message": "配置文件不存在"}

        with open(config_path, "r", encoding="utf-8") as file_obj:
            return {"success": True, "config": file_obj.read()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件原始配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.put("/config/{plugin_id}/raw")
async def update_plugin_config_raw(
    plugin_id: str,
    request: UpdatePluginRawConfigRequest,
    maibot_session: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    """更新插件原始 TOML 配置内容。

    Args:
        plugin_id: 插件 ID。
        request: 原始配置更新请求。
        maibot_session: 当前会话令牌。

    Returns:
        Dict[str, Any]: 更新结果。
    """

    require_plugin_token(maibot_session)
    logger.info(f"更新插件原始配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        try:
            tomlkit.loads(request.config)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        backup_path = backup_file(config_path, "backup")
        if backup_path is not None:
            logger.info(f"已备份配置文件: {backup_path}")

        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(request.config)

        logger.info(f"已更新插件原始配置: {plugin_id}")
        return {"success": True, "message": "配置已保存", "note": "配置更改将自动热更新到对应插件"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件原始配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}")
async def get_plugin_config(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """获取插件配置字典。

    Args:
        plugin_id: 插件 ID。
        maibot_session: 当前会话令牌。

    Returns:
        Dict[str, Any]: 当前配置响应。
    """

    require_plugin_token(maibot_session)
    logger.info(f"获取插件配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        try:
            runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
        except ValueError as exc:
            logger.warning(f"插件 {plugin_id} 配置读取失败，将回退到磁盘内容: {exc}")
            runtime_snapshot = None

        if runtime_snapshot is not None:
            message = "配置文件不存在，已返回默认配置" if not config_path.exists() else ""
            return {
                "success": True,
                "config": dict(runtime_snapshot.normalized_config),
                "message": message,
            }

        if not config_path.exists():
            return {"success": True, "config": {}, "message": "配置文件不存在"}

        return {"success": True, "config": _load_plugin_config_from_disk(plugin_path)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.put("/config/{plugin_id}")
async def update_plugin_config(
    plugin_id: str,
    request: UpdatePluginConfigRequest,
    maibot_session: Optional[str] = Cookie(None),
) -> Dict[str, Any]:
    """更新插件结构化配置。

    Args:
        plugin_id: 插件 ID。
        request: 结构化配置更新请求。
        maibot_session: 当前会话令牌。

    Returns:
        Dict[str, Any]: 更新结果。
    """

    require_plugin_token(maibot_session)
    logger.info(f"更新插件配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_data = request.config or {}
        if isinstance(config_data, dict):
            config_patch = normalize_dotted_keys(config_data)
            runtime_snapshot = None
            try:
                runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
            except ValueError as exc:
                logger.warning(f"插件 {plugin_id} 保存前配置检查失败，将回退到磁盘内容: {exc}")

            base_config = (
                dict(runtime_snapshot.normalized_config)
                if runtime_snapshot is not None
                else _load_plugin_config_from_disk(plugin_path)
            )
            config_data = _merge_plugin_config_patch(base_config, config_patch)
            runtime_validated_config = await _validate_plugin_config_via_runtime(plugin_id, config_data)
            if isinstance(runtime_validated_config, dict):
                config_data = runtime_validated_config
            else:
                runtime_snapshot = await _inspect_plugin_config_via_runtime(
                    plugin_id,
                    config_data,
                    use_provided_config=True,
                )
                if runtime_snapshot is not None and runtime_snapshot.config_schema:
                    _coerce_config_by_plugin_schema(dict(runtime_snapshot.config_schema), config_data)

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        backup_path = backup_file(config_path, "backup")
        if backup_path is not None:
            logger.info(f"已备份配置文件: {backup_path}")

        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_toml_with_format(config_data, str(config_path))
        logger.info(f"已更新插件配置: {plugin_id}")
        return {"success": True, "message": "配置已保存", "note": "配置更改将自动热更新到对应插件"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/config/{plugin_id}/reset")
async def reset_plugin_config(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """重置插件配置文件。

    Args:
        plugin_id: 插件 ID。
        maibot_session: 当前会话令牌。

    Returns:
        Dict[str, Any]: 重置结果。
    """

    require_plugin_token(maibot_session)
    logger.info(f"重置插件配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        if not config_path.exists():
            return {"success": True, "message": "配置文件不存在，无需重置"}

        backup_path = backup_file(config_path, "reset", move_file=True)
        logger.info(f"已重置插件配置: {plugin_id}，备份: {backup_path}")
        return {"success": True, "message": "配置已重置，运行时将自动刷新为默认配置", "backup": str(backup_path)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/config/{plugin_id}/toggle")
async def toggle_plugin(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """切换插件启用状态。

    Args:
        plugin_id: 插件 ID。
        maibot_session: 当前会话令牌。

    Returns:
        Dict[str, Any]: 切换结果。
    """

    require_plugin_token(maibot_session)
    logger.info(f"切换插件状态: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        try:
            runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
        except ValueError as exc:
            logger.warning(f"插件 {plugin_id} 状态切换前配置解析失败，将回退到磁盘内容: {exc}")
            runtime_snapshot = None

        current_config = (
            dict(runtime_snapshot.normalized_config)
            if runtime_snapshot is not None
            else _load_plugin_config_from_disk(plugin_path)
        )
        config = _build_toml_document(current_config)

        plugin_section = config.get("plugin")
        if plugin_section is None or not hasattr(plugin_section, "get"):
            config["plugin"] = tomlkit.table()

        plugin_config = cast(Any, config["plugin"])
        current_enabled = (
            bool(runtime_snapshot.enabled)
            if runtime_snapshot is not None
            else bool(plugin_config.get("enabled", True))
        )
        new_enabled = not current_enabled
        plugin_config["enabled"] = new_enabled
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_toml_with_format(config, str(config_path))

        runtime_status = None
        if runtime_snapshot is not None:
            runtime_status = await run_on_main_loop(
                _wait_for_plugin_runtime_toggle(plugin_id, new_enabled)
            )
            if runtime_status is None:
                logger.warning(f"插件 {plugin_id} 配置已写入，但等待运行时同步启停状态超时")

        status = "启用" if new_enabled else "禁用"
        logger.info(f"已{status}插件: {plugin_id}")
        if runtime_snapshot is None:
            runtime_note = "状态更改将在插件运行时启动后生效"
        elif runtime_status is None:
            runtime_note = "运行时状态同步仍在进行"
        else:
            runtime_note = "状态更改已同步到插件运行时"
        return {
            "success": True,
            "enabled": new_enabled,
            "message": f"插件已{status}",
            "note": runtime_note,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换插件状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e
