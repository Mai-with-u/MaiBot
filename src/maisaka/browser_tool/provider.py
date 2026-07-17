"""实验性动作票据式网页浏览 Tool Provider。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import secrets

from src.config.config import config_manager
from src.core.tooling import (
    ToolAvailabilityContext,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolInvocation,
    ToolProvider,
    ToolSpec,
)

from .service import (
    BrowserActionError,
    BrowserActionSettings,
    BrowserActionManager,
    BrowserRecoverableActionError,
    get_browser_action_manager,
)


def _build_browser_tool_specs() -> List[ToolSpec]:
    """构建只包含会话入口、动作票据执行和关闭能力的工具声明。"""

    common_metadata = {
        "capability_group": "experimental_browser",
        "progressive_disclosure": "action_ticket",
    }
    return [
        ToolSpec(
            name="browser_start",
            description=(
                "仅在需要访问公开网页时启动隔离浏览会话。在 url 与 search_query 中二选一；"
                "只返回相关正文和相关语义区域内可安全执行的一次性动作票据，不返回选择器。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要直接打开的完整 http/https URL，与 search_query 二选一。",
                    },
                    "search_query": {
                        "type": "string",
                        "description": "要通过搜索引擎检索的关键词，与 url 二选一。",
                    },
                },
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
        """解析参数并创建浏览会话。"""

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
            search_query=self._optional_string(arguments, "search_query"),
            url=self._optional_string(arguments, "url"),
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
    def _optional_string(arguments: Dict[str, Any], name: str) -> str:
        """读取可选字符串参数。"""

        value = arguments.get(name)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise BrowserActionError(f"{name} 必须是字符串。")
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
