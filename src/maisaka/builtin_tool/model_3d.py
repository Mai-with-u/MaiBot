"""当前聊天流的 3D 三维建模、检查、编辑和渲染内置工具。"""

from typing import Any, Dict, List, Optional, Tuple
import asyncio
import base64
import json
import math
import re

from src.common.logger import get_logger
from src.config.config import global_config
from src.core.tooling import ToolContentItem, ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.maisaka.visual.mode_utils import resolve_enable_visual_planner

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_model_3d")


def _vector_schema(description: str, default: List[float]) -> Dict[str, Any]:
    """声明按 X、Y、Z 顺序排列的三维向量。"""

    return {
        "type": "array",
        "description": description,
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
        "default": default,
    }


def _color_schema(description: str, *, default: Optional[str] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "string", "description": description, "pattern": "^#[0-9A-Fa-f]{6}$"}
    if default is not None:
        schema["default"] = default
    return schema


def _part_schema() -> Dict[str, Any]:
    """创建和新增部件共用同一份完整几何参数声明。"""

    return {
        "type": "object",
        "description": "一个具名纯色网格部件；几何体中心在原点，Z 轴朝上，先局部缩放、依次绕 XYZ 旋转，再平移。",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 64, "description": "部件名称，模型内唯一。"},
            "primitive": {
                "type": "string",
                "enum": ["box", "sphere", "cylinder", "cone", "torus"],
                "description": "基础几何体：长方体、球、圆柱、圆锥或圆环。",
            },
            "size": _vector_schema("box 的 XYZ 边长，均为正数，默认 [1,1,1]。", [1, 1, 1]),
            "radius": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 0.5,
                "description": "球、圆柱、圆锥的半径，或圆环主半径。",
            },
            "height": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 1,
                "description": "圆柱或圆锥的高度，沿 Z 轴。",
            },
            "tube_radius": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": 0.15,
                "description": "圆环管半径。",
            },
            "segments": {
                "type": "integer",
                "minimum": 8,
                "maximum": 64,
                "default": 32,
                "description": "圆柱、圆锥和圆环的圆周分段数。",
            },
            "position": _vector_schema("XYZ 平移，默认 [0,0,0]。", [0, 0, 0]),
            "rotation": _vector_schema("依次绕 X、Y、Z 轴旋转的角度，单位为度，默认 [0,0,0]。", [0, 0, 0]),
            "scale": _vector_schema("XYZ 局部缩放倍数，均为正数，默认 [1,1,1]。", [1, 1, 1]),
            "color": _color_schema("部件纯色，格式 #RRGGBB。", default="#7C9BC0"),
        },
        "required": ["name", "primitive"],
        "additionalProperties": False,
    }


