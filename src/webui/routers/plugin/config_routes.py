from typing import Any, Optional, cast

import json
import tomlkit

from fastapi import APIRouter, Cookie, HTTPException

from src.common.logger import get_logger
from src.webui.utils.toml_utils import save_toml_with_format

from .schemas import UpdatePluginConfigRequest, UpdatePluginRawConfigRequest
from .support import (
    backup_file,
    coerce_types,
    find_plugin_instance,
    find_plugin_path_by_id,
    normalize_dotted_keys,
    require_plugin_token,
    resolve_plugin_file_path,
)

logger = get_logger("webui.plugin_routes")

router = APIRouter()


def _build_schema_from_current_config(plugin_id: str, current_config: Any) -> dict[str, Any]:
    schema: dict[str, Any] = {
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
        section_fields: dict[str, Any] = {}
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


@router.get("/config/{plugin_id}/schema")
async def get_plugin_config_schema(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"获取插件配置 Schema: {plugin_id}")

    try:
        plugin_instance = find_plugin_instance(plugin_id)
        if plugin_instance and hasattr(plugin_instance, "get_webui_config_schema"):
            return {"success": True, "schema": plugin_instance.get_webui_config_schema()}

        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        schema_json_path = resolve_plugin_file_path(plugin_path, "config_schema.json")
        if schema_json_path.exists():
            try:
                with open(schema_json_path, "r", encoding="utf-8") as file_obj:
                    return {"success": True, "schema": json.load(file_obj)}
            except Exception as e:
                logger.warning(f"读取 config_schema.json 失败，回退到自动推断: {e}")

        current_config: Any = {}
        config_path = resolve_plugin_file_path(plugin_path, "config.toml")
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as file_obj:
                current_config = tomlkit.load(file_obj)

        return {"success": True, "schema": _build_schema_from_current_config(plugin_id, current_config)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件配置 Schema 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}/raw")
async def get_plugin_config_raw(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"获取插件原始配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = resolve_plugin_file_path(plugin_path, "config.toml")
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
) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"更新插件原始配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = resolve_plugin_file_path(plugin_path, "config.toml")
        try:
            tomlkit.loads(request.config)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        backup_path = backup_file(config_path, "backup")
        if backup_path is not None:
            logger.info(f"已备份配置文件: {backup_path}")

        with open(config_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(request.config)

        logger.info(f"已更新插件原始配置: {plugin_id}")
        return {"success": True, "message": "配置已保存", "note": "配置更改将在插件重新加载后生效"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件原始配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}")
async def get_plugin_config(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"获取插件配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = resolve_plugin_file_path(plugin_path, "config.toml")
        if not config_path.exists():
            return {"success": True, "config": {}, "message": "配置文件不存在"}

        with open(config_path, "r", encoding="utf-8") as file_obj:
            config = tomlkit.load(file_obj)
        return {"success": True, "config": dict(config)}
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
) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"更新插件配置: {plugin_id}")

    try:
        plugin_instance = find_plugin_instance(plugin_id)
        config_data = request.config or {}
        if plugin_instance and isinstance(config_data, dict):
            config_data = normalize_dotted_keys(config_data)
            if isinstance(plugin_instance.config_schema, dict):
                coerce_types(plugin_instance.config_schema, config_data)

        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = resolve_plugin_file_path(plugin_path, "config.toml")
        backup_path = backup_file(config_path, "backup")
        if backup_path is not None:
            logger.info(f"已备份配置文件: {backup_path}")

        save_toml_with_format(config_data, str(config_path))
        logger.info(f"已更新插件配置: {plugin_id}")
        return {"success": True, "message": "配置已保存", "note": "配置更改将在插件重新加载后生效"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/config/{plugin_id}/reset")
async def reset_plugin_config(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"重置插件配置: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = resolve_plugin_file_path(plugin_path, "config.toml")
        if not config_path.exists():
            return {"success": True, "message": "配置文件不存在，无需重置"}

        backup_path = backup_file(config_path, "reset", move_file=True)
        logger.info(f"已重置插件配置: {plugin_id}，备份: {backup_path}")
        return {"success": True, "message": "配置已重置，下次加载插件时将使用默认配置", "backup": str(backup_path)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置插件配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.post("/config/{plugin_id}/toggle")
async def toggle_plugin(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    require_plugin_token(maibot_session)
    logger.info(f"切换插件状态: {plugin_id}")

    try:
        plugin_path = find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = resolve_plugin_file_path(plugin_path, "config.toml")
        config = tomlkit.document()
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as file_obj:
                config = tomlkit.load(file_obj)

        if "plugin" not in config:
            config["plugin"] = tomlkit.table()

        plugin_config = cast(Any, config["plugin"])
        current_enabled = bool(plugin_config.get("enabled", True))
        new_enabled = not current_enabled
        plugin_config["enabled"] = new_enabled
        save_toml_with_format(config, str(config_path))

        status = "启用" if new_enabled else "禁用"
        logger.info(f"已{status}插件: {plugin_id}")
        return {"success": True, "enabled": new_enabled, "message": f"插件已{status}", "note": "状态更改将在下次加载插件时生效"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换插件状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e