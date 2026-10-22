"""插件配置相关 WebUI 路由。"""

from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import asyncio

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import TypeAdapter
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
)

logger = get_logger("webui.plugin_routes")

router = APIRouter()

_SDK_VIRTUAL_GENERAL_SECTION_NAME = "general"
_PLUGIN_RESERVED_SECTION_NAME = "plugin"
_PLUGIN_ENABLED_VALIDATOR = TypeAdapter(bool)


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


def _get_virtual_general_field_names(runtime_snapshot: InspectPluginConfigResultPayload) -> List[str]:
    """获取 SDK 虚构 general 配置节中的字段名。

    SDK 生成配置 Schema 时，会把配置模型根级的扁平字段归入一个名为 ``general``
    的配置节，但这些字段在 ``config.toml`` 中位于根级，并不存在 ``[general]`` 表。
    只有 Schema 含有 general 节、该节字段全部是插件默认配置的根级键，且插件默认
    配置与当前配置中都没有真实的 general 表时，才把它视为虚构节，避免影响真正
    声明了 general 配置节的插件（包括自定义 Schema、未提供默认配置的插件）。

    Args:
        runtime_snapshot: 插件运行时返回的配置解析结果。

    Returns:
        List[str]: 虚构 general 节中的字段名；general 节不是虚构节时返回空列表。
    """

    sections = runtime_snapshot.config_schema.get("sections")
    if not isinstance(sections, dict):
        return []
    general_section = sections.get(_SDK_VIRTUAL_GENERAL_SECTION_NAME)
    if not isinstance(general_section, dict) or not isinstance(general_section.get("fields"), dict):
        return []
    if isinstance(runtime_snapshot.default_config.get(_SDK_VIRTUAL_GENERAL_SECTION_NAME), dict):
        return []
    if isinstance(runtime_snapshot.normalized_config.get(_SDK_VIRTUAL_GENERAL_SECTION_NAME), dict):
        return []
    general_field_names = list(general_section["fields"])
    # SDK 虚构节的字段都来自配置模型根级，默认配置会把它们全部导出到根级；自定义 Schema 声明的 general 节不满足这一点
    if not general_field_names or any(
        field_name not in runtime_snapshot.default_config for field_name in general_field_names
    ):
        return []
    return general_field_names


def _fold_root_fields_into_virtual_general(config_data: Dict[str, Any], field_names: List[str]) -> Dict[str, Any]:
    """把根级扁平字段移入虚构 general 节，使配置形状与 Schema 一致。

    WebUI 可视化表单按 ``config[section][field]`` 读写字段，因此读取时需要把
    根级扁平字段放到 ``general`` 下；保存时再由
    :func:`_unfold_virtual_general_into_root` 还原回根级。
    ``plugin`` 保留节被声明为普通 dict 时也会出现在虚构节中，它只复制不移动：
    WebUI 依赖根级 ``config.plugin.enabled`` 判断启用状态，而保存时 ``general.*``
    优先于根级键还原，复制不会产生冲突。

    Args:
        config_data: 磁盘形状（根级扁平字段）的配置。
        field_names: 虚构 general 节中的字段名。

    Returns:
        Dict[str, Any]: 扁平字段位于 ``general`` 下的配置副本。
    """

    folded_config = dict(config_data)
    general_config: Dict[str, Any] = {}
    for field_name in field_names:
        if field_name not in folded_config:
            continue
        if field_name == _PLUGIN_RESERVED_SECTION_NAME:
            general_config[field_name] = folded_config[field_name]
        else:
            general_config[field_name] = folded_config.pop(field_name)
    folded_config[_SDK_VIRTUAL_GENERAL_SECTION_NAME] = general_config
    return folded_config