def _tool_spec(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def get_create_tool_spec() -> ToolSpec:
    """声明由基础几何部件创建三维模型的工具。"""

    return _tool_spec(
        "create_3d_model",
        "创建 3D 三维模型，用多个基础几何部件进行组合建模，支持 box/sphere/cylinder/cone/torus。"
        "parts 必须有 1..64 个部件，每个部件需要唯一 name（1..64 字符）及 primitive，其余几何参数可省略。"
        "采用 Z 轴朝上的坐标系，几何体中心位于原点，先局部缩放、按 XYZ 顺序旋转（角度制），最后平移。"
        "资产属于当前聊天流，返回 model_id、部件和文件信息；可继续 inspect_3d_model 检查、"
        "edit_3d_model 修改、render_3d_model 看图。"
        '调用示例：create_3d_model({"name":"小桌子","parts":[{"name":"桌面","primitive":"box",'
        '"size":[2,1,0.2],"position":[0,0,1],"color":"#7C9BC0"}]}).',
        {
            "name": {"type": "string", "minLength": 1, "maxLength": 64, "description": "三维模型的名称，1..64 字符。"},
            "parts": {"type": "array", "minItems": 1, "maxItems": 64, "items": _part_schema()},
        },
        ["name", "parts"],
    )


def get_inspect_tool_spec() -> ToolSpec:
    """声明已保存模型检查和外部网格导入工具。"""

    spec = _tool_spec(
        "inspect_3d_model",
        "检查或导入 3D 三维模型，为建模和编辑提供部件名、包围盒、面数及资产文件信息。"
        "model_id 与 source_file 必须且只能提供一个。model_id 检查当前聊天流已保存的模型；"
        "source_file 导入当前聊天流 data/3d_models/<真实session_id>/imports/ 目录内的相对文件路径，"
        "仅支持 .obj/.stl/.ply/.glb 自包含纯色网格，不能访问其他聊天流或目录外文件。"
        "导入会创建新资产并返回新 model_id、parts、files，可接着编辑或渲染。"
        '调用示例：inspect_3d_model({"model_id":"上次返回的model_id"})；'
        '导入示例：inspect_3d_model({"source_file":"零件.glb"})。',
        {
            "model_id": {
                "type": "string",
                "minLength": 1,
                "description": "当前聊天流已有的模型 ID，与 source_file 二选一。",
            },
            "source_file": {
                "type": "string",
                "minLength": 1,
                "description": "相对当前聊天流 imports 目录的 .obj/.stl/.ply/.glb 文件路径，与 model_id 二选一。",
            },
        },
        [],
    )
    spec.parameters_schema["oneOf"] = [{"required": ["model_id"]}, {"required": ["source_file"]}]
    return spec


def get_edit_tool_spec() -> ToolSpec:
    """声明按部件编辑三维模型并保存副本的工具。"""

    operation_schema = {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["add", "remove", "transform", "color"],
                "description": "新增、删除、相对变换或改色。",
            },
            "part": _part_schema(),
            "target": {
                "type": "string",
                "minLength": 1,
                "description": "已有部件名；transform/color 可用 * 指整个模型，remove 禁止 *。",
            },
            "position": _vector_schema("相对 XYZ 平移量，默认 [0,0,0]。", [0, 0, 0]),
            "rotation": _vector_schema("相对 XYZ 旋转角度，默认 [0,0,0]；绕当前包围盒中心。", [0, 0, 0]),
            "scale": _vector_schema("相对 XYZ 缩放倍数，均为正数，默认 [1,1,1]；绕当前包围盒中心。", [1, 1, 1]),
            "color": _color_schema("改色操作使用的 #RRGGBB 纯色。"),
        },
        "required": ["op"],
        "additionalProperties": False,
        "oneOf": [
            {"properties": {"op": {"enum": ["add"]}}, "required": ["part"]},
            {"properties": {"op": {"enum": ["remove"]}, "target": {"not": {"enum": ["*"]}}}, "required": ["target"]},
            {"properties": {"op": {"enum": ["transform"]}}, "required": ["target"]},
            {"properties": {"op": {"enum": ["color"]}}, "required": ["target", "color"]},
        ],
    }
    return _tool_spec(
        "edit_3d_model",
        "编辑当前聊天流的 3D 三维模型，用于迭代建模。operations 按顺序执行，数量为 1..64。"
        "add 需要 part（与 create_3d_model 的完整部件对象定义相同，至少包含 name/primitive）；"
        "remove/transform/color 需要 target 已有部件名，color 还必须提供 #RRGGBB 的 color。"
        "transform 的 position/rotation/scale 是相对变换：绕部件当前包围盒中心缩放，按 XYZ 顺序旋转（度），再平移。"
        "target='*' 可整体变换或改色，整体变换使用整个模型的当前包围盒中心，remove 禁止 '*'."
        "每次编辑保存副本并返回新 model_id，不覆盖源模型；后续检查、渲染和编辑请使用新 ID。"
        '调用示例：edit_3d_model({"model_id":"上次返回的model_id","operations":'
        '[{"op":"transform","target":"桌面","position":[0,0,0.2]},'
        '{"op":"color","target":"*","color":"#FF8800"}]}).',
        {
            "model_id": {"type": "string", "minLength": 1, "description": "待编辑的当前聊天流模型 ID。"},
            "operations": {"type": "array", "minItems": 1, "maxItems": 64, "items": operation_schema},
        },
        ["model_id", "operations"],
    )


