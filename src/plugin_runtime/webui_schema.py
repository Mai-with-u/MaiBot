"""插件 WebUI 协议：共享 API 边界，分别定义简单组件页面与隔离 HTML 页面。"""

from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

import hashlib
import json
import math

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")]
Text = Annotated[str, Field(max_length=4000)]
Label = Annotated[str, Field(min_length=1, max_length=80)]
Scalar = Union[str, int, float, bool, None]
MAX_DECLARATION_BYTES = 131072
MAX_CUSTOM_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS_BYTES = 8 * 1024 * 1024
MAX_REGISTRATION_BYTES = 524288
MAX_ARGUMENT_BYTES = 65536
WEBUI_CAPABILITIES = ["declarative_basic", "custom_pages", "image_upload", "structured_arguments"]


def validate_json(value: Any, *, max_bytes: int = MAX_ARGUMENT_BYTES, max_depth: int = 12) -> None:
    """限制 JSON 的深度、元素数及 UTF-8 总量，拒绝非 JSON 值而不隐式转换。"""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > max_depth or count > 20000:
            raise ValueError(f"JSON 超过 {max_depth} 层或 20000 个元素")
        if isinstance(item, (dict, list)) and count + len(pending) + len(item) > 20000:
            raise ValueError("JSON 超过 20000 个元素")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON 对象的键必须为字符串")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("仅接受 JSON 数据")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > max_bytes:
        raise ValueError(f"JSON 超过 {max_bytes // 1024} KiB")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class DataReference(StrictModel):
    scope: Literal["query", "selection"] = "query"
    source: Identifier
    field: str = Field(default="", pattern=r"^(?:[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*)?$", max_length=200)


