"""maibot_helper 插件。

向 LLM 暴露部署文档、插件 SDK 文档、版本更新和插件市场浏览工具。
"""

from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import asyncio
import json
import re
import tomllib

from packaging.version import Version

from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from src.common.paths import get_instance_root, resource_path


DOCS_BASE_URL = "https://docs.maibot.chat"
DEFAULT_TIMEOUT_SECONDS = 8
HTTP_USER_AGENT = "MaiBotHelper/1.6"
MAX_PAGE_CHARS = 12800
DEFAULT_CHANGELOG_LIMIT = 3
DEFAULT_CHANGELOG_CHARS = 2500
MAX_CHANGELOG_CHARS = 8000
MAX_PLUGIN_LIST_LIMIT = 50
DEFAULT_PLUGIN_README_CHARS = 8000
MAX_PLUGIN_README_CHARS = 20000
MARKET_SUMMARY_FIELDS = (
    "id", "marketplace_id", "name", "version", "description", "author", "categories", "repository_url", "stats",
)
REPO_ROOT = get_instance_root()
DOCS_DIR = resource_path("mai-docs")
MAIBOT_REPOSITORY = "Mai-with-u/MaiBot"
MAIBOT_RELEASES_API_URL = f"https://api.github.com/repos/{MAIBOT_REPOSITORY}/releases"
MAIBOT_RELEASES_URL = f"https://github.com/{MAIBOT_REPOSITORY}/releases"
WEBUI_REPOSITORY = "Mai-with-u/MaiBot-Dashboard"
WEBUI_RELEASES_API_URL = f"https://api.github.com/repos/{WEBUI_REPOSITORY}/releases"
WEBUI_RELEASES_URL = f"https://github.com/{WEBUI_REPOSITORY}/releases"
WEBUI_PYPI_PACKAGE = "maibot-dashboard"
WEBUI_PYPI_API_URL = f"https://pypi.org/pypi/{WEBUI_PYPI_PACKAGE}/json"
WEBUI_PYPI_PROJECT_URL = f"https://pypi.org/project/{WEBUI_PYPI_PACKAGE}/"


class _ReadableHtmlParser(HTMLParser):
    """从 VitePress HTML 中提取可读文本。"""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        del attrs
        if tag in {"script", "style", "svg", "nav", "footer"}:
            self._skip_depth += 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "tr", "br", "pre"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0 and tag in {"script", "style", "svg", "nav", "footer"}:
            self._skip_depth -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "tr", "pre"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return _normalize_text("\n".join(self._parts))