def get_render_tool_spec() -> ToolSpec:
    """声明供模型继续观察和修改的 PNG 预览工具。"""

    return _tool_spec(
        "render_3d_model",
        "将当前聊天流的 3D 三维模型渲染为 PNG 预览，检查建模外观并继续调用 edit_3d_model 迭代。"
        "Z 轴朝上，正交投影、自动取景；azimuth 是水平方位角（度，正 Y 方向为 90），"
        "elevation 是仰角（-90..90 度），width/height 是 128..1024 的整数像素。"
        "默认 640×480、方位角 45、仰角 30、背景 #F1F5F9。返回模型和 render_file 信息，以及可在上下文查看的图片。"
        "渲染不会自动发给用户，可用 send_image 的 media_index 或 reply 的 attach_pic 引用 tool_result:<call_id>:1 发送。"
        '调用示例：render_3d_model({"model_id":"上次返回的model_id","width":640,"height":480,'
        '"azimuth":45,"elevation":30,"background":"#F1F5F9"})。',
        {
            "model_id": {"type": "string", "minLength": 1, "description": "当前聊天流待渲染的模型 ID。"},
            "width": {
                "type": "integer",
                "minimum": 128,
                "maximum": 1024,
                "default": 640,
                "description": "PNG 宽度（像素）。",
            },
            "height": {
                "type": "integer",
                "minimum": 128,
                "maximum": 1024,
                "default": 480,
                "description": "PNG 高度（像素）。",
            },
            "azimuth": {"type": "number", "default": 45, "description": "水平方位角，单位为度，正 Y 方向为 90。"},
            "elevation": {
                "type": "number",
                "minimum": -90,
                "maximum": 90,
                "default": 30,
                "description": "视线仰角，单位为度。",
            },
            "background": _color_schema("PNG 背景纯色，格式 #RRGGBB。", default="#F1F5F9"),
        },
        ["model_id"],
    )


_SPEC_BUILDERS = {
    "create_3d_model": get_create_tool_spec,
    "inspect_3d_model": get_inspect_tool_spec,
    "edit_3d_model": get_edit_tool_spec,
    "render_3d_model": get_render_tool_spec,
}

# 四个三维工具由同一个实验性开关统一启停，供内置工具目录按配置筛选。
TOOL_NAMES = frozenset(_SPEC_BUILDERS)


def _validate_arguments(tool_name: str, arguments: Dict[str, Any]) -> None:
    """运行时不会自动校验 schema；先严格检查顶层，内部几何参数交由服务校验。"""

    if tool_name not in _SPEC_BUILDERS:
        raise ValueError(f"未知的三维建模工具：{tool_name}")
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须为 JSON 对象。")

    schema = _SPEC_BUILDERS[tool_name]().parameters_schema
    properties = schema["properties"]
    unknown = [str(key) for key in arguments if key not in properties]
    if unknown:
        raise ValueError(f"不支持的顶层参数：{', '.join(unknown)}")
    missing = [key for key in schema["required"] if key not in arguments]
    if missing:
        raise ValueError(f"缺少必填参数：{', '.join(missing)}")
    if tool_name == "inspect_3d_model" and len(arguments) != 1:
        raise ValueError("model_id 与 source_file 必须且只能提供一个。")

    for key, value in arguments.items():
        field = properties[key]
        field_type = field["type"]
        if field_type == "string":
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} 必须为非空字符串。")
            if "maxLength" in field and len(value) > field["maxLength"]:
                raise ValueError(f"{key} 最多包含 {field['maxLength']} 个字符。")
            if "pattern" in field and re.fullmatch(field["pattern"], value) is None:
                raise ValueError(f"{key} 必须是 #RRGGBB 格式的颜色。")
        elif field_type == "array":
            if not isinstance(value, list) or not field["minItems"] <= len(value) <= field["maxItems"]:
                raise ValueError(f"{key} 必须为包含 1..64 项的数组。")
        elif field_type in {"integer", "number"}:
            valid_type = type(value) is int if field_type == "integer" else type(value) in {int, float}
            if not valid_type:
                raise ValueError(f"{key} 必须为{'整数' if field_type == 'integer' else '有限数值'}。")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{key} 必须为有限数值。")
            if ("minimum" in field and value < field["minimum"]) or ("maximum" in field and value > field["maximum"]):
                raise ValueError(f"{key} 必须在 {field['minimum']}..{field['maximum']} 范围内。")


