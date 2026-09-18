"""三维内置工具的契约、异步执行及图片上下文集成测试。"""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional, Tuple
from unittest.mock import Mock

from PIL import Image

import asyncio
import base64
import importlib
import pytest
import sys
import threading


TOOL_ARGUMENTS = {
    "create_3d_model": {"name": "测试模型", "parts": [{"name": "主体", "primitive": "box"}]},
    "inspect_3d_model": {"model_id": "test-model"},
    "edit_3d_model": {
        "model_id": "test-model",
        "operations": [{"op": "transform", "target": "主体", "position": [0, 0, 1]}],
    },
    "render_3d_model": {"model_id": "test-model"},
}


@pytest.fixture(scope="module")
def tool_modules() -> Iterator[SimpleNamespace]:
    """使用内存配置延迟导入真实工具与引擎，避免真实 bot 配置的启动升级。"""

    from src.config import model_configs, official_configs

    config = SimpleNamespace(
        bot=official_configs.BotConfig(),
        personality=official_configs.PersonalityConfig(),
        chat=official_configs.ChatConfig(),
        experimental=official_configs.ExperimentalConfig(enable_3d_modeling=True),
        visual=official_configs.VisualConfig(),
        expression=official_configs.ExpressionConfig(),
        jargon=official_configs.JargonConfig(),
        a_memorix=official_configs.AMemorixConfig(),
        message_receive=official_configs.MessageReceiveConfig(),
        voice=official_configs.VoiceConfig(),
        emoji=official_configs.EmojiConfig(),
        keyword_reaction=official_configs.KeywordReactionConfig(),
        response_post_process=official_configs.ResponsePostProcessConfig(),
        chinese_typo=official_configs.ChineseTypoConfig(),
        response_splitter=official_configs.ResponseSplitterConfig(),
        telemetry=official_configs.TelemetryConfig(),
        log=official_configs.LogConfig(),
        debug=official_configs.DebugConfig(),
        maim_message=official_configs.MaimMessageConfig(),
        webui=official_configs.WebUIConfig(),
        database=official_configs.DatabaseConfig(),
        mcp=official_configs.MCPConfig(),
        plugin=official_configs.PluginConfig(),
        plugin_runtime=official_configs.PluginRuntimeConfig(),
    )
    models = SimpleNamespace(model_task_config=model_configs.ModelTaskConfig(), models=[], api_providers=[])
    config.visual.planner_mode = "auto"
    config.visual.max_image_num = 4
    models.model_task_config.planner.model_list = ["test-visual"]
    models.models = [SimpleNamespace(name="test-visual", visual=True)]
    config_module = ModuleType("src.config.config")
    config_module.global_config = config
    config_module.model_config = models
    config_module.MMC_VERSION = "test"
    config_module.config_manager = SimpleNamespace(
        get_global_config=lambda: config,
        get_model_config=lambda: models,
        register_reload_callback=Mock(),
        unregister_reload_callback=Mock(),
    )
    # 沿用现有测试的 sys.modules + monkeypatch 隔离方式，不导入会写配置的 config.py。
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "src.config.config", config_module)
        builtin = importlib.import_module("src.maisaka.builtin_tool")
        model_3d = importlib.import_module("src.maisaka.builtin_tool.model_3d")
        visual = importlib.import_module("src.maisaka.visual.mode_utils")
        patch.setattr(builtin, "global_config", config)
        patch.setattr(model_3d, "global_config", config)
        patch.setattr(visual, "global_config", config)
        patch.setattr(visual, "config_manager", config_module.config_manager)
        yield SimpleNamespace(
            builtin=builtin,
            tools=model_3d,
            service=importlib.import_module("src.maisaka.modeling.service"),
            provider=importlib.import_module("src.maisaka.builtin_tool.provider"),
            tooling=importlib.import_module("src.core.tooling"),
            runtime=importlib.import_module("src.maisaka.runtime"),
            engine=importlib.import_module("src.maisaka.reasoning_engine"),
            context=importlib.import_module("src.maisaka.builtin_tool.context"),
            messages=importlib.import_module("src.maisaka.context.messages"),
            components=importlib.import_module("src.common.data_models.message_component_data_model"),
            items=importlib.import_module("src.llm_models.payload_content.context_item"),
            tool_option=importlib.import_module("src.llm_models.payload_content.tool_option"),
            config=config,
            models=models,
        )


