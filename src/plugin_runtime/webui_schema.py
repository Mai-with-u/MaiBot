"""插件 WebUI 声明协议：只接受宿主支持的组件和所属插件的 API 绑定。"""

from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

import json
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")]
Text = Annotated[str, Field(max_length=4000)]
Label = Annotated[str, Field(min_length=1, max_length=80)]
Scalar = Union[str, int, float, bool, None]
MAX_DECLARATION_BYTES = 131072


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class DataReference(StrictModel):
    scope: Literal["query", "selection", "item"] = "query"
    source: Identifier
    field: str = Field(default="", pattern=r"^(?:[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*)?$", max_length=200)


class Parameter(StrictModel):
    type: Literal["string", "integer", "number", "boolean"]
    required: bool = False
    max_length: int = Field(default=4000, ge=1, le=65536)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: List[Scalar] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_constraints(self) -> "Parameter":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("参数 minimum 不能大于 maximum")
        if any(not self.accepts(value) for value in self.choices):
            raise ValueError("参数 choices 必须符合声明类型和范围")
        return self

    def accepts(self, value: Any) -> bool:
        """严格校验参数，不把字符串、布尔值隐式转换成数字。"""
        expected = {
            "string": isinstance(value, str),
            "integer": type(value) is int,
            "number": type(value) in (int, float),
            "boolean": type(value) is bool,
        }[self.type]
        if not expected or (self.choices and value not in self.choices):
            return False
        if isinstance(value, str):
            return len(value) <= self.max_length
        if type(value) in (int, float):
            return (
                math.isfinite(value)
                and (self.minimum is None or value >= self.minimum)
                and (self.maximum is None or value <= self.maximum)
            )
        return True


class APIBinding(StrictModel):
    api: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
    version: str = Field(default="1", min_length=1, max_length=40)
    parameters: Dict[Identifier, Parameter] = Field(default_factory=dict, max_length=30)
    confirmation: Optional[Label] = None
    arguments: Dict[Identifier, DataReference] = Field(default_factory=dict, max_length=30)
    clear_selection: Optional[Identifier] = None

    @model_validator(mode="after")
    def validate_bindings(self) -> "APIBinding":
        if self.arguments.keys() - self.parameters.keys():
            raise ValueError("arguments 必须对应已声明的参数")
        return self

    def validate_args(self, args: Dict[str, Any]) -> None:
        for name in args:
            if name not in self.parameters:
                raise ValueError(f"未声明的参数: {name}")
        for name, parameter in self.parameters.items():
            if name not in args:
                if parameter.required:
                    raise ValueError(f"缺少参数: {name}")
            elif not parameter.accepts(args[name]):
                raise ValueError(f"参数不符合声明: {name}")


class Column(StrictModel):
    field: Identifier
    label: Label


class Option(StrictModel):
    label: Label
    value: str = Field(min_length=1, max_length=200)


class VisibilityCondition(StrictModel):
    reference: DataReference
    operator: Literal["truthy", "empty", "not_empty", "equals"] = "truthy"
    expected: Scalar = None

    @model_validator(mode="after")
    def validate_expected(self) -> "VisibilityCondition":
        if self.operator != "equals" and self.expected is not None:
            raise ValueError("只有 equals 条件可以声明 expected")
        return self