def _dispatch(session_id: str, tool_name: str, arguments: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[bytes]]:
    """在线程内加载依赖、构造工作区并执行同步服务，避免占用事件循环。"""

    from src.maisaka.modeling.service import ModelWorkspace

    workspace = ModelWorkspace(session_id)
    if tool_name == "create_3d_model":
        return workspace.create_model(**arguments), None
    if tool_name == "inspect_3d_model":
        return workspace.inspect_model(**arguments), None
    if tool_name == "edit_3d_model":
        return workspace.edit_model(**arguments), None
    if tool_name == "render_3d_model":
        return workspace.render_model(**arguments)
    raise ValueError(f"未知的三维建模工具：{tool_name}")


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """按工具名执行三维建模，工作区归属只取可信运行时中的真实聊天流 ID。"""

    del context
    try:
        _validate_arguments(invocation.tool_name, invocation.arguments)
        warnings: List[str] = []
        if invocation.tool_name == "render_3d_model":
            # 复用实际视觉开关解析，multimodal 配置不匹配时让其明确失败。
            if not resolve_enable_visual_planner():
                warnings.append(
                    "PNG 已生成并附到上下文，但当前 planner 视觉设置无法读取图片。"
                    "请开启 planner 视觉模式并为所有 planner 模型开启 visual；仍可将预览发送给用户。"
                )
            if global_config.visual.max_image_num == 0:
                warnings.append(
                    "PNG 已生成并附到上下文，但当前 planner 图片额度 max_image_num=0，无法读取图片。"
                    "请提高 visual.max_image_num 图片额度；仍可将预览发送给用户。"
                )
        model_info, png = await asyncio.to_thread(
            _dispatch, tool_ctx.runtime.session_id, invocation.tool_name, invocation.arguments
        )
        structured_content = dict(model_info)
        if warnings:
            structured_content["warnings"] = warnings
        content_items: List[ToolContentItem] = []
        if png is not None:
            media_index = f"tool_result:{invocation.call_id.strip() or invocation.tool_name}:1"
            structured_content["media_index"] = media_index
            preview_message = (
                "三维模型预览已生成，当前 planner 无法读取图片，请按 warnings 调整视觉设置后再看图编辑。"
                if warnings
                else "三维模型预览已生成，可查看图片后继续编辑。"
            )
            structured_content["message"] = (
                f'{preview_message}需要发送给用户时，可调用 send_image(media_index="{media_index}") '
                f'或 reply(attach_pic="{media_index}")。'
            )
            content_items.append(
                ToolContentItem(content_type="image", mime_type="image/png", data=base64.b64encode(png).decode("ascii"))
            )
        result = tool_ctx.build_success_result(
            invocation.tool_name,
            json.dumps(structured_content, ensure_ascii=False),
            structured_content=structured_content,
        )
        result.content_items = content_items
        return result
    except (ValueError, OSError, ImportError) as exc:
        return tool_ctx.build_failure_result(invocation.tool_name, f"三维模型工具执行失败：{type(exc).__name__}: {exc}")
    except Exception as exc:
        logger.exception(f"三维模型工具执行异常：tool={invocation.tool_name}")
        return tool_ctx.build_failure_result(invocation.tool_name, f"三维模型工具执行异常：{type(exc).__name__}: {exc}")