def _unfold_virtual_general_into_root(config_patch: Dict[str, Any]) -> None:
    """把提交内容中虚构 general 节下的字段就地还原到根级。

    Args:
        config_patch: 本次提交的配置改动。
    """

    general_patch = config_patch.get(_SDK_VIRTUAL_GENERAL_SECTION_NAME)
    if not isinstance(general_patch, dict):
        return
    del config_patch[_SDK_VIRTUAL_GENERAL_SECTION_NAME]
    config_patch.update(general_patch)


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
) -> InspectPluginConfigResultPayload:
    """通过插件运行时解析配置元数据。

    Args:
        plugin_id: 插件 ID。
        config_data: 可选的配置内容。
        use_provided_config: 是否优先使用传入配置而不是磁盘配置。

    Returns:
        InspectPluginConfigResultPayload: 返回运行时解析结果，配置错误由 config_error 明确表示。

    Raises:
        ValueError: 插件运行时明确拒绝解析请求时抛出。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    runtime_manager = get_plugin_runtime_manager()
    try:
        result = await run_on_main_loop(
            runtime_manager.inspect_plugin_config(
                plugin_id, config_data, use_provided_config=use_provided_config,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=503, detail="插件配置运行时不可用，无法读取配置定义")
    return result


async def _validate_plugin_config_via_runtime(plugin_id: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
    """通过插件运行时对配置进行校验。

    Args:
        plugin_id: 插件 ID。
        config_data: 待校验的配置内容。

    Returns:
        Dict[str, Any]: 校验成功时返回规范化后的配置；运行时不可用时返回 HTTP 错误。

    Raises:
        ValueError: 插件运行时明确判定配置非法时抛出。
    """

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    runtime_manager = get_plugin_runtime_manager()
    result = await run_on_main_loop(runtime_manager.validate_plugin_config(plugin_id, config_data))
    if result is None:
        raise HTTPException(status_code=503, detail="插件配置运行时不可用，无法校验配置")
    return result


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
        runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
        raw_config = await asyncio.to_thread(_load_plugin_raw_config_from_disk, plugin_id, plugin_path)
        if runtime_snapshot.config_error:
            return {
                "success": True, "schema": None, "config": {}, "raw_config": raw_config,
                "config_error": runtime_snapshot.config_error,
            }
        current_config = dict(runtime_snapshot.normalized_config)
        # 表单按 Schema 的 general 节读写根级扁平字段，返回与 Schema 一致的形状
        if virtual_general_fields := _get_virtual_general_field_names(runtime_snapshot):
            current_config = _fold_root_fields_into_virtual_general(current_config, virtual_general_fields)
        schema = dict(runtime_snapshot.config_schema)
        if not schema:
            raise HTTPException(status_code=500, detail="插件未提供配置定义")
        message = "配置文件不存在，已返回默认配置" if not config_path.exists() else ""

        return {
            "success": True,
            "schema": schema,
            "config": current_config,
            "raw_config": raw_config,
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

        runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
        if runtime_snapshot.config_error:
            raise HTTPException(status_code=400, detail=runtime_snapshot.config_error)
        if not runtime_snapshot.config_schema:
            raise HTTPException(status_code=500, detail="插件未提供配置定义")
        return {"success": True, "schema": dict(runtime_snapshot.config_schema)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取插件配置 Schema 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e


@router.get("/config/{plugin_id}/raw")
def get_plugin_config_raw(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
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
        plugin_path = await asyncio.to_thread(find_plugin_path_by_id, plugin_id)
        if plugin_path is None:
            raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")

        config_path = get_plugin_config_path(plugin_id, plugin_path)
        try:
            await asyncio.to_thread(tomlkit.loads, request.config)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TOML 格式错误: {str(e)}") from e

        def save() -> None:
            backup_path = backup_file(config_path, "backup")
            if backup_path is not None:
                logger.info(f"已备份配置文件: {backup_path}")
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(request.config, encoding="utf-8")

        async def write_config() -> None:
            await asyncio.to_thread(save)

        from src.plugin_runtime.integration import get_plugin_runtime_manager

        runtime_status = await run_on_main_loop(
            get_plugin_runtime_manager().apply_plugin_config(plugin_id, write_config)
        )

        logger.info(f"已更新插件原始配置: {plugin_id}")
        return {
            "success": True, "message": "配置已保存", "runtime_status": runtime_status,
            "note": (
                "配置仍有错误，插件业务暂停" if runtime_status == "config_error" else
                "运行时尚未启动" if runtime_status == "stopped" else "配置已同步到插件运行时"
            ),
        }
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

        runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
        if runtime_snapshot.config_error:
            raise HTTPException(status_code=400, detail=runtime_snapshot.config_error)
        return {"success": True, "config": dict(runtime_snapshot.normalized_config)}
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
            runtime_snapshot = await _inspect_plugin_config_via_runtime(plugin_id)
            if runtime_snapshot.config_error:
                raise HTTPException(status_code=400, detail=runtime_snapshot.config_error)
            # 表单根级字段需还原回根级，否则会被模型校验丢弃。
            if _get_virtual_general_field_names(runtime_snapshot):
                _unfold_virtual_general_into_root(config_patch)
            config_data = _merge_plugin_config_patch(dict(runtime_snapshot.normalized_config), config_patch)
            config_data = await _validate_plugin_config_via_runtime(plugin_id, config_data)

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
def reset_plugin_config(plugin_id: str, maibot_session: Optional[str] = Cookie(None)) -> Dict[str, Any]:
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

    from src.plugin_runtime.integration import get_plugin_runtime_manager

    try:
        new_enabled = False

        async def write_config() -> None:
            nonlocal new_enabled
            plugin_path = await asyncio.to_thread(find_plugin_path_by_id, plugin_id)
            if plugin_path is None:
                raise HTTPException(status_code=404, detail=f"未找到插件: {plugin_id}")
            config_path = get_plugin_config_path(plugin_id, plugin_path)
            # 开关是宿主保留配置，直接读写磁盘，避免启用缺包插件前先冷导入其代码。
            raw_config = await asyncio.to_thread(_load_plugin_raw_config_from_disk, plugin_id, plugin_path)
            config = await asyncio.to_thread(tomlkit.parse, raw_config)
            plugin_section = config.get("plugin")
            if plugin_section is None:
                config["plugin"] = tomlkit.table()
            elif not isinstance(plugin_section, dict):
                raise ValueError("插件配置 plugin 必须为 TOML 表")
            plugin_config = cast(Any, config["plugin"])
            current_enabled = _PLUGIN_ENABLED_VALIDATOR.validate_python(plugin_config.get("enabled", True))
            new_enabled = not current_enabled
            plugin_config["enabled"] = new_enabled

            def save() -> None:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                save_toml_with_format(config, str(config_path))

            await asyncio.to_thread(save)

        runtime_status = await run_on_main_loop(
            get_plugin_runtime_manager().apply_plugin_config(plugin_id, write_config)
        )
        status = "启用" if new_enabled else "禁用"
        logger.info(f"插件 {plugin_id} 配置已{status}，运行时状态：{runtime_status}")
        return {
            "success": True, "enabled": new_enabled, "runtime_status": runtime_status,
            "message": f"插件已{status}" if runtime_status != "stopped" else f"插件{status}配置已保存",
            "note": "运行时尚未启动" if runtime_status == "stopped" else "状态更改已同步到插件运行时",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=f"插件状态同步失败：{exc}") from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换插件状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}") from e