class WebUINode(StrictModel):
    type: Literal[
        "stack", "grid", "card", "tabs", "text", "stat", "table", "chart", "gallery", "image", "pagination", "input", "select", "choice", "multi_select", "checkbox", "switch", "date", "button", "dialog", "collapsible", "repeat", "upload"
    ]
    label: Optional[Label] = None
    value: Union[Text, int, float, bool, DataReference, None] = None
    children: List["WebUINode"] = Field(default_factory=list, max_length=50)
    columns: Union[int, List[Column], None] = None
    name: Optional[Identifier] = None
    options: List[Option] = Field(default_factory=list, max_length=100)
    action: Optional[Identifier] = None
    variant: Literal["primary", "danger", "muted"] = "primary"
    chart_type: Literal["line", "bar"] = "line"
    x: Optional[Identifier] = None
    y: Optional[Identifier] = None
    when: Optional[VisibilityCondition] = None
    selection: Optional[Identifier] = None
    detail: Optional[Identifier] = None
    max_items: int = Field(default=50, ge=1, le=100)
    default_open: bool = False
    image_max_edge: Optional[int] = Field(default=None, ge=1, le=8192)
    compact: bool = False

    @model_validator(mode="after")
    def validate_component(self) -> "WebUINode":
        common = {"type", "label", "when"}
        allowed = {
            "stack": {"children"},
            "grid": {"children", "columns"},
            "card": {"children", "compact"},
            "tabs": {"children"},
            "text": {"value"},
            "stat": {"value"},
            "table": {"value", "columns", "selection", "detail"},
            "dialog": {"name", "children"},
            "collapsible": {"children", "default_open"},
            "repeat": {"value", "name", "children", "max_items"},
            "gallery": {"value", "columns"},
            "image": {"value"},
            "pagination": {"value", "name"},
            "chart": {"value", "chart_type", "x", "y"},
            "input": {"name", "value"},
            "select": {"name", "value", "options"},
            "choice": {"name", "value", "options"},
            "multi_select": {"selection", "value"},
            "checkbox": {"selection", "value"},
            "switch": {"name", "value"},
            "date": {"name", "value"},
            "button": {"action", "variant"},
            "upload": {"action", "image_max_edge"},
        }[self.type]
        # RPC 的 model_dump 会带上默认字段；只允许非适用字段保持协议默认值。
        for name in self.model_fields_set - common - allowed:
            field = type(self).model_fields[name]
            default = field.default_factory() if field.default_factory is not None else field.default
            if self.__dict__[name] != default:
                raise ValueError(f"{self.type} 包含不支持的属性: {name}")
        if self.type == "grid" and (type(self.columns) is not int or not 1 <= self.columns <= 4):
            raise ValueError("grid/gallery.columns 必须为 1 到 4")
        if self.type == "gallery" and self.columns is not None and (type(self.columns) is not int or not 1 <= self.columns <= 4):
            raise ValueError("gallery.columns 必须为空（自动布局）或 1 到 4")
        if self.type == "table" and (not isinstance(self.columns, list) or not 1 <= len(self.columns) <= 20):
            raise ValueError("table 必须声明 1 到 20 列")
        if self.type in {"table", "chart", "gallery", "pagination", "repeat"} and not isinstance(self.value, DataReference):
            raise ValueError("表格、图表和图库必须绑定查询结果")
        if self.type == "pagination" and self.name is None:
            raise ValueError("pagination 必须声明页码参数 name")
        if self.type in {"dialog", "repeat"} and self.name is None:
            raise ValueError("dialog/repeat 必须声明 name")
        if self.type in {"dialog", "collapsible"} and self.label is None:
            raise ValueError("详情和折叠容器必须声明 label")
        if self.detail is not None and self.selection is None:
            raise ValueError("table.detail 必须同时声明 selection")
        if self.type == "chart" and (self.x is None or self.y is None):
            raise ValueError("chart 必须声明 x 和 y 字段")
        if self.type in {"multi_select", "checkbox"} and (self.selection is None or not isinstance(self.value, DataReference)):
            raise ValueError("多选组件必须声明 selection 并绑定数据")
        if self.type in {"input", "select", "choice", "switch", "date"}:
            if self.name is None or isinstance(self.value, DataReference):
                raise ValueError("输入组件必须声明 name，默认值必须为标量")
            if self.type == "switch" and type(self.value) is not bool:
                raise ValueError("switch 默认值必须为布尔值")
            if self.type in {"date", "select", "choice"} and self.value is not None and not isinstance(self.value, str):
                raise ValueError("日期和选择组件默认值必须为字符串")
            if self.type in {"select", "choice"} and (
                not self.options or self.value not in [None, *[o.value for o in self.options]]
            ):
                raise ValueError("select 默认值必须属于 options")
        if self.type in {"button", "upload"} and (self.action is None or self.label is None):
            raise ValueError("button 必须声明 action 和 label")
        if self.type == "tabs" and (not self.children or any(child.label is None for child in self.children)):
            raise ValueError("tabs 子节点必须有 label")
        return self


