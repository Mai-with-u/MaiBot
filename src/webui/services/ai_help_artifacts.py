"""答疑 Agent 的结构化成果发布。"""

from typing import Any, Dict, List, Literal, Tuple
from urllib.parse import parse_qsl, urlsplit
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import re

from src.llm_models.payload_content.tool_option import ToolDefinitionInput


PUBLISH_ARTIFACT_TOOL = "publish_help_artifact"
_MAX_ARTIFACTS = 4
_MAX_SVG_CHARS = 32_000
_MAX_MARKDOWN_CHARS = 16_000
_MAX_IMAGE_URL_CHARS = 2_048
_MAX_SVG_ELEMENTS = 180
_MAX_SVG_DEPTH = 12
_SVG_NS = "http://www.w3.org/2000/svg"
_REASONING_IMAGE_PATH = "/api/webui/reasoning-process/image"

_SVG_GEOMETRY = {
    "svg": {"viewBox", "width", "height"},
    "g": set(),
    "rect": {"x", "y", "width", "height", "rx", "ry"},
    "circle": {"cx", "cy", "r"},
    "ellipse": {"cx", "cy", "rx", "ry"},
    "line": {"x1", "y1", "x2", "y2"},
    "path": {"d"},
    "polygon": {"points"},
    "polyline": {"points"},
    "text": {"x", "y", "dx", "dy"},
    "tspan": {"x", "y", "dx", "dy"},
    "title": set(),
    "desc": set(),
}
_NUMBER = r"[+-]?(?:[0-9]{1,5}(?:\.[0-9]{0,6})?|\.[0-9]{1,6})"
_NUMBER_LIST = rf"{_NUMBER}(?:[\s,]+{_NUMBER})*"
_LENGTH = re.compile(rf"{_NUMBER}(?:px|%)?")
_COLOR = r"(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,24}|rgba?\([0-9.,%\s]+\)|hsla?\([0-9.,%\s]+\))"
_TRANSFORM = rf"(?:matrix|translate|scale|rotate|skewX|skewY)\(\s*{_NUMBER_LIST}\s*\)"
_SVG_VALUES = {
    "viewBox": re.compile(rf"{_NUMBER}(?:[\s,]+{_NUMBER}){{3}}"),
    "d": re.compile(r"[MmZzLlHhVvCcSsQqTtAa0-9,+.\s-]{1,6000}"),
    "points": re.compile(_NUMBER_LIST),
    "fill": re.compile(_COLOR),
    "stroke": re.compile(_COLOR),
    "opacity": re.compile(_NUMBER),
    "fill-opacity": re.compile(_NUMBER),
    "stroke-opacity": re.compile(_NUMBER),
    "stroke-width": _LENGTH,
    "stroke-dasharray": re.compile(rf"(?:none|{_NUMBER_LIST})"),
    "stroke-linecap": re.compile(r"butt|round|square"),
    "stroke-linejoin": re.compile(r"miter|round|bevel"),
    "font-size": _LENGTH,
    "font-weight": re.compile(r"normal|bold|[1-9]00"),
    "font-family": re.compile(r"sans-serif|serif|monospace|system-ui"),
    "text-anchor": re.compile(r"start|middle|end"),
    "dominant-baseline": re.compile(r"auto|middle|central|hanging|alphabetic"),
    "transform": re.compile(rf"{_TRANSFORM}(?:\s+{_TRANSFORM})*"),
}
_SVG_PRESENTATION = set(_SVG_VALUES) - {"viewBox", "d", "points"}
_SVG_TEXT_TAGS = {"text", "tspan", "title", "desc"}
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_PUBLIC_HOST = re.compile(rf"(?:{_HOST_LABEL}\.)+[a-z](?:[a-z0-9-]{{0,61}}[a-z0-9])?")
_URL_UNSAFE = re.compile(r"[\s\\\x00-\x1f\x7f]")
_CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")