DOC_PAGES: List[Dict[str, str]] = [
    {
        "key": "deployment",
        "category": "部署",
        "title": "部署概览",
        "path": "manual/index.md",
        "url": f"{DOCS_BASE_URL}/manual/",
        "keywords": "部署 安装 WebUI QQ NapCat 适配器 一键包 Docker 源码 模型 启动",
        "summary": "MaiBot 部署概览：选择源码、一键包或 Docker 部署，通过 WebUI 或 NapCat 适配器与麦麦对话。",
    },
    {
        "key": "installation",
        "category": "部署",
        "title": "源码安装",
        "path": "manual/deployment/windows.md",
        "url": f"{DOCS_BASE_URL}/manual/deployment/windows",
        "keywords": "源码 安装 uv pip bot.py GitHub Release main dev",
        "summary": "源码安装：克隆 MaiBot，使用 uv sync 安装依赖，使用 uv run python bot.py 启动。",
    },
    {
        "key": "one_key",
        "category": "部署",
        "title": "一键包安装",
        "path": "manual/deployment/windows.md",
        "url": f"{DOCS_BASE_URL}/manual/deployment/windows",
        "keywords": "一键包 一键安装 QQ群 下载 更新 新手 Windows",
        "summary": "一键包部署：通过 MaiBot OneKey 管理安装、更新、启动和配置，操作步骤见 Windows 部署文档。",
    },
    {
        "key": "docker",
        "category": "部署",
        "title": "Docker 部署",
        "path": "manual/deployment/docker.md",
        "url": f"{DOCS_BASE_URL}/manual/deployment/docker",
        "keywords": "Docker docker compose 容器 端口 18001 6099 数据目录",
        "summary": "Docker 部署：安装 Docker/Compose，执行 docker compose up -d，配置后重启 core。",
    },
    {
        "key": "adapters",
        "category": "适配器",
        "title": "适配器概览",
        "path": "manual/adapters/index.md",
        "url": f"{DOCS_BASE_URL}/manual/adapters/",
        "keywords": "适配器 概览 NapCat GoCQ SnowLuma Telegram Discord 桌面 微信 wxauto 插件版 独立版 QQ",
        "summary": "适配器概览：列出可用适配器，并说明插件版、独立版、连接配置和连接验证流程。",
    },
    {
        "key": "napcat",
        "category": "适配器",
        "title": "NapCat QQ 连接",
        "path": "manual/adapters/napcat.md",
        "url": f"{DOCS_BASE_URL}/manual/adapters/napcat",
        "keywords": "NapCat QQ 连接 正向 WebSocket 端口 token 插件商店 群聊白名单 群聊名单",
        "summary": "NapCat QQ 连接：通过 WebUI 插件商店安装并启用 NapCat Adapter，配置正向 WebSocket、访问令牌和群聊白名单。",
    },
    {
        "key": "configuration",
        "category": "配置",
        "title": "配置概览",
        "path": "manual/configuration/index.md",
        "url": f"{DOCS_BASE_URL}/manual/configuration/",
        "keywords": "配置 bot_config model_config WebUI 热重载 API key 模型",
        "summary": "主要配置文件是 config/bot_config.toml 和 config/model_config.toml，启动一次后生成。",
    },
    {
        "key": "plugin_quickstart",
        "category": "插件 SDK",
        "title": "插件开发指南",
        "path": "plugin/index.md",
        "url": f"{DOCS_BASE_URL}/plugin/",
        "keywords": "插件 SDK maibot-plugin-sdk maibot_sdk MaiBotPlugin Tool Command Host Runner",
        "summary": "插件继承 MaiBotPlugin，用装饰器声明 Tool、Command、Hook 等组件，并实现生命周期方法。",
    },
    {
        "key": "plugin_manifest",
        "category": "插件 SDK",
        "title": "Manifest 系统",
        "path": "plugin/manifest.md",
        "url": f"{DOCS_BASE_URL}/plugin/manifest",
        "keywords": "_manifest.json manifest_version id version author capabilities dependencies sdk",
        "summary": "每个插件根目录必须包含 _manifest.json，用于声明插件元信息、兼容性、依赖和能力。",
    },
    {
        "key": "plugin_tools",
        "category": "插件 SDK",
        "title": "Tool 组件",
        "path": "plugin/tools.md",
        "url": f"{DOCS_BASE_URL}/plugin/tools",
        "keywords": "@Tool ToolParameterInfo ToolParamType description LLM 工具",
        "summary": "@Tool 向 LLM 暴露可调用工具。",
    },
    {
        "key": "plugin_commands",
        "category": "插件 SDK",
        "title": "Command 组件",
        "path": "plugin/commands.md",
        "url": f"{DOCS_BASE_URL}/plugin/commands",
        "keywords": "@Command 斜杠命令 pattern 正则 stream_id send.text",
        "summary": "@Command 用于用户通过命令触发插件逻辑，通常配合正则 pattern 匹配消息。",
    },
    {
        "key": "plugin_config",
        "category": "插件 SDK",
        "title": "插件配置管理",
        "path": "plugin/config.md",
        "url": f"{DOCS_BASE_URL}/plugin/config",
        "keywords": "PluginConfigBase Field config.toml self.config 配置 热重载 WebUI Schema",
        "summary": "插件可用 PluginConfigBase 声明强类型配置，Runner 会生成默认配置和 WebUI Schema。",
    },
    {
        "key": "plugin_api_reference",
        "category": "插件 SDK",
        "title": "插件 API 参考",
        "path": "plugin/api-reference.md",
        "url": f"{DOCS_BASE_URL}/plugin/api-reference",
        "keywords": "API 参考 ctx send db llm config emoji message component knowledge render",
        "summary": "插件通过 self.ctx 访问发送消息、数据库、LLM、配置、表情、组件、知识库等能力代理。",
    },
]


def _tool_param(name: str, param_type: ToolParamType, description: str, required: bool) -> ToolParameterInfo:
    """构造 Tool 参数声明。"""

    return ToolParameterInfo(name=name, param_type=param_type, description=description, required=required)