class WebUIPage(StrictModel):
    id: Identifier
    title: Label
    description: Text = ""
    placement: Literal["sidebar", "workspace"] = "sidebar"
    icon: Literal["puzzle", "chart", "settings", "database", "list"] = "puzzle"
    auto_refresh: bool = False
    poll_interval_seconds: int = Field(default=0, ge=0, le=60)
    queries: Dict[Identifier, APIBinding] = Field(default_factory=dict, max_length=10)
    actions: Dict[Identifier, APIBinding] = Field(default_factory=dict, max_length=20)
    content: List[WebUINode] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_references(self) -> "WebUIPage":
        if self.poll_interval_seconds in {1, 2}:
            raise ValueError("轮询间隔至少3秒；0表示关闭")
        inputs = set()
        selections = set()
        dialogs = set()
        repeat_names = set()
        pending = [(node, 1, frozenset()) for node in self.content]
        nodes = []
        count = 0
        while pending:
            node, depth, items = pending.pop()
            nodes.append((node, items))
            count += 1
            if depth > 8 or count > 200:
                raise ValueError("页面超过 8 层或 200 个组件")
            if items and (node.name is not None and node.type != "repeat" or node.selection is not None and node.type != "checkbox"):
                raise ValueError("repeat 内不能声明输入、弹窗或行选择，以免产生重复状态")
            if node.selection is not None and node.type != "checkbox":
                if node.selection in selections:
                    raise ValueError("行选择名称重复")
                selections.add(node.selection)
            if node.type == "dialog":
                if depth != 1:
                    raise ValueError("dialog 必须声明在 content 顶层，避免被折叠或非活动标签页隐藏")
                if node.name in dialogs:
                    raise ValueError("弹窗名称重复")
                dialogs.add(node.name)
            if node.action is not None and node.action not in self.actions:
                raise ValueError(f"未声明的操作: {node.action}")
            if node.type == "upload":
                binding = self.actions[node.action]
                parameter = binding.parameters.get("upload_id")
                if parameter is None or parameter.type != "string" or not parameter.required or binding.confirmation:
                    raise ValueError("upload 操作必须接受必填字符串 upload_id，且不能声明 confirmation")
            if node.type == "button" and node.variant == "danger" and node.action is not None:
                if self.actions[node.action].confirmation is None:
                    raise ValueError("danger 操作必须声明 confirmation")
            if node.name is not None and node.type not in {"dialog", "repeat"}:
                if node.name in inputs:
                    raise ValueError(f"重复输入字段: {node.name}")
                inputs.add(node.name)
            child_items = items
            if node.type == "repeat":
                if node.name in items:
                    raise ValueError("repeat 不能遮蔽外层循环名称")
                child_items = items | {node.name}
                repeat_names.add(node.name)
            pending.extend((child, depth + 1, child_items) for child in node.children)

        def validate_reference(reference: DataReference, items: frozenset) -> None:
            sources = {"query": self.queries, "selection": selections, "item": items}[reference.scope]
            if reference.source not in sources:
                if reference.scope == "query":
                    raise ValueError(f"未声明的查询: {reference.source}")
                raise ValueError(f"未声明的数据上下文: {reference.scope}.{reference.source}")

        for binding in self.queries.values():
            if binding.arguments:
                raise ValueError("动态 arguments 仅适用于 actions；queries 参数来自表单")
        for binding in self.actions.values():
            if binding.clear_selection is not None and binding.clear_selection not in selections:
                raise ValueError("clear_selection 必须引用已声明的选择组")
            for reference in binding.arguments.values():
                validate_reference(reference, frozenset(repeat_names))
        for node, items in nodes:
            if node.type == "checkbox" and node.selection not in selections:
                raise ValueError("checkbox 必须引用已声明的多选组")
            if isinstance(node.value, DataReference):
                validate_reference(node.value, items)
            if node.when is not None:
                validate_reference(node.when.reference, items)
            if node.detail is not None and node.detail not in dialogs:
                raise ValueError(f"未声明的详情弹窗: {node.detail}")
            if node.action is not None:
                for reference in self.actions[node.action].arguments.values():
                    validate_reference(reference, items)
        return self


class WebUIExtension(StrictModel):
    schema_version: Literal[1] = 1
    required_capabilities: List[Literal["file_upload_v1"]] = Field(default_factory=list, max_length=1)
    workspace_title: Optional[Label] = None
    pages: List[WebUIPage] = Field(min_length=1, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def validate_size(cls, value: Any) -> Any:
        if isinstance(value, dict):
            # Host 同样校验 Runner 上报的总量，不能依赖插件目录读取时的限制。
            if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 524288:
                raise ValueError("WebUI 注册声明超过 512 KiB")
        return value

    @model_validator(mode="after")
    def validate_pages(self) -> "WebUIExtension":
        pending = [node for page in self.pages for node in page.content]
        while pending:
            node = pending.pop()
            if node.type == "upload" and "file_upload_v1" not in self.required_capabilities:
                raise ValueError("upload 需要声明 required_capabilities: file_upload_v1")
            pending.extend(node.children)
        if len({page.id for page in self.pages}) != len(self.pages):
            raise ValueError("页面 ID 重复")
        if any(page.placement == "workspace" for page in self.pages) and self.workspace_title is None:
            raise ValueError("顶部工作区必须声明 workspace_title")
        return self


def load_webui_extension(plugin_dir: str) -> Optional[WebUIExtension]:
    """在 Runner 的工作线程读取声明；无文件的插件不注册 WebUI 扩展。"""
    root = Path(plugin_dir).resolve()
    path = root / "webui.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("webui.json 必须位于插件目录内，且不能是符号链接")
    with path.open("rb") as stream:
        raw = stream.read(MAX_DECLARATION_BYTES + 1)
    if len(raw) > MAX_DECLARATION_BYTES:
        raise ValueError("webui.json 超过 128 KiB")
    return WebUIExtension.model_validate(json.loads(raw))
