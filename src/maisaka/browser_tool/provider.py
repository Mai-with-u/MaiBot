"""实验性动作票据式网页浏览 Tool Provider。"""

from __future__ import annotations

from base64 import b64encode
from typing import Any, Dict, List, Optional
import json
import secrets

from src.config.config import config_manager
from src.core.tooling import (
    ToolAvailabilityContext,
    ToolContentItem,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolInvocation,
    ToolProvider,
    ToolSpec,
)

from .service import (
    BrowserActionError,
    BrowserActionManager,
    BrowserActionSettings,
    BrowserImageContent,
    BrowserRecoverableActionError,
    BrowserScreenshot,
    get_browser_action_manager,
)


def _build_browser_tool_specs() -> List[ToolSpec]:
    """构建 URL 入口、独立搜索、动作票据执行和关闭能力的工具声明。"""

    common_metadata = {
        "capability_group": "experimental_browser",
        "progressive_disclosure": "action_ticket",
    }
    return [
        ToolSpec(
            name="browser_start",
            description=(
                "从一个明确的公开 http/https URL 启动隔离浏览会话。所有浏览都必须从 URL 开始；"
                "需要搜索时，先打开目标网站或搜索网站，再使用页面披露的搜索框完成操作。"
                "只返回相关正文和相关语义区域内可安全执行的一次性动作票据，不返回选择器。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "作为浏览起点直接打开的完整公开 http/https URL。",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            provider_name="experimental_browser",
            provider_type="browser",
            metadata=dict(common_metadata),
        ),
        ToolSpec(
            name="browser_step",
            description=(
                "执行 browser_start 或上一次 browser_step 返回的一张页面动作票据。必须原样携带"
                " browser_session_id、page_version 和 action_id；页面变化后旧票据立即失效。"
                "若失败结果包含新票据，可直接继续当前会话。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "browser_session_id": {
                        "type": "string",
                        "description": "浏览工具返回的隔离浏览会话 ID。",
                    },
                    "page_version": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "动作菜单绑定的页面版本。",
                    },
                    "action_id": {
                        "type": "string",
                        "description": "当前页面动作菜单中的一次性 action_id。",
                    },
                    "value": {
                        "type": "string",
                        "description": "填写输入框或选择选项时使用；无输入动作应省略。",
                    },
                },
                "required": ["browser_session_id", "page_version", "action_id"],
                "additionalProperties": False,
            },
            provider_name="experimental_browser",
            provider_type="browser",
            metadata=dict(common_metadata),
        ),
        ToolSpec(
            name="browser_screenshot",
            description=(
                "仅在需要判断图片、画布、复杂布局、遮罩，或结构化页面信息不足时，按需截取当前浏览器视口。"
                "普通阅读和操作不要截图；长页面应先用 browser_step 滚动到相关区域。"
                "截图绑定当前 page_version，不改变页面状态和动作票据。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "browser_session_id": {
                        "type": "string",
                        "description": "浏览工具返回的隔离浏览会话 ID。",
                    },
                    "page_version": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "要截图的当前页面版本。",
                    },
                },
                "required": ["browser_session_id", "page_version"],
                "additionalProperties": False,
            },
            provider_name="experimental_browser",
            provider_type="browser",
            metadata=dict(common_metadata),
        ),
        ToolSpec(
            name="browser_get_image",
            description=(
                "读取当前页面 images 列表中的一张可见图片，并将其作为独立图片媒体返回。"
                "只能使用当前 page_version 披露的 image_id，不接受任意 URL。"
                "需要把图片发给用户时，继续使用 reply.attach_pic 或 send_image 引用工具返回的媒体索引。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "browser_session_id": {
                        "type": "string",
                        "description": "浏览工具返回的隔离浏览会话 ID。",
                    },
                    "page_version": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "图片列表所属的当前页面版本。",
                    },
                    "image_id": {
                        "type": "string",
                        "description": "当前页面 images 列表中的一次性图片票据。",
                    },
                },
                "required": ["browser_session_id", "page_version", "image_id"],
                "additionalProperties": False,
            },
            provider_name="experimental_browser",
            provider_type="browser",
            metadata=dict(common_metadata),
        ),
        ToolSpec(
            name="browser_stop",
            description="完成浏览后关闭隔离浏览会话并立即释放 Cookie、页面状态和动作票据。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "browser_session_id": {
                        "type": "string",
                        "description": "要关闭的浏览会话 ID。",
                    }
                },
                "required": ["browser_session_id"],
                "additionalProperties": False,
            },
            provider_name="experimental_browser",
            provider_type="browser",
            metadata=dict(common_metadata),
        ),
    ]