@pytest.fixture
def tool_context(tool_modules: SimpleNamespace) -> Any:
    runtime = SimpleNamespace(session_id="trusted-session", _chat_history=[], log_prefix="[三维测试]")
    engine = tool_modules.engine.MaisakaReasoningEngine(runtime)
    engine._active_logical_turn_id = "test-turn"
    return tool_modules.context.BuiltinToolRuntimeContext(engine, runtime)


@pytest.fixture
def png_bytes() -> bytes:
    with BytesIO() as output:
        Image.new("RGB", (2, 2), "#7C9BC0").save(output, format="PNG")
        return output.getvalue()


@pytest.fixture
def workspace_stub(tool_modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, png_bytes: bytes) -> SimpleNamespace:
    state = SimpleNamespace(
        calls=[],
        construction_error=None,
        method_error=None,
        png=png_bytes,
        model_info={
            "model_id": "new-model",
            "name": "测试模型",
            "parts": [{"name": "主体", "face_count": 12}],
            "files": {"glb": "models/new-model/model.glb"},
            "render_file": "renders/new-model.png",
        },
    )

    class Workspace:
        def __init__(self, session_id: str) -> None:
            state.calls.append(("init", session_id, threading.get_ident()))
            if state.construction_error is not None:
                raise state.construction_error

        def _call(self, method: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
            state.calls.append((method, arguments, threading.get_ident()))
            if state.method_error is not None:
                raise state.method_error
            return state.model_info

        def create_model(self, name: str, parts: List[Dict[str, Any]]) -> Dict[str, Any]:
            return self._call("create_model", {"name": name, "parts": parts})

        def inspect_model(self, model_id: Optional[str] = None, source_file: Optional[str] = None) -> Dict[str, Any]:
            return self._call("inspect_model", {"model_id": model_id, "source_file": source_file})

        def edit_model(self, model_id: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
            return self._call("edit_model", {"model_id": model_id, "operations": operations})

        def render_model(
            self,
            model_id: str,
            width: int = 640,
            height: int = 480,
            azimuth: float = 45,
            elevation: float = 30,
            background: str = "#F1F5F9",
        ) -> Tuple[Dict[str, Any], bytes]:
            return self._call(
                "render_model",
                {
                    "model_id": model_id,
                    "width": width,
                    "height": height,
                    "azimuth": azimuth,
                    "elevation": elevation,
                    "background": background,
                },
            ), state.png

    monkeypatch.setattr(tool_modules.service, "ModelWorkspace", Workspace)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["3D", "三维", "建模", "模型", *TOOL_ARGUMENTS])
async def test_tools_are_deferred_and_discoverable(
    tool_modules: SimpleNamespace, tool_context: Any, query: str
) -> None:
    builtin = tool_modules.builtin
    provider = tool_modules.provider.MaisakaBuiltinToolProvider(builtin.build_builtin_tool_handlers(tool_context))
    specs = {spec.name: spec for spec in await provider.list_tools()}
    assert set(TOOL_ARGUMENTS) <= specs.keys()
    assert not set(TOOL_ARGUMENTS).intersection(spec["name"] for spec in builtin.get_builtin_tools())
    for name in TOOL_ARGUMENTS:
        assert specs[name].metadata == {"builtin_stage": "action", "visibility": "deferred"}
        assert specs[name].parameters_schema["additionalProperties"] is False
        assert "调用示例" in specs[name].description

    # 使用实际 runtime 的搜索、发现方法，不初始化聊天流、调度器或 bot。
    runtime_class = tool_modules.runtime.MaisakaHeartFlowChatting
    runtime = object.__new__(runtime_class)
    runtime.session_id = "trusted-session"
    runtime.discovered_tool_names = set()
    runtime.update_deferred_tool_specs(
        [spec for spec in specs.values() if builtin.get_builtin_tool_visibility(spec) == "deferred"]
    )
    context = tool_modules.context.BuiltinToolRuntimeContext(tool_context.engine, runtime)
    handlers = builtin.build_builtin_tool_handlers(context)
    result = await handlers["tool_search"](
        tool_modules.tooling.ToolInvocation(tool_name="tool_search", arguments={"query": query, "limit": 20})
    )
    expected = {query} if query in TOOL_ARGUMENTS else set(TOOL_ARGUMENTS)
    assert result.success
    assert expected <= set(result.structured_content["matched_tool_names"])
    assert expected <= {spec.name for spec in runtime.get_discovered_deferred_tool_specs()}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", TOOL_ARGUMENTS)
async def test_service_construction_and_method_are_offloaded_with_trusted_session(
    tool_modules: SimpleNamespace, tool_context: Any, workspace_stub: SimpleNamespace, tool_name: str
) -> None:
    main_thread = threading.get_ident()
    invocation = tool_modules.tooling.ToolInvocation(
        tool_name=tool_name,
        arguments=deepcopy(TOOL_ARGUMENTS[tool_name]),
        call_id="call-3d",
        session_id="untrusted-invocation",
        stream_id="untrusted-stream",
        metadata={"session_id": "untrusted-metadata"},
    )
    context = tool_modules.tooling.ToolExecutionContext(session_id="untrusted-context", stream_id="another-stream")
    handlers = tool_modules.builtin.build_builtin_tool_handlers(tool_context)
    result = await handlers[tool_name](invocation, context)

    assert result.success, result.error_message
    assert not result.stop_after_execution
    assert workspace_stub.calls[0][0:2] == ("init", "trusted-session")
    assert workspace_stub.calls[1][0] == tool_name.replace("_3d", "")
    assert all(call[2] != main_thread for call in workspace_stub.calls)
    assert len({call[2] for call in workspace_stub.calls}) == 1
    for key, value in invocation.arguments.items():
        assert workspace_stub.calls[1][1][key] == value
    assert result.structured_content["model_id"] == "new-model"
    assert "测试模型" in result.content
    assert "\\u6d4b" not in result.content


@pytest.mark.asyncio
async def test_import_source_file_and_render_options_reach_service(
    tool_modules: SimpleNamespace, tool_context: Any, workspace_stub: SimpleNamespace
) -> None:
    source_file = "零件/导入.glb"
    result = await tool_modules.tools.handle_tool(
        tool_context,
        tool_modules.tooling.ToolInvocation(tool_name="inspect_3d_model", arguments={"source_file": source_file}),
    )
    assert result.success
    assert workspace_stub.calls[-1][1] == {"model_id": None, "source_file": source_file}
    arguments = {
        "model_id": "test-model",
        "width": 128,
        "height": 1024,
        "azimuth": 90,
        "elevation": -90,
        "background": "#123abc",
    }
    result = await tool_modules.tools.handle_tool(
        tool_context, tool_modules.tooling.ToolInvocation(tool_name="render_3d_model", arguments=arguments)
    )
    assert result.success
    assert workspace_stub.calls[-1][1] == arguments


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", TOOL_ARGUMENTS)
@pytest.mark.parametrize("unknown", ["session_id", "root", "unexpected"])
async def test_unknown_top_level_arguments_fail_before_service(
    tool_modules: SimpleNamespace, tool_context: Any, workspace_stub: SimpleNamespace, tool_name: str, unknown: str
) -> None:
    arguments = {**deepcopy(TOOL_ARGUMENTS[tool_name]), unknown: "untrusted"}
    result = await tool_modules.tools.handle_tool(
        tool_context, tool_modules.tooling.ToolInvocation(tool_name=tool_name, arguments=arguments)
    )
    assert not result.success
    assert unknown in result.error_message
    assert not workspace_stub.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("create_3d_model", {}),
        ("create_3d_model", {"name": "测试"}),
        ("create_3d_model", {"parts": [{"name": "主体", "primitive": "box"}]}),
        ("create_3d_model", {"name": "", "parts": [{}]}),
        ("create_3d_model", {"name": "模" * 65, "parts": [{}]}),
        ("create_3d_model", {"name": "测试", "parts": []}),
        ("create_3d_model", {"name": "测试", "parts": [{}] * 65}),
        ("create_3d_model", {"name": "测试", "parts": {}}),
        ("inspect_3d_model", {}),
        ("inspect_3d_model", {"model_id": "id", "source_file": "file.glb"}),
        ("inspect_3d_model", {"model_id": None}),
        ("inspect_3d_model", {"source_file": " "}),
        ("edit_3d_model", {"model_id": "id"}),
        ("edit_3d_model", {"operations": [{}]}),
        ("edit_3d_model", {"model_id": "id", "operations": []}),
        ("edit_3d_model", {"model_id": "id", "operations": [{}] * 65}),
        ("render_3d_model", {}),
        ("render_3d_model", {"model_id": False}),
        ("render_3d_model", {"model_id": "id", "width": 127}),
        ("render_3d_model", {"model_id": "id", "height": 1025}),
        ("render_3d_model", {"model_id": "id", "width": 640.0}),
        ("render_3d_model", {"model_id": "id", "height": True}),
        ("render_3d_model", {"model_id": "id", "elevation": 91}),
        ("render_3d_model", {"model_id": "id", "azimuth": "45"}),
        ("render_3d_model", {"model_id": "id", "azimuth": float("nan")}),
        ("render_3d_model", {"model_id": "id", "elevation": float("inf")}),
        ("render_3d_model", {"model_id": "id", "background": "red"}),
        ("render_3d_model", {"model_id": "id", "background": None}),
        ("create_3d_model", None),
        ("create_3d_model", []),
    ],
)
async def test_invalid_top_level_arguments_return_failure(
    tool_modules: SimpleNamespace,
    tool_context: Any,
    workspace_stub: SimpleNamespace,
    tool_name: str,
    arguments: Any,
) -> None:
    result = await tool_modules.tools.handle_tool(
        tool_context, tool_modules.tooling.ToolInvocation(tool_name=tool_name, arguments=arguments)
    )
    assert not result.success
    assert "失败" in result.error_message
    assert not workspace_stub.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", TOOL_ARGUMENTS)