class HelpArtifact(BaseModel):
    """可在当前答疑中发布和保存的结构化成果。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kind: Literal["markdown", "svg", "image"] = Field(description="成果类型")
    title: str = Field(min_length=1, max_length=100, description="简短标题")
    content: str = Field(min_length=1, max_length=_MAX_SVG_CHARS, description="Markdown、静态 SVG 或图片 URL")


def build_help_artifact_tool() -> ToolDefinitionInput:
    return {
        "name": PUBLISH_ARTIFACT_TOOL,
        "description": (
            "发布用户可查看的成果：Markdown 文档、静态 SVG 图表，或引用已知的 HTTPS/诊断图片。"
            "SVG 使用基本形状、文本和展示属性。每轮最多四件。"
        ),
        "parameters": HelpArtifact.model_json_schema(),
    }


def _validated_svg(content: str) -> str:
    # 禁用实体、文档类型和处理指令，再从已验证的树重新生成 XML。
    if "<!" in content or "<?" in content:
        raise ValueError("SVG 不允许 XML 声明、文档类型、实体或处理指令")
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, UnicodeError):
        raise ValueError("SVG 格式无效") from None
    namespace = f"{{{_SVG_NS}}}" if root.tag == f"{{{_SVG_NS}}}svg" else ""
    if root.tag != f"{namespace}svg":
        raise ValueError("SVG 必须包含一个 svg 根元素")

    pending: List[Tuple[ElementTree.Element, int]] = [(root, 0)]
    count = 0
    while pending:
        element, depth = pending.pop()
        count += 1
        if count > _MAX_SVG_ELEMENTS or depth > _MAX_SVG_DEPTH:
            raise ValueError("SVG 元素数量或层级超过限制")
        tag = element.tag.removeprefix(namespace)
        geometry = _SVG_GEOMETRY.get(tag)
        if geometry is None or (tag == "svg" and depth != 0):
            raise ValueError("SVG 包含不支持的元素")
        if element.text and element.text.strip() and tag not in _SVG_TEXT_TAGS:
            raise ValueError("SVG 文本必须位于文本元素内")
        for attribute, value in element.attrib.items():
            if attribute not in geometry and attribute not in _SVG_PRESENTATION:
                raise ValueError("SVG 包含不支持的属性")
            pattern = _SVG_VALUES.get(attribute, _LENGTH)
            if len(value) > 6000 or pattern.fullmatch(value.strip()) is None:
                raise ValueError("SVG 属性值无效")
        element.tag = tag
        pending.extend((child, depth + 1) for child in element)

    root.set("xmlns", _SVG_NS)
    return ElementTree.tostring(root, encoding="unicode")


def _validate_image_url(content: str) -> None:
    if len(content) > _MAX_IMAGE_URL_CHARS or _URL_UNSAFE.search(content):
        raise ValueError("图片 URL 格式或长度无效")
    try:
        url = urlsplit(content)
        if content.startswith(f"{_REASONING_IMAGE_PATH}?") and "#" not in content:
            parameters = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True, errors="strict")
            if (
                len(parameters) == 1
                and parameters[0][0] == "path"
                and parameters[0][1].strip()
                and _CONTROL_CHAR.search(parameters[0][1]) is None
            ):
                return
        if (
            url.scheme == "https"
            and url.hostname
            and _PUBLIC_HOST.fullmatch(url.hostname)
            and not url.hostname.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))
            and url.username is None
            and url.password is None
            and url.port in (None, 443)
        ):
            return
    except (ValueError, UnicodeError):
        pass
    raise ValueError("图片必须使用公开 HTTPS URL 或诊断图片路径")


class HelpArtifactStore:
    """一次答疑中累积已校验成果。"""

    def __init__(self) -> None:
        self.artifacts: List[HelpArtifact] = []

    def publish(self, arguments: Dict[str, Any]) -> HelpArtifact:
        try:
            artifact = HelpArtifact.model_validate(arguments)
        except ValidationError:
            raise ValueError("成果格式或长度无效，请按照工具定义提交") from None

        if artifact.kind == "svg":
            artifact = artifact.model_copy(update={"content": _validated_svg(artifact.content)})
        elif artifact.kind == "image":
            _validate_image_url(artifact.content)
        elif len(artifact.content) > _MAX_MARKDOWN_CHARS:
            raise ValueError("Markdown 成果超过长度限制")

        if artifact not in self.artifacts:
            if len(self.artifacts) >= _MAX_ARTIFACTS:
                raise ValueError("单轮最多发布四件成果")
            self.artifacts.append(artifact)
        return artifact