class BrowserActionToolProvider(ToolProvider):
    """根据实验性配置动态暴露动作票据式网页浏览工具。"""

    provider_name = "experimental_browser"
    provider_type = "browser"

    def __init__(self, manager: Optional[BrowserActionManager] = None) -> None:
        """初始化 Provider，并分配独立资源所有者标识。"""

        self._manager = manager or get_browser_action_manager()
        self._owner_id = f"browser_provider_{secrets.token_urlsafe(10)}"

    async def list_tools(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> List[ToolSpec]:
        """仅在实验性网页浏览开关启用时声明工具。"""

        del context
        if not config_manager.get_global_config().experimental.browser.enabled:
            await self._manager.close_owner(self._owner_id)
            return []
        return _build_browser_tool_specs()

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        """执行浏览会话入口、动作票据或关闭请求。"""

        if not config_manager.get_global_config().experimental.browser.enabled:
            return self._failure(invocation.tool_name, "实验性网页浏览功能尚未启用。")
        if context is None or not context.session_id.strip():
            return self._failure(invocation.tool_name, "网页浏览需要绑定真实聊天流，当前缺少 session_id。")

        scope_key = self._build_scope_key(context)
        try:
            if invocation.tool_name == "browser_start":
                manifest = await self._handle_start(invocation.arguments, scope_key)
                return self._success(invocation.tool_name, manifest)
            if invocation.tool_name == "browser_step":
                manifest = await self._handle_step(invocation.arguments, scope_key)
                return self._success(invocation.tool_name, manifest)
            if invocation.tool_name == "browser_screenshot":
                screenshot = await self._handle_screenshot(invocation.arguments, scope_key)
                return self._screenshot_success(invocation.tool_name, screenshot)
            if invocation.tool_name == "browser_get_image":
                image = await self._handle_get_image(invocation.arguments, scope_key)
                return self._image_success(invocation.tool_name, image)
            if invocation.tool_name == "browser_stop":
                browser_session_id = self._required_string(invocation.arguments, "browser_session_id")
                await self._manager.stop(
                    browser_session_id=browser_session_id,
                    owner_id=self._owner_id,
                    scope_key=scope_key,
                )
                return ToolExecutionResult(
                    tool_name=invocation.tool_name,
                    success=True,
                    content="浏览会话已关闭，页面状态和动作票据已释放。",
                    structured_content={"browser_session_id": browser_session_id, "closed": True},
                )
            return self._failure(invocation.tool_name, f"未知的网页浏览工具：{invocation.tool_name}")
        except BrowserRecoverableActionError as exc:
            return self._recoverable_failure(invocation.tool_name, str(exc), exc.manifest)
        except BrowserActionError as exc:
            return self._failure(invocation.tool_name, str(exc))

    async def close(self) -> None:
        """关闭当前 Provider 创建的全部浏览会话。"""

        await self._manager.close_owner(self._owner_id)

    async def _handle_start(self, arguments: Dict[str, Any], scope_key: str) -> Dict[str, Any]:
        """从明确 URL 创建浏览会话。"""

        return await self._start_session(
            scope_key=scope_key,
            url=self._required_string(arguments, "url"),
        )

    async def _start_session(
        self,
        *,
        scope_key: str,
        url: str,
    ) -> Dict[str, Any]:
        """使用统一配置从明确 URL 创建浏览会话。"""

        browser_config = config_manager.get_global_config().experimental.browser
        settings = BrowserActionSettings(
            session_timeout_seconds=browser_config.session_timeout_seconds,
            navigation_timeout_seconds=browser_config.navigation_timeout_seconds,
            max_page_text_length=browser_config.max_page_text_length,
            max_actions=browser_config.max_actions,
        )
        return await self._manager.start(
            owner_id=self._owner_id,
            scope_key=scope_key,
            settings=settings,
            url=url,
        )

    async def _handle_step(self, arguments: Dict[str, Any], scope_key: str) -> Dict[str, Any]:
        """解析参数并执行当前页面的一张动作票据。"""

        raw_page_version = arguments.get("page_version")
        if isinstance(raw_page_version, bool) or not isinstance(raw_page_version, int):
            raise BrowserActionError("page_version 必须是整数。")
        raw_value = arguments.get("value")
        if raw_value is not None and not isinstance(raw_value, str):
            raise BrowserActionError("value 必须是字符串。")
        return await self._manager.step(
            action_id=self._required_string(arguments, "action_id"),
            browser_session_id=self._required_string(arguments, "browser_session_id"),
            owner_id=self._owner_id,
            page_version=raw_page_version,
            scope_key=scope_key,
            value=raw_value,
        )

    async def _handle_screenshot(
        self,
        arguments: Dict[str, Any],
        scope_key: str,
    ) -> BrowserScreenshot:
        """校验页面版本并按需截取当前视口。"""

        raw_page_version = arguments.get("page_version")
        if isinstance(raw_page_version, bool) or not isinstance(raw_page_version, int):
            raise BrowserActionError("page_version 必须是整数。")
        return await self._manager.screenshot(
            browser_session_id=self._required_string(arguments, "browser_session_id"),
            owner_id=self._owner_id,
            page_version=raw_page_version,
            scope_key=scope_key,
        )

    async def _handle_get_image(
        self,
        arguments: Dict[str, Any],
        scope_key: str,
    ) -> BrowserImageContent:
        """校验图片票据并读取当前页面中的单张可见图片。"""

        raw_page_version = arguments.get("page_version")
        if isinstance(raw_page_version, bool) or not isinstance(raw_page_version, int):
            raise BrowserActionError("page_version 必须是整数。")
        return await self._manager.get_image(
            browser_session_id=self._required_string(arguments, "browser_session_id"),
            image_id=self._required_string(arguments, "image_id"),
            owner_id=self._owner_id,
            page_version=raw_page_version,
            scope_key=scope_key,
        )

    @staticmethod
    def _build_scope_key(context: ToolExecutionContext) -> str:
        """使用已有真实聊天流和可用用户信息构造浏览资源作用域。"""

        scope_parts = [context.platform.strip() or "unknown", context.session_id.strip()]
        if context.is_group_chat is True and context.user_id.strip():
            scope_parts.append(context.user_id.strip())
        return ":".join(scope_parts)

    @staticmethod
    def _required_string(arguments: Dict[str, Any], name: str) -> str:
        """读取必填非空字符串参数。"""

        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise BrowserActionError(f"{name} 必须是非空字符串。")
        return value.strip()

    @staticmethod
    def _success(tool_name: str, manifest: Dict[str, Any]) -> ToolExecutionResult:
        """把动作菜单同时写入文本历史与结构化结果。"""

        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=json.dumps(manifest, ensure_ascii=False),
            structured_content=manifest,
            metadata={
                "browser_session_id": manifest.get("browser_session_id", ""),
                "page_version": manifest.get("page_version"),
            },
        )

    @staticmethod
    def _screenshot_success(
        tool_name: str,
        screenshot: BrowserScreenshot,
    ) -> ToolExecutionResult:
        """将截图作为独立图片内容返回，文本历史只保留精简索引。"""

        screenshot_payload = {
            "browser_session_id": screenshot.browser_session_id,
            "page_version": screenshot.page_version,
            "title": screenshot.title,
            "url": screenshot.url,
            "viewport_screenshot": True,
        }
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=json.dumps(screenshot_payload, ensure_ascii=False),
            structured_content=screenshot_payload,
            content_items=[
                ToolContentItem(
                    content_type="image",
                    data=b64encode(screenshot.image_bytes).decode("ascii"),
                    mime_type="image/png",
                    name=f"browser-page-{screenshot.page_version}.png",
                    metadata={
                        "context_key": (
                            f"{screenshot.browser_session_id}:page:{screenshot.page_version}"
                        ),
                        "source_url": screenshot.url,
                    },
                )
            ],
            metadata={
                "browser_session_id": screenshot.browser_session_id,
                "page_version": screenshot.page_version,
            },
        )

    @staticmethod
    def _image_success(
        tool_name: str,
        image: BrowserImageContent,
    ) -> ToolExecutionResult:
        """将页面图片票据解析结果作为独立图片媒体返回。"""

        image_payload = {
            "browser_session_id": image.browser_session_id,
            "page_version": image.page_version,
            "image_id": image.image_id,
            "label": image.label,
            "url": image.url,
        }
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=json.dumps(image_payload, ensure_ascii=False),
            structured_content=image_payload,
            content_items=[
                ToolContentItem(
                    content_type="image",
                    data=b64encode(image.image_bytes).decode("ascii"),
                    mime_type="image/png",
                    name=f"browser-image-{image.image_id}.png",
                    metadata={
                        "context_key": (
                            f"{image.browser_session_id}:page:{image.page_version}:image:{image.image_id}"
                        ),
                        "source_url": image.url,
                    },
                )
            ],
            metadata={
                "browser_session_id": image.browser_session_id,
                "page_version": image.page_version,
            },
        )

    @staticmethod
    def _recoverable_failure(
        tool_name: str,
        message: str,
        manifest: Dict[str, Any],
    ) -> ToolExecutionResult:
        """返回失败状态和刷新后的动作票据，让 Planner 无需重启浏览会话。"""

        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            content=json.dumps(manifest, ensure_ascii=False),
            error_message=message,
            structured_content=manifest,
            metadata={
                "browser_session_id": manifest.get("browser_session_id", ""),
                "page_version": manifest.get("page_version"),
                "recoverable": True,
            },
        )

    @staticmethod
    def _failure(tool_name: str, message: str) -> ToolExecutionResult:
        """构造浏览工具失败结果。"""

        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_message=message,
        )