@pytest.mark.parametrize(
    "error", [ValueError("几何参数无效"), OSError("模型文件无法读取"), ImportError("缺少三维依赖")]
)
async def test_service_errors_are_explicit_failures(
    tool_modules: SimpleNamespace,
    tool_context: Any,
    workspace_stub: SimpleNamespace,
    tool_name: str,
    error: Exception,
) -> None:
    workspace_stub.method_error = error
    result = await tool_modules.tools.handle_tool(
        tool_context,
        tool_modules.tooling.ToolInvocation(tool_name=tool_name, arguments=deepcopy(TOOL_ARGUMENTS[tool_name])),
    )
    assert not result.success
    assert str(error) in result.error_message
    assert type(error).__name__ in result.error_message
    assert not result.content_items
    assert not result.stop_after_execution


@pytest.mark.asyncio
async def test_unknown_exception_is_logged_and_construction_failure_is_reported(
    tool_modules: SimpleNamespace, tool_context: Any, workspace_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    logger = Mock()
    monkeypatch.setattr(tool_modules.tools, "logger", logger)
    workspace_stub.construction_error = RuntimeError("构造工作区异常")
    result = await tool_modules.tools.handle_tool(
        tool_context, tool_modules.tooling.ToolInvocation(tool_name="inspect_3d_model", arguments={"model_id": "id"})
    )
    assert not result.success
    assert "RuntimeError: 构造工作区异常" in result.error_message
    logger.exception.assert_called_once()
    assert workspace_stub.calls[0][0:2] == ("init", "trusted-session")


@pytest.mark.asyncio
async def test_cancelled_tool_propagates_cancellation(
    tool_modules: SimpleNamespace, tool_context: Any, workspace_stub: SimpleNamespace
) -> None:
    workspace_stub.method_error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await tool_modules.tools.handle_tool(
            tool_context,
            tool_modules.tooling.ToolInvocation(tool_name="inspect_3d_model", arguments={"model_id": "id"}),
        )


@pytest.mark.asyncio
async def test_render_image_is_assembled_into_planner_context_and_reply_attachment(
    tool_modules: SimpleNamespace, tool_context: Any, workspace_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await tool_modules.tools.handle_tool(
        tool_context,
        tool_modules.tooling.ToolInvocation(
            tool_name="render_3d_model", arguments={"model_id": "test-model"}, call_id="render-call"
        ),
    )
    assert result.success
    assert not result.stop_after_execution
    assert len(result.content_items) == 1
    image = result.content_items[0]
    encoded = base64.b64encode(workspace_stub.png).decode("ascii")
    assert image.content_type == "image"
    assert image.mime_type == "image/png"
    assert image.data == encoded
    assert encoded not in result.content
    assert result.structured_content["media_index"] == "tool_result:render-call:1"
    assert "send_image" in result.content and "attach_pic" in result.content
    assert "media_index" not in workspace_stub.model_info

    # 只隔离后台识图，实际执行历史写入、媒体分离和多模态上下文投影。
    recognition = Mock()
    monkeypatch.setattr(tool_context.engine, "_schedule_tool_result_media_image_recognition", recognition)
    call = tool_modules.tool_option.ToolCall(call_id="render-call", func_name="render_3d_model")
    tool_context.engine._append_tool_execution_result(call, result)
    history_result, media_message = tool_context.runtime._chat_history
    assert isinstance(history_result, tool_modules.messages.ToolResultMessage)
    assert "tool_result:render-call:1" in history_result.content
    assert encoded not in history_result.content
    assert isinstance(media_message, tool_modules.messages.SessionBackedMessage)
    assert media_message.message_id == "tool_result:render-call:1"
    assert media_message.source_kind == "tool_result_media"
    image_components = [
        item
        for item in media_message.raw_message.components
        if isinstance(item, tool_modules.components.ImageComponent)
    ]
    assert len(image_components) == 1
    assert image_components[0].binary_data == workspace_stub.png
    recognition.assert_called_once()
    item = media_message.to_context_item(enable_visual_message=True)
    assert isinstance(item, tool_modules.items.UserMessageItem)
    images = [part for part in item.parts if isinstance(part, tool_modules.items.ContextImagePart)]
    assert len(images) == 1
    assert images[0].image_format == "png"
    assert images[0].image_base64 == encoded
    attachment = await tool_context._resolve_image_attachment({"media_index": "tool_result:render-call:1"})
    assert attachment.binary_data == workspace_stub.png


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "visual", "quota", "warning"),
    [("text", True, 4, "视觉"), ("auto", False, 4, "视觉"), ("auto", True, 0, "max_image_num=0")],
)
async def test_render_warns_when_planner_cannot_read_image(
    tool_modules: SimpleNamespace,
    tool_context: Any,
    workspace_stub: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    visual: bool,
    quota: int,
    warning: str,
) -> None:
    monkeypatch.setattr(tool_modules.config.visual, "planner_mode", mode)
    monkeypatch.setattr(tool_modules.config.visual, "max_image_num", quota)
    monkeypatch.setattr(tool_modules.models.models[0], "visual", visual)
    result = await tool_modules.tools.handle_tool(
        tool_context, tool_modules.tooling.ToolInvocation(tool_name="render_3d_model", arguments={"model_id": "id"})
    )
    assert result.success
    assert any(warning in text for text in result.structured_content["warnings"])
    assert "PNG 已生成并附到上下文" in result.content
    assert "无法读取图片" in result.content
    assert "可查看图片后继续编辑" not in result.content
    assert "无法读取图片" in result.structured_content["message"]
    assert result.content_items[0].data
    assert workspace_stub.calls[-1][0] == "render_model"
    assert tool_modules.config.visual.planner_mode == mode
    assert tool_modules.config.visual.max_image_num == quota