def _normalize_text(text: str) -> str:
    """压缩多余空白，保留段落可读性。"""

    normalized = re.sub(r"[ \t\r\f\v]+", " ", text)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _strip_markdown_noise(text: str) -> str:
    """移除 VitePress frontmatter 和容器标记，保留主体内容。"""

    cleaned = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)
    cleaned = re.sub(r":::\s*\w*.*?", "", cleaned)
    cleaned = cleaned.replace(":::", "")
    return _normalize_text(cleaned)


def _clip_text(text: str, max_chars: int) -> str:
    """按字符数裁剪文本。"""

    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 20].rstrip() + "\n\n（内容较长，已截断）"


def _safe_string(value: Any) -> str:
    """把外部接口返回值转换成稳定字符串。"""

    if value is None:
        return ""
    return str(value).strip()


def _strip_version_prefix(version: str) -> str:
    """移除常见的 v 前缀，保留版本主体。"""

    return re.sub(r"^v", "", version.strip(), flags=re.IGNORECASE)


def _fetch_json(url: str, accept: str = "application/json", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> Any:
    """读取远端 JSON，供版本和更新日志查询使用。"""

    request = Request(url, headers={"Accept": accept, "User-Agent": HTTP_USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw_text = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        raise ValueError(f"HTTP {error.code}: {error.reason}") from error
    except (OSError, URLError) as error:
        raise ValueError(str(error)) from error

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"响应不是有效 JSON：{error}") from error


def _market_plugin_summary(plugin: Dict[str, Any]) -> Dict[str, Any]:
    """列表和统计只保留回答所需字段，避免重复清单占用模型上下文。"""

    return {key: value for key, value in plugin.items() if key in MARKET_SUMMARY_FIELDS}


def _github_release_summary(release: Optional[Dict[str, Any]], max_changelog_chars: int) -> Optional[Dict[str, Any]]:
    """整理 GitHub Release 信息。"""

    if release is None:
        return None

    tag_name = _safe_string(release.get("tag_name"))
    changelog = _safe_string(release.get("body")) or "该 Release 没有填写更新日志。"
    return {
        "version": _strip_version_prefix(tag_name),
        "published_at": _safe_string(release.get("published_at")),
        "url": _safe_string(release.get("html_url")),
        "changelog": _clip_text(changelog, max_changelog_chars),
    }


def _as_github_release_list(payload: Any) -> List[Dict[str, Any]]:
    """校验并提取 GitHub Releases 响应。"""

    if not isinstance(payload, list):
        raise ValueError("GitHub Releases 响应格式不是列表")
    return [item for item in payload if isinstance(item, dict)]








def _score_page(page: Dict[str, str], query: str) -> int:
    """根据标题、关键词和摘要给文档页打分。"""

    normalized_query = query.lower().strip()
    if not normalized_query:
        return 0
    haystacks = [
        (page["key"].lower(), 8),
        (page["title"].lower(), 7),
        (page["keywords"].lower(), 5),
        (page["summary"].lower(), 3),
        (page["category"].lower(), 2),
    ]
    terms = [term for term in re.split(r"\s+", normalized_query) if term]
    score = 0
    for term in terms:
        for haystack, weight in haystacks:
            if term in haystack:
                score += weight
    return score


def _find_page(topic: str) -> Optional[Dict[str, str]]:
    """通过 key、标题或关键词查找最匹配的文档页。"""

    normalized_topic = topic.lower().strip()
    if not normalized_topic:
        return None
    for page in DOC_PAGES:
        if normalized_topic in {page["key"].lower(), page["title"].lower()}:
            return page
    scored = sorted(((_score_page(page, normalized_topic), page) for page in DOC_PAGES), key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return None


def _resolve_doc_path(relative_path: str) -> Path:
    """解析并限制 Markdown 文件路径只能位于 mai-docs 目录内。"""

    normalized = relative_path.strip().replace("\\", "/")
    if not normalized:
        raise ValueError("文档路径不能为空")
    if normalized.startswith("/") or re.match(r"^[a-zA-Z]:", normalized):
        raise ValueError("请传入相对 mai-docs 的路径")
    doc_path = (DOCS_DIR / normalized).resolve()
    docs_root = DOCS_DIR.resolve()
    if doc_path != docs_root and docs_root not in doc_path.parents:
        raise ValueError("文档路径不能超出 mai-docs 目录")
    if doc_path.suffix.lower() != ".md":
        raise ValueError("只能读写 .md 文件")
    return doc_path


def _read_model_task_assignments(task_name: str) -> Dict[str, Any]:
    """读取已保存的分配，仅投影允许公开的字段，禁止返回完整配置或解析错误原文。"""

    try:
        with (REPO_ROOT / "config" / "model_config.toml").open("rb") as config_file:
            config = tomllib.load(config_file)
    except tomllib.TOMLDecodeError:
        # TOML 错误可能包含原始配置行，不能将其暴露给工具调用方。
        raise ValueError("模型配置 TOML 格式错误，请管理员检查配置文件") from None
    tasks = config["model_task_config"]
    if task_name and task_name not in tasks:
        raise ValueError("未知模型任务，请留空 task_name 查看所有已配置任务")
    models = {model["name"]: model for model in config["models"]}
    assignments = []
    for name, task in tasks.items():
        if task_name and name != task_name:
            continue
        if name == "web_search":
            # 联网搜索分配的是服务而非模型，不读取服务定义中的鉴权信息。
            assignments.append({"task_name": name, "services": list(task["model_list"])})
            continue
        assigned_models = []
        for model_name in task["model_list"]:
            model = models[model_name]
            assigned_models.append({
                "name": model["name"],
                "model_identifier": model["model_identifier"],
                "api_provider": model["api_provider"],
            })
        assignments.append({"task_name": name, "models": assigned_models})
    return {
        "note": "只读查看已保存的显式分配；空列表表示未显式分配，不代表运行时没有继用其他任务。",
        "tasks": assignments,
    }


class QueryPreferences(PluginConfigBase):
    """日常查询默认值，工具显式参数优先。"""

    __ui_label__ = "查询偏好"
    __ui_icon__ = "search"
    __ui_order__ = 0
    __ui_i18n__ = {
        "zh_CN": {"title": "查询偏好", "description": "设置日常查询习惯，保存后生效。"},
        "en_US": {"title": "Query preferences", "description": "Choose query defaults. Changes apply after saving."},
        "ja_JP": {"title": "検索設定", "description": "通常の検索設定です。保存後に反映されます。"},
    }

    config_version: str = Field(default="1.1.0", json_schema_extra={"hidden": True})

    enabled: bool = Field(default=False, description="启用 Helper", json_schema_extra={"hidden": True})

    plugin_limit: int = Field(
        default=20, ge=1, le=50, description="每次显示的插件数",
        json_schema_extra={'label': '每次显示的插件数', 'hint': '搜索和排行榜未指定数量时使用，范围 1–50。', 'order': 0, 'i18n': {'zh_CN': {'label': '每次显示的插件数', 'hint': '搜索和排行榜未指定数量时使用，范围 1–50。'}, 'en_US': {'label': 'Plugins per page', 'hint': 'Used when a search or ranking does not specify a limit (1–50).'}, 'ja_JP': {'label': 'プラグイン表示件数', 'hint': '検索やランキングで件数を指定しない場合に使用します（1〜50）。'}}},
    )

    compatible_only: bool = Field(
        default=False, description="默认只看兼容插件",
        json_schema_extra={'label': '默认只看兼容插件', 'hint': '未指定筛选条件时，只显示当前麦麦和 SDK 可用的插件。', 'order': 1, 'i18n': {'zh_CN': {'label': '默认只看兼容插件', 'hint': '未指定筛选条件时，只显示当前麦麦和 SDK 可用的插件。'}, 'en_US': {'label': 'Compatible plugins only', 'hint': 'By default, show only plugins compatible with the current MaiBot and SDK.'}, 'ja_JP': {'label': '互換性のあるプラグインのみ', 'hint': '条件を指定しない場合、現在の MaiBot と SDK で利用できるプラグインのみ表示します。'}}},
    )

    prefer_local_readme: bool = Field(
        default=True, description="优先读取本地说明",
        json_schema_extra={'label': '优先读取本地说明', 'hint': '优先读取已安装插件的 README；没有本地说明时读取远程内容。', 'order': 2, 'i18n': {'zh_CN': {'label': '优先读取本地说明', 'hint': '优先读取已安装插件的 README；没有本地说明时读取远程内容。'}, 'en_US': {'label': 'Prefer local README', 'hint': 'Read installed plugins’ README first; use the remote README when unavailable locally.'}, 'ja_JP': {'label': 'ローカルの README を優先', 'hint': 'インストール済みの README を優先し、ない場合はリモートから読み込みます。'}}},
    )


class MaiBotHelperConfig(PluginConfigBase):
    """Helper 基础配置。"""

    plugin: QueryPreferences = Field(default_factory=QueryPreferences)


class MaiBotHelperPlugin(MaiBotPlugin):
    """MaiBot 部署、插件 SDK、版本信息和插件市场助手。"""

    config_model = MaiBotHelperConfig

    async def on_load(self) -> None:
        """处理插件加载。"""

        self.ctx.logger.info("maibot_helper 已加载")

    async def on_unload(self) -> None:
        """处理插件卸载。"""

        self.ctx.logger.info("maibot_helper 已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, object], version: str) -> None:
        """处理配置热重载事件。"""

        del scope
        del config_data
        del version


    @Tool(
        "maibot_resource_link",
        description=(
            "获取麦麦Maibot的所有下载资源地址和信息资源地址链接。当用户需要Maibot麦麦官方文档，Maibot麦麦官方网站，人设分享网站，插件市场，一键包下载地址时使用。"
        ),
        parameters=[],
    )
    async def get_resource_link(self, **kwargs: Any) -> Dict[str, Any]:
        """提供固定资源入口，无需联网查询。"""

        del kwargs
        return {
            "resources": [
                {"name": "麦麦一键包 CNB 下载", "url": "https://cnb.cool/Mai-with-u/MaiBotOneKey/-/releases"},
                {"name": "麦麦文档", "url": DOCS_BASE_URL + "/"},
                {"name": "麦麦插件市场", "url": "https://plugin.maibot.chat/"},
                {"name": "麦麦官方网站", "url": "https://maibot.chat/"},
                {"name": "麦麦人设分享", "url": "https://maiprompt.top/"},
            ],
        }

    @Tool(
        "maibot_model_tasks",
        description=(
            "查看自己配置的模型。当用户询问MaiBot自身分配了什么模型时调用。读取已保存的 model_config.toml。task_name 留空查看全部，指定 replyer、planner 等任务名查看单项。不返回密钥信息。"
        ),
        parameters=[_tool_param("task_name", ToolParamType.STRING, "任务名；留空查看全部任务", False)],
    )
    async def get_model_tasks(self, task_name: str = "", **kwargs: Any) -> Dict[str, Any]:
        """在线程中读取配置，避免同步文件操作阻塞事件循环。"""

        del kwargs
        return await asyncio.to_thread(_read_model_task_assignments, task_name.strip())

    @Tool(
        "maibot_docs_search",
        description=(
            "搜索 MaiBot 部署和插件 SDK 文档，返回相关页面、摘要和链接。当用户询问技术问题、报错问题、部署、配置、连接适配器、开发插件或使用插件 SDK 时调用。返回结果可作为后续读取具体文档页的 topic。"
        ),
        parameters=[
            _tool_param("query", ToolParamType.STRING, "搜索关键词，例如：一键包、Docker 部署、NapCat、Tool 组件", True),
            _tool_param("category", ToolParamType.STRING, "可选分类：部署、适配器、配置、插件 SDK；为空则搜索全部", False),
            _tool_param("limit", ToolParamType.INTEGER, "最多返回几条结果，默认 5", False),
        ],
    )
    async def search_docs(self, query: str, category: str = "", limit: int = 5, **kwargs: Any) -> Dict[str, Any]:
        """搜索文档索引。"""

        del kwargs

        normalized_category = category.strip()
        max_results = min(max(int(limit or 5), 1), 10)
        candidates = [
            page
            for page in DOC_PAGES
            if not normalized_category or normalized_category.lower() in page["category"].lower()
        ]
        scored = sorted(((_score_page(page, query), page) for page in candidates), key=lambda item: item[0], reverse=True)
        results = [
            {
                "topic": page["key"],
                "title": page["title"],
                "category": page["category"],
                "summary": page["summary"],
                "url": page["url"],
            }
            for score, page in scored
            if score > 0
        ][:max_results]
        if not results:
            results = [
                {
                    "topic": page["key"],
                    "title": page["title"],
                    "category": page["category"],
                    "summary": page["summary"],
                    "url": page["url"],
                }
                for page in candidates[:max_results]
            ]
        return {"success": True, "query": query, "results": results}

    @Tool(
        "maibot_docs_page",
        description=(
            "读取 MaiBot 文档中的指定主题页面，返回可用于回答用户的正文摘要。topic 可使用 maibot_docs_search 返回的 topic，也可直接传入 deployment、installation、one_key、docker、adapters、napcat、configuration、plugin_quickstart、plugin_manifest、plugin_tools、plugin_commands、plugin_config、plugin_api_reference 等。"
        ),
        parameters=[
            _tool_param("topic", ToolParamType.STRING, "文档主题 key、标题或关键词", True),
            _tool_param("max_chars", ToolParamType.INTEGER, "返回正文最大字符数，默认 6400，最大 12800", False),
        ],
    )
    async def get_doc_page(self, topic: str, max_chars: int = 6400, **kwargs: Any) -> Dict[str, Any]:
        """读取指定文档页。"""

        del kwargs

        page = _find_page(topic)
        if page is None:
            return {
                "success": False,
                "error": f"没有找到主题：{topic}",
                "available_topics": [item["key"] for item in DOC_PAGES],
            }

        content = await self._load_page_content(page)
        return {
            "success": True,
            "topic": page["key"],
            "title": page["title"],
            "category": page["category"],
            "url": page["url"],
            "content": _clip_text(content or page["summary"], min(max(int(max_chars or 6400), 800), MAX_PAGE_CHARS)),
        }

    @Tool(
        "maibot_release_info",
        description=(
            "获取 MaiBot 最新版本、WebUI 最新版本和更新日志。当用户询问 MaiBot 最新版、稳定版、测试版、WebUI 最新版本、更新日志、升级内容或下载链接时调用。"
        ),
        parameters=[
            _tool_param("changelog_limit", ToolParamType.INTEGER, "返回最近几个 MaiBot Release 的更新日志，默认 3，范围 1 到 10", False),
            _tool_param("max_changelog_chars", ToolParamType.INTEGER, "每条更新日志最大字符数，默认 2500，范围 500 到 8000", False),
        ],
    )
    async def get_release_info(
        self,
        changelog_limit: int = DEFAULT_CHANGELOG_LIMIT,
        max_changelog_chars: int = DEFAULT_CHANGELOG_CHARS,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """查询稳定版本，保留版本、链接及更新日志；各来源失败独立报告。"""
        del kwargs
        recent_limit = min(max(int(changelog_limit or DEFAULT_CHANGELOG_LIMIT), 1), 10)
        changelog_chars = min(max(int(max_changelog_chars or DEFAULT_CHANGELOG_CHARS), 500), MAX_CHANGELOG_CHARS)
        sources = ("maibot_github_releases", "webui_pypi", "webui_github_releases")
        responses = await asyncio.gather(
            asyncio.to_thread(_fetch_json, f"{MAIBOT_RELEASES_API_URL}?per_page=50"),
            asyncio.to_thread(_fetch_json, WEBUI_PYPI_API_URL),
            asyncio.to_thread(_fetch_json, f"{WEBUI_RELEASES_API_URL}?per_page=50"),
            return_exceptions=True,
        )
        result: Dict[str, Any] = {"success": False, "maibot": {}, "webui": {}, "errors": []}
        for source, response in zip(sources, responses, strict=True):
            if isinstance(response, Exception):
                result["errors"].append({"source": source, "error": str(response)})
                continue
            try:
                if source == "webui_pypi":
                    # 按版本选择稳定且仍有可用文件的发布，排除 dev/pre 和已撤回文件。
                    if not isinstance(response, dict) or not isinstance(response.get("releases"), dict):
                        raise ValueError("PyPI releases 响应格式不是对象")
                    candidates = []
                    for version, files in response["releases"].items():
                        parsed = Version(version)
                        if parsed.is_prerelease or parsed.is_devrelease:
                            continue
                        available = [file for file in files if not file.get("yanked", False)]
                        if available:
                            candidates.append((parsed, version, available))
                    if not candidates:
                        raise ValueError("PyPI 没有可用的稳定版本")
                    _, version, files = max(candidates, key=lambda item: item[0])
                    result["webui"]["latest"] = {
                        "version": version,
                        "published_at": max(_safe_string(file.get("upload_time_iso_8601") or file.get("upload_time")) for file in files),
                        "url": f"{WEBUI_PYPI_PROJECT_URL}{version}/",
                    }
                else:
                    releases = [release for release in _as_github_release_list(response)
                                if not release.get("draft") and not release.get("prerelease")]
                    if source == "maibot_github_releases":
                        result["maibot"] = {
                            "latest": _github_release_summary(releases[0], changelog_chars) if releases else None,
                            "recent_changelogs": [_github_release_summary(release, changelog_chars)
                                                  for release in releases[:recent_limit]],
                        }
                    else:
                        # 独立保留 Dashboard Release 的版本，避免把日志误归到不同的 PyPI 版本。
                        result["webui"]["github_release"] = (
                            _github_release_summary(releases[0], changelog_chars) if releases else None
                        )
            except (ValueError, TypeError, KeyError) as error:
                result["errors"].append({"source": source, "error": str(error)})
        result["success"] = bool(result["maibot"].get("latest") or result["webui"].get("latest"))
        return result

    @Tool(
        "maibot_plugin_list",
        description=(
            "当用户需要扩展Maibot自身能力，或者需要插件推荐时，当用户想查看有哪些 MaiBot 插件、按功能寻找插件时调用；搜索和浏览共用本工具。默认返回简洁摘要及插件 ID，详细能力、依赖和兼容版本请调用 maibot_plugin_detail，使用说明请调用 maibot_plugin_readme。compatible_only=true 可筛选当前环境可用插件。查询热门、下载、点赞、评分及 7/30 日飙升榜时设置 sort_by，统计排序自动附带统计。单个插件统计请调用 maibot_plugin_detail。"
        ),
        parameters=[
            _tool_param("sort_by", ToolParamType.STRING, "排序：name（默认）、downloads、likes、rating、downloads_growth_7d、likes_growth_7d、downloads_growth_30d、likes_growth_30d；统计排序自动附带统计", False),
            _tool_param("query", ToolParamType.STRING, "搜索关键词，可匹配插件 ID、名称、描述、作者、分类、关键词或仓库地址", False),
            _tool_param("category", ToolParamType.STRING, "按分类过滤，例如 Game、Moderation、Adapter；为空则不过滤", False),
            _tool_param("page", ToolParamType.INTEGER, "页码，从 1 开始，默认 1", False),
            _tool_param("limit", ToolParamType.INTEGER, "每页插件数量；未传时使用查询偏好（初始 20），最大 50", False),
            _tool_param("include_manifest", ToolParamType.BOOLEAN, "是否返回完整元信息和 manifest，默认 false；单插件详情优先使用 maibot_plugin_detail", False),
            _tool_param("compatible_only", ToolParamType.BOOLEAN, "是否只返回当前 Host 和 SDK 环境兼容的插件；未传时使用查询偏好（初始 false）", False),
            _tool_param("force_refresh", ToolParamType.BOOLEAN, "是否强制刷新插件市场缓存，默认 false", False),
            _tool_param("include_stats", ToolParamType.BOOLEAN, "是否附带插件统计和 7/30 日飙升排名，默认 false", False),
        ],
    )
    async def list_market_plugins(
        self,
        query: str = "",
        category: str = "",
        page: int = 1,
        limit: Optional[int] = None,
        include_manifest: bool = False,
        force_refresh: bool = False,
        include_stats: bool = False,
        compatible_only: Optional[bool] = None,
        sort_by: str = "name",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """通过 Host 搜索市场，默认只返回插件摘要。"""

        del kwargs
        if limit is None:
            limit = self.config.plugin.plugin_limit
        if compatible_only is None:
            compatible_only = self.config.plugin.compatible_only
        result = await self.ctx.market.search(
            query, category=category, page=max(int(page), 1),
            limit=min(max(int(limit), 1), MAX_PLUGIN_LIST_LIMIT),
            compatible_only=compatible_only, include_manifest=include_manifest,
            sort_by=sort_by, include_stats=include_stats or sort_by != "name", force_refresh=force_refresh,
        )
        if include_manifest:
            return result
        return {**result, "plugins": [_market_plugin_summary(plugin) for plugin in result["plugins"]]}

    @Tool(
        "maibot_plugin_detail",
        description=(
            "查询一个 MaiBot 插件的完整元信息、下载量、点赞、评分、兼容性和推荐安装版本。已知具体插件时直接调用，可传插件 ID、市场 ID、名称或已登记仓库地址；搜索结果中的 id 最准确；安装配置说明请调用 maibot_plugin_readme。"
        ),
        parameters=[
            _tool_param("plugin_id", ToolParamType.STRING, "插件 ID、市场 ID、名称或已登记仓库地址", True),
            _tool_param("include_manifest", ToolParamType.BOOLEAN, "是否附带插件及各发布版本的完整 manifest，默认 false", False),
            _tool_param("force_refresh", ToolParamType.BOOLEAN, "是否强制刷新市场详情缓存，默认 false", False),
        ],
    )
    async def get_market_plugin_detail(
        self, plugin_id: str, include_manifest: bool = False, force_refresh: bool = False, **kwargs: Any,
    ) -> Dict[str, Any]:
        """读取 Host 详情，默认移除各版本重复的完整清单。"""

        del kwargs
        result = await self.ctx.market.get_detail(plugin_id, force_refresh=force_refresh)
        if include_manifest:
            return result
        releases = result["releases"]
        return {
            **result,
            "plugin": {key: value for key, value in result["plugin"].items() if key != "manifest"},
            "releases": {
                **releases,
                "versions": [
                    {key: value for key, value in release.items() if key != "manifest"}
                    for release in releases["versions"]
                ],
            },
        }

    @Tool(
        "maibot_plugin_readme",
        description=(
            "浏览某个具体 MaiBot 插件的 README。当用户询问某个插件怎么用、插件说明、安装/配置步骤或 README 内容时调用。可传插件 ID、市场 ID、插件名称或已登记的 GitHub 仓库地址。"
        ),
        parameters=[
            _tool_param("plugin_id", ToolParamType.STRING, "插件 ID、市场 ID、插件名称或 GitHub 仓库地址", True),
            _tool_param("max_chars", ToolParamType.INTEGER, "返回 README 最大字符数，默认 8000，最大 20000", False),
            _tool_param("prefer_local", ToolParamType.BOOLEAN, "是否优先读取本地已安装插件 README；未传时使用查询偏好（初始 true）", False),
            _tool_param("branch", ToolParamType.STRING, "远程仓库分支，默认 main；仅读取指定分支", False),
            _tool_param("force_refresh", ToolParamType.BOOLEAN, "是否强制刷新 README 缓存，默认 false", False),
        ],
    )
    async def get_plugin_readme(
        self,
        plugin_id: str,
        max_chars: int = DEFAULT_PLUGIN_README_CHARS,
        prefer_local: Optional[bool] = None,
        branch: str = "main",
        force_refresh: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """读取某个插件的 README。"""

        del kwargs
        if prefer_local is None:
            prefer_local = self.config.plugin.prefer_local_readme
        return await self.ctx.market.get_readme(
            plugin_id, max_chars=min(max(int(max_chars), 500), MAX_PLUGIN_README_CHARS),
            prefer_local=prefer_local, branch=branch, force_refresh=force_refresh,
        )

    async def _load_page_content(self, page: Dict[str, str]) -> str:
        """读取本地或在线文档页。"""

        local_text = self._read_local_doc(page["path"])
        if local_text:
            return local_text
        online_text = await asyncio.to_thread(self._fetch_online_doc, page["url"])
        return online_text or page["summary"]


    def _read_local_doc(self, relative_path: str) -> str:
        """读取仓库内 mai-docs 文档副本。"""

        try:
            doc_path = _resolve_doc_path(relative_path)
        except ValueError:
            return ""
        if not doc_path.exists() or not doc_path.is_file():
            return ""
        try:
            return _strip_markdown_noise(doc_path.read_text(encoding="utf-8"))
        except OSError:
            return ""

    def _fetch_online_doc(self, url: str) -> str:
        """从在线文档站拉取页面正文。"""

        request = Request(url, headers={"User-Agent": HTTP_USER_AGENT})
        try:
            with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                raw_html = response.read().decode("utf-8", errors="replace")
        except (OSError, URLError):
            return ""
        parser = _ReadableHtmlParser()
        parser.feed(raw_html)
        return parser.get_text()


def create_plugin() -> MaiBotHelperPlugin:
    """创建 maibot_helper 插件实例。"""

    return MaiBotHelperPlugin()
