"""能力服务层

Host 端实现的能力服务，处理来自插件的 cap.* 请求。
每个能力方法被注册到 RPC Server，接收 Runner 转发的请求并执行实际操作。
"""

from typing import Any, Callable, Dict, List, Coroutine, TYPE_CHECKING

from src.common.logger import get_logger
from src.plugin_runtime.protocol.envelope import CapabilityRequestPayload, CapabilityResponsePayload, Envelope
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError

if TYPE_CHECKING:
    from src.plugin_runtime.host.authorization import AuthorizationManager

logger = get_logger("plugin_runtime.host.capability_service")

# 能力实现函数类型: (plugin_id, capability, args) -> result
CapabilityImpl = Callable[[str, str, Dict[str, Any]], Coroutine[Any, Any, Any]]


class CapabilityService:
    """能力服务

    负责：
    1. 注册能力实现
    2. 接收插件的能力调用请求
    3. 通过策略引擎校验权限和限流
    4. 执行实际操作并返回结果
    """

    def __init__(self, authorization: "AuthorizationManager") -> None:
        self._authorization = authorization
        # capability_name -> implementation
        self._implementations: Dict[str, CapabilityImpl] = {}

    def register_capability(self, name: str, impl: CapabilityImpl) -> None:
        """注册一个能力实现

        Args:
            name: 能力名称，如 "send.text", "db.query", "llm.generate"
            impl: 实现函数
        """
        self._implementations[name] = impl
        logger.debug(f"注册能力实现: {name}")

    async def handle_capability_request(self, envelope: Envelope) -> Envelope:
        """处理能力调用请求（作为 RPC Server 的 method handler）

        从 envelope 中提取 capability 名称和参数，
        校验权限后调用对应实现。
        """
        plugin_id = envelope.plugin_id

        try:
            req = CapabilityRequestPayload.model_validate(envelope.payload)
        except Exception as e:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, f"能力调用 payload 格式错误: {e}")

        capability = req.capability

        # 1. 权限校验
        allowed, reason = self._authorization.check_capability(plugin_id, capability)
        if not allowed:
            return envelope.make_error_response(ErrorCode.E_CAPABILITY_DENIED.value, reason)

        # 2. 查找实现
        impl = self._implementations.get(capability)
        if impl is None:
            return envelope.make_error_response(ErrorCode.E_METHOD_NOT_ALLOWED.value, f"未注册的能力: {capability}")

        # 3. 执行
        try:
            result = await impl(plugin_id, capability, req.args)
            resp_payload = CapabilityResponsePayload(success=True, result=result)
            return envelope.make_response(payload=resp_payload.model_dump())
        except RPCError as e:
            return envelope.make_error_response(e.code.value, e.message, e.details)
        except Exception as e:
            logger.error(f"能力 {capability} 执行异常: {e}", exc_info=True)
            return envelope.make_error_response(ErrorCode.E_CAPABILITY_FAILED.value, str(e))

    def list_capabilities(self) -> List[str]:
        """列出所有已注册的能力"""
        return list(self._implementations.keys())