@pytest.mark.asyncio
async def test_incompatible_multimodal_configuration_fails_explicitly(
    tool_modules: SimpleNamespace, tool_context: Any, workspace_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool_modules.config.visual, "planner_mode", "multimodal")
    monkeypatch.setattr(tool_modules.models.models[0], "visual", False)
    result = await tool_modules.tools.handle_tool(
        tool_context, tool_modules.tooling.ToolInvocation(tool_name="render_3d_model", arguments={"model_id": "id"})
    )
    assert not result.success
    assert "planner_mode=multimodal" in result.error_message
    assert "visual" in result.error_message
    assert not workspace_stub.calls


@pytest.mark.asyncio
async def test_registered_tools_roundtrip_real_workspace_into_image_context(
    tool_modules: SimpleNamespace,
    tool_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """真实建模服务贯穿注册处理器及图片上下文，编辑副本不改动源资产。"""

    real_workspace = tool_modules.service.ModelWorkspace
    monkeypatch.setattr(
        tool_modules.service, "ModelWorkspace", lambda session_id: real_workspace(session_id, root=tmp_path)
    )
    handlers = tool_modules.builtin.build_builtin_tool_handlers(tool_context)
    invocation_type = tool_modules.tooling.ToolInvocation
    created = await handlers["create_3d_model"](
        invocation_type(
            tool_name="create_3d_model",
            arguments={
                "name": "端到端测试模型",
                "parts": [{"name": "主体", "primitive": "box", "size": [2, 1, 1], "color": "#7C9BC0"}],
            },
        )
    )
    assert created.success, created.error_message
    source_id = created.structured_content["model_id"]
    source_directory = tmp_path / tool_context.runtime.session_id / "models" / source_id
    source_files = {name: (source_directory / name).read_bytes() for name in ("scene.json", "model.glb", "model.stl")}
    inspected = await handlers["inspect_3d_model"](
        invocation_type(tool_name="inspect_3d_model", arguments={"model_id": source_id})
    )
    assert inspected.success, inspected.error_message
    assert inspected.structured_content["bounds"] == [[-1.0, -0.5, -0.5], [1.0, 0.5, 0.5]]
    assert inspected.structured_content["parts"][0]["name"] == "主体"

    edited = await handlers["edit_3d_model"](
        invocation_type(
            tool_name="edit_3d_model",
            arguments={
                "model_id": source_id,
                "operations": [
                    {"op": "transform", "target": "主体", "position": [0, 0, 2]},
                    {"op": "color", "target": "*", "color": "#FF8800"},
                ],
            },
        )
    )
    assert edited.success, edited.error_message
    edited_id = edited.structured_content["model_id"]
    assert edited_id != source_id
    assert edited.structured_content["parent_model_id"] == source_id
    assert edited.structured_content["bounds"] == [[-1.0, -0.5, 1.5], [1.0, 0.5, 2.5]]
    source_after = await handlers["inspect_3d_model"](
        invocation_type(tool_name="inspect_3d_model", arguments={"model_id": source_id})
    )
    assert source_after.success, source_after.error_message
    assert source_after.structured_content == inspected.structured_content
    assert {name: (source_directory / name).read_bytes() for name in source_files} == source_files

    rendered = await handlers["render_3d_model"](
        invocation_type(
            tool_name="render_3d_model",
            arguments={"model_id": edited_id, "width": 128, "height": 128},
            call_id="roundtrip-render",
        )
    )
    assert rendered.success, rendered.error_message
    assert not rendered.stop_after_execution
    assert rendered.structured_content["model_id"] == edited_id
    assert len(rendered.content_items) == 1
    png = base64.b64decode(rendered.content_items[0].data, validate=True)
    assert Path(rendered.structured_content["render_file"]).read_bytes() == png
    with Image.open(BytesIO(png)) as preview:
        assert preview.format == "PNG"
        assert preview.size == (128, 128)
    assert rendered.content_items[0].data not in rendered.content

    recognition = Mock()
    monkeypatch.setattr(tool_context.engine, "_schedule_tool_result_media_image_recognition", recognition)
    call = tool_modules.tool_option.ToolCall(call_id="roundtrip-render", func_name="render_3d_model")
    tool_context.engine._append_tool_execution_result(call, rendered)
    history_result, media_message = tool_context.runtime._chat_history
    assert isinstance(history_result, tool_modules.messages.ToolResultMessage)
    assert "tool_result:roundtrip-render:1" in history_result.content
    assert isinstance(media_message, tool_modules.messages.SessionBackedMessage)
    assert media_message.message_id == "tool_result:roundtrip-render:1"
    assert media_message.source_kind == "tool_result_media"
    recognition.assert_called_once_with(media_message.raw_message, "tool_result:roundtrip-render:1")
    item = media_message.to_context_item(enable_visual_message=True)
    assert isinstance(item, tool_modules.items.UserMessageItem)
    images = [part for part in item.parts if isinstance(part, tool_modules.items.ContextImagePart)]
    assert len(images) == 1
    assert images[0].image_format == "png"
    assert base64.b64decode(images[0].image_base64, validate=True) == png