class Parameter(StrictModel):
    type: Literal["string", "integer", "number", "boolean", "array", "object"]
    required: bool = False
    max_length: int = Field(default=4000, ge=1, le=65536)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: List[Scalar] = Field(default_factory=list, max_length=100)
    items: Optional["Parameter"] = None
    properties: Dict[Identifier, "Parameter"] = Field(default_factory=dict, max_length=30)
    max_items: int = Field(default=1000, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_constraints(self) -> "Parameter":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("参数 minimum 不能大于 maximum")
        if (self.type == "array") != (self.items is not None):
            raise ValueError("array 参数必须声明 items；其它类型不能声明 items")
        if self.type != "object" and self.properties:
            raise ValueError("仅 object 参数可以声明 properties")
        if self.type in {"array", "object"} and self.choices:
            raise ValueError("结构化参数不能使用标量 choices")
        if any(not self.accepts(value) for value in self.choices):
            raise ValueError("参数 choices 必须符合声明类型和范围")
        return self

    def accepts(self, value: Any) -> bool:
        """严格检查嵌套参数；对象只接受已声明属性，数字不接受布尔值。"""
        if self.type == "array":
            return (
                isinstance(value, list)
                and len(value) <= self.max_items
                and self.items is not None
                and all(self.items.accepts(item) for item in value)
            )
        if self.type == "object":
            return (
                isinstance(value, dict)
                and not value.keys() - self.properties.keys()
                and all(
                    (name in value and parameter.accepts(value[name])) or (name not in value and not parameter.required)
                    for name, parameter in self.properties.items()
                )
            )
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

    def validate_args(self, args: Dict[str, Any]) -> None:
        validate_json(args)
        for name in args:
            if name not in self.parameters:
                raise ValueError(f"未声明的参数: {name}")
        for name, parameter in self.parameters.items():
            if name not in args:
                if parameter.required:
                    raise ValueError(f"缺少参数: {name}")
            elif not parameter.accepts(args[name]):
                raise ValueError(f"参数不符合声明: {name}")


class DeclarativeBinding(APIBinding):
    arguments: Dict[Identifier, DataReference] = Field(default_factory=dict, max_length=30)

    @model_validator(mode="after")
    def validate_bindings(self) -> "DeclarativeBinding":
        if self.arguments.keys() - self.parameters.keys():
            raise ValueError("arguments 必须对应已声明的参数")
        if any(parameter.type in {"array", "object"} for parameter in self.parameters.values()):
            raise ValueError("预置表单仅接受标量参数；结构化交互请使用 custom 页面")
        return self


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


class Node(StrictModel):
    label: Optional[Label] = None
    when: Optional[VisibilityCondition] = None


class StackNode(Node):
    type: Literal["stack", "card"]
    children: List["WebUINode"] = Field(default_factory=list, max_length=50)


class GridNode(StackNode):
    type: Literal["grid"]
    columns: int = Field(ge=1, le=4)


class TabsNode(StackNode):
    type: Literal["tabs"]

    @model_validator(mode="after")
    def validate_tabs(self) -> "TabsNode":
        if not self.children or any(child.label is None for child in self.children):
            raise ValueError("tabs 子节点必须有 label")
        return self


class CollapsibleNode(StackNode):
    type: Literal["collapsible"]
    label: Label
    default_open: bool = False


class DialogNode(StackNode):
    type: Literal["dialog"]
    name: Identifier
    label: Label


class TextNode(Node):
    type: Literal["text"]
    value: Union[Text, int, float, bool, DataReference, None] = None
    format: Literal["plain", "log"] = "plain"


class StatNode(Node):
    type: Literal["stat"]
    value: Union[Text, int, float, bool, DataReference, None] = None


class ProgressNode(Node):
    type: Literal["progress"]
    value: Union[Annotated[float, Field(ge=0, le=100)], DataReference]


class ImageNode(Node):
    type: Literal["image"]
    value: DataReference


class TableNode(Node):
    type: Literal["table"]
    value: DataReference
    columns: List[Column] = Field(min_length=1, max_length=20)
    selection: Optional[Identifier] = None
    detail: Optional[Identifier] = None

    @model_validator(mode="after")
    def validate_detail(self) -> "TableNode":
        if self.detail is not None and self.selection is None:
            raise ValueError("table.detail 必须同时声明 selection")
        return self


class ChartNode(Node):
    type: Literal["chart"]
    value: DataReference
    chart_type: Literal["line", "bar"] = "line"
    x: Identifier
    y: Identifier


class PaginationNode(Node):
    type: Literal["pagination"]
    value: DataReference
    name: Identifier


class InteractiveNode(Node):
    disabled_when: Optional[VisibilityCondition] = None
    disabled_reason: Optional[Label] = None


class InputNode(InteractiveNode):
    type: Literal["input"]
    name: Identifier
    value: Union[Text, int, float, None] = None


class DateNode(InteractiveNode):
    type: Literal["date"]
    name: Identifier
    value: Optional[Text] = None


class SwitchNode(InteractiveNode):
    type: Literal["switch"]
    name: Identifier
    value: bool = False


class SelectNode(InteractiveNode):
    type: Literal["select"]
    name: Identifier
    value: Optional[str] = None
    options: List[Option] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_option(self) -> "SelectNode":
        if self.value is not None and self.value not in [option.value for option in self.options]:
            raise ValueError("select 默认值必须属于 options")
        return self


class ButtonNode(InteractiveNode):
    type: Literal["button"]
    label: Label
    action: Identifier
    variant: Literal["primary", "danger", "muted"] = "primary"


WebUINode = Annotated[
    Union[
        StackNode,
        GridNode,
        TabsNode,
        CollapsibleNode,
        DialogNode,
        TextNode,
        StatNode,
        ProgressNode,
        ImageNode,
        TableNode,
        ChartNode,
        PaginationNode,
        InputNode,
        DateNode,
        SwitchNode,
        SelectNode,
        ButtonNode,
    ],
    Field(discriminator="type"),
]
for _container in (StackNode, GridNode, TabsNode, CollapsibleNode, DialogNode):
    _container.model_rebuild()


class Page(StrictModel):
    id: Identifier
    title: Label
    description: Text = ""
    placement: Literal["sidebar", "workspace"] = "sidebar"
    icon: Literal["puzzle", "chart", "settings", "database", "list"] = "puzzle"


class DeclarativePage(Page):
    mode: Literal["declarative"] = "declarative"
    queries: Dict[Identifier, DeclarativeBinding] = Field(default_factory=dict, max_length=10)
    actions: Dict[Identifier, DeclarativeBinding] = Field(default_factory=dict, max_length=20)
    content: List[WebUINode] = Field(min_length=1, max_length=50)
    auto_refresh: bool = False
    poll_interval_seconds: int = Field(default=0, ge=0, le=60)

    @model_validator(mode="after")
    def validate_references(self) -> "DeclarativePage":
        if self.poll_interval_seconds in {1, 2}:
            raise ValueError("轮询间隔至少3秒；0表示关闭")
        inputs, selections, dialogs = set(), set(), set()
        pending = [(node, 1) for node in self.content]
        nodes = []
        while pending:
            node, depth = pending.pop()
            nodes.append(node)
            if depth > 8 or len(nodes) > 200:
                raise ValueError("页面超过 8 层或 200 个组件")
            if isinstance(node, StackNode):
                pending.extend((child, depth + 1) for child in node.children)
            if isinstance(node, DialogNode):
                if depth != 1 or node.name in dialogs:
                    raise ValueError("dialog 必须位于 content 顶层且名称唯一")
                dialogs.add(node.name)
            if isinstance(node, TableNode) and node.selection is not None:
                if node.selection in selections:
                    raise ValueError("行选择名称重复")
                selections.add(node.selection)
            if isinstance(node, (InputNode, DateNode, SelectNode, SwitchNode, PaginationNode)):
                if node.name in inputs:
                    raise ValueError(f"重复输入字段: {node.name}")
                inputs.add(node.name)
            if isinstance(node, ButtonNode):
                if node.action not in self.actions:
                    raise ValueError(f"未声明的操作: {node.action}")
                if node.variant == "danger" and self.actions[node.action].confirmation is None:
                    raise ValueError("danger 操作必须声明 confirmation")

        def validate_reference(reference: DataReference) -> None:
            sources = self.queries if reference.scope == "query" else selections
            if reference.source not in sources:
                raise ValueError(f"未声明的数据上下文: {reference.scope}.{reference.source}")

        for binding in self.queries.values():
            if binding.arguments or binding.confirmation:
                raise ValueError("queries 只接受表单参数，不声明 arguments 或 confirmation")
        for binding in self.actions.values():
            for reference in binding.arguments.values():
                validate_reference(reference)
        for node in nodes:
            if isinstance(node, (TextNode, StatNode, ProgressNode, ImageNode, TableNode, ChartNode, PaginationNode)):
                if isinstance(node.value, DataReference):
                    validate_reference(node.value)
            if node.when is not None:
                validate_reference(node.when.reference)
            if isinstance(node, InteractiveNode) and node.disabled_when is not None:
                validate_reference(node.disabled_when.reference)
            if isinstance(node, TableNode) and node.detail is not None and node.detail not in dialogs:
                raise ValueError(f"未声明的详情弹窗: {node.detail}")
        return self


class ImageUpload(StrictModel):
    action: Identifier
    image_max_edge: Optional[int] = Field(default=None, ge=1, le=8192)


class CustomPage(Page):
    mode: Literal["custom"]
    entry: str = Field(min_length=1, max_length=200)
    layout: Literal["document", "viewport"] = "document"
    queries: Dict[Identifier, APIBinding] = Field(default_factory=dict, max_length=10)
    actions: Dict[Identifier, APIBinding] = Field(default_factory=dict, max_length=20)
    uploads: Dict[Identifier, ImageUpload] = Field(default_factory=dict, max_length=10)
    # Runner 在线程读取产物；Host 保存已校验快照，不开放插件文件目录。
    document: str = Field(min_length=1, max_length=MAX_CUSTOM_DOCUMENT_BYTES)

    @property
    def document_revision(self) -> str:
        return hashlib.sha256(self.document.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def validate_page(self) -> "CustomPage":
        if not self.document.strip() or len(self.document.encode("utf-8")) > MAX_CUSTOM_DOCUMENT_BYTES:
            raise ValueError("自定义页面必须非空且不超过 2 MiB")
        if any(binding.confirmation for binding in self.queries.values()):
            raise ValueError("queries 不声明 confirmation")
        for upload in self.uploads.values():
            binding = self.actions.get(upload.action)
            parameter = binding.parameters.get("upload_id") if binding is not None else None
            if parameter is None or parameter.type != "string" or not parameter.required:
                raise ValueError("图片上传必须绑定接受必填字符串 upload_id 的 action")
        return self


WebUIPage = Annotated[Union[DeclarativePage, CustomPage], Field(discriminator="mode")]


class WebUIExtension(StrictModel):
    schema_version: Literal[2] = 2
    required_capabilities: List[
        Literal["declarative_basic", "custom_pages", "image_upload", "structured_arguments"]
    ] = Field(
        default_factory=list,
        max_length=4,
    )
    workspace_title: Optional[Label] = None
    pages: List[WebUIPage] = Field(min_length=1, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def validate_size(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("schema_version", 2) != 2:
                raise ValueError("WebUI 协议已升级为 2；请按 docs/plugin-webui.md 迁移声明")
            pages = value.get("pages")
            if isinstance(pages, list):
                # HTML 单独计量，不与组件/API 元数据争用注册配额。
                metadata = {
                    **value,
                    "pages": [
                        {key: item for key, item in page.items() if key != "document"}
                        if isinstance(page, dict)
                        else page
                        for page in pages
                    ],
                }
                validate_json(metadata, max_bytes=MAX_REGISTRATION_BYTES, max_depth=32)
                # 缺省 mode 仍表示简单组件页；不转换任何已移除的组件或字段。
                value = {
                    **value,
                    "pages": [{"mode": "declarative", **page} if isinstance(page, dict) else page for page in pages],
                }
        return value

    @model_validator(mode="after")
    def validate_pages(self) -> "WebUIExtension":
        if len({page.id for page in self.pages}) != len(self.pages):
            raise ValueError("页面 ID 重复")
        if any(page.placement == "workspace" for page in self.pages) and self.workspace_title is None:
            raise ValueError("顶部工作区必须声明 workspace_title")
        total = 0
        for page in self.pages:
            bindings = [*page.queries.values(), *page.actions.values()]
            if (
                any(
                    parameter.type in {"array", "object"}
                    for binding in bindings
                    for parameter in binding.parameters.values()
                )
                and "structured_arguments" not in self.required_capabilities
            ):
                raise ValueError("结构化参数需要声明 required_capabilities: structured_arguments")
            if isinstance(page, CustomPage):
                if "custom_pages" not in self.required_capabilities:
                    raise ValueError("custom 页面需要声明 required_capabilities: custom_pages")
                if page.uploads and "image_upload" not in self.required_capabilities:
                    raise ValueError("图片上传需要声明 required_capabilities: image_upload")
                total += len(page.document.encode("utf-8"))
                if total > MAX_DOCUMENTS_BYTES:
                    raise ValueError("自定义页面产物总量超过 8 MiB")
        return self


class WebUIDocument(StrictModel):
    id: Identifier
    entry: str = Field(min_length=1, max_length=200)
    document: str = Field(min_length=1, max_length=MAX_CUSTOM_DOCUMENT_BYTES)


class DocumentReload(StrictModel):
    documents: List[WebUIDocument] = Field(max_length=20)


def replace_webui_documents(extension: WebUIExtension, payload: Dict[str, Any]) -> WebUIExtension:
    """同样校验首次注册和热重载；失败时调用者保留旧快照并明确报错。"""
    response = DocumentReload.model_validate(payload)
    documents = {(item.id, item.entry): item.document for item in response.documents}
    expected = {(page.id, page.entry) for page in extension.pages if isinstance(page, CustomPage)}
    if len(documents) != len(response.documents) or documents.keys() != expected:
        raise ValueError("页面声明已改变或重复，请重载插件")
    declaration = extension.model_dump(mode="json")
    for page in declaration["pages"]:
        if page["mode"] == "custom":
            page["document"] = documents[(page["id"], page["entry"])]
    return WebUIExtension.model_validate(declaration)


def load_webui_extension(plugin_dir: str) -> Optional[WebUIExtension]:
    """在 Runner 工作线程读取声明；无文件的插件不注册 WebUI 扩展。"""
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
    declaration = json.loads(raw)
    if isinstance(declaration, dict) and isinstance(declaration.get("pages"), list):
        if len(declaration["pages"]) > 20:
            raise ValueError("每插件最多 20 个页面")
        document_bytes = 0
        for page in declaration["pages"]:
            if not isinstance(page, dict) or page.get("mode") != "custom":
                continue
            entry = page.get("entry")
            if not isinstance(entry, str) or not entry or len(entry) > 200 or "document" in page:
                raise ValueError("custom 页面必须通过 entry 读取 HTML，不能内联 document")
            relative = Path(entry)
            if (
                relative.is_absolute()
                or relative.anchor
                or ".." in relative.parts
                or relative.suffix.lower() != ".html"
            ):
                raise ValueError("entry 必须是插件内的相对 HTML 路径")
            target = root / relative
            # 检查插件内路径的所有部分，拒绝符号链接和 Windows junction。
            if any(part.is_symlink() or part.is_junction() for part in [target, *target.parents] if part != root):
                raise ValueError("entry 路径不能包含链接")
            if not target.resolve().is_relative_to(root):
                raise ValueError("entry 超出插件目录")
            if not target.is_file():
                raise ValueError("entry 必须是普通 HTML 文件")
            with target.open("rb") as stream:
                document = stream.read(MAX_CUSTOM_DOCUMENT_BYTES + 1)
            if len(document) > MAX_CUSTOM_DOCUMENT_BYTES:
                raise ValueError("自定义页面超过 2 MiB")
            document_bytes += len(document)
            if document_bytes > MAX_DOCUMENTS_BYTES:
                raise ValueError("自定义页面总量超过 8 MiB")
            page["document"] = document.decode("utf-8")
    return WebUIExtension.model_validate(declaration)
