"""RPC 错误码定义

所有 Host 与 Runner 之间的 RPC 通信使用统一的错误码体系。
"""

from enum import Enum


class ErrorCode(str, Enum):
    """RPC 错误码枚举"""

    # 通用
    OK = "OK"
    E_UNKNOWN = "E_UNKNOWN"

    # 协议层
    E_TIMEOUT = "E_TIMEOUT"
    E_BAD_PAYLOAD = "E_BAD_PAYLOAD"
    E_PROTOCOL_MISMATCH = "E_PROTOCOL_MISMATCH"

    # 权限与策略
    E_UNAUTHORIZED = "E_UNAUTHORIZED"
    E_METHOD_NOT_ALLOWED = "E_METHOD_NOT_ALLOWED"
    E_BACKPRESSURE = "E_BACKPRESSURE"
    E_HOST_OVERLOADED = "E_HOST_OVERLOADED"

    # 插件生命周期
    E_PLUGIN_CRASHED = "E_PLUGIN_CRASHED"
    E_PLUGIN_NOT_FOUND = "E_PLUGIN_NOT_FOUND"
    E_GENERATION_MISMATCH = "E_GENERATION_MISMATCH"
    E_RELOAD_IN_PROGRESS = "E_RELOAD_IN_PROGRESS"

    # 能力调用
    E_CAPABILITY_DENIED = "E_CAPABILITY_DENIED"
    E_CAPABILITY_FAILED = "E_CAPABILITY_FAILED"


class RPCError(Exception):
    """RPC 调用异常"""

    def __init__(self, code: ErrorCode, message: str = "", details: dict | None = None):
        self.code = code
        self.message = message or code.value
        self.details = details or {}
        super().__init__(f"[{code.value}] {self.message}")

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RPCError":
        code = ErrorCode(data.get("code", "E_UNKNOWN"))
        return cls(
            code=code,
            message=data.get("message", ""),
            details=data.get("details", {}),
        )
