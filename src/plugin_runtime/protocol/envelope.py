"""RPC Envelope 消息模型

定义 Host 与 Runner 之间所有 RPC 消息的统一信封格式。
使用 Pydantic 进行 schema 定义与校验。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import logging as stdlib_logging
import time


# ====== 协议常量 ======
PROTOCOL_VERSION = "1.0.0"
# 支持的 SDK 版本范围（Host 在握手时校验）
MIN_SDK_VERSION = "1.0.0"
MAX_SDK_VERSION = "1.99.99"


# ====== 消息类型 ======
class MessageType(str, Enum):
    """RPC 消息类型"""

    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"


# ====== 请求 ID 生成器 ======
class RequestIdGenerator:
    """单调递增 int64 请求 ID 生成器"""

    def __init__(self, start: int = 1) -> None:
        self._counter = start

    async def next(self) -> int:
        current = self._counter
        self._counter += 1
        return current


# ====== Envelope 模型 ======
class Envelope(BaseModel):
    """RPC 统一消息封装

    所有 Host <-> Runner 消息均封装为此格式。
    序列化流程：Envelope -> .model_dump() -> MsgPack encode
    反序列化流程：MsgPack decode -> Envelope.model_validate(data)
    """

    protocol_version: str = Field(default=PROTOCOL_VERSION, description="协议版本")
    """协议版本"""
    request_id: int = Field(description="单调递增请求 ID")
    """单调递增请求 ID"""
    message_type: MessageType = Field(description="消息类型")
    """消息类型"""
    method: str = Field(default="", description="RPC 方法名")
    """RPC 方法名"""
    plugin_id: str = Field(default="", description="目标插件 ID")
    """目标插件 ID"""
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000), description="发送时间戳 (ms)")
    """发送时间戳 (ms)"""
    timeout_ms: int = Field(default=30000, description="相对超时 (ms)")
    """相对超时 (ms)"""
    payload: Dict[str, Any] = Field(default_factory=dict, description="业务数据")
    """业务数据"""
    error: Optional[Dict[str, Any]] = Field(default=None, description="错误信息 (仅 response)")
    """错误信息 (仅 response)"""

    def is_request(self) -> bool:
        return self.message_type == MessageType.REQUEST

    def is_response(self) -> bool:
        return self.message_type == MessageType.RESPONSE

    def is_broadcast(self) -> bool:
        return self.message_type == MessageType.BROADCAST

    def make_response(
        self, payload: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None
    ) -> "Envelope":
        """基于当前请求创建对应的响应信封"""
        return Envelope(
            protocol_version=self.protocol_version,
            request_id=self.request_id,
            message_type=MessageType.RESPONSE,
            method=self.method,
            plugin_id=self.plugin_id,
            payload=payload or {},
            error=error,
        )

    def make_error_response(self, code: str, message: str = "", details: Optional[Dict[str, Any]] = None) -> "Envelope":
        """基于当前请求创建错误响应"""
        return self.make_response(
            error={
                "code": code,
                "message": message,
                "details": details or {},
            }
        )


# ====== 握手请求与响应 ======
class HelloPayload(BaseModel):
    """runner.hello 握手请求 payload"""

    runner_id: str = Field(description="Runner 进程唯一标识")
    """Runner 进程唯一标识"""
    sdk_version: str = Field(description="SDK 版本号")
    """SDK 版本号"""
    session_token: str = Field(description="一次性会话令牌")
    """一次性会话令牌"""


class HelloResponsePayload(BaseModel):
    """runner.hello 握手响应 payload"""

    accepted: bool = Field(description="是否接受连接")
    """是否接受连接"""
    host_version: str = Field(default="", description="Host 版本号")
    """Host 版本号"""
    reason: str = Field(default="", description="拒绝原因 (若 accepted=False)")
    """拒绝原因 (若 `accepted`=`False`)"""


# ====== 组件注册消息 ======
class ComponentDeclaration(BaseModel):
    """单个组件声明"""

    name: str = Field(description="组件名称")
    """组件名称"""
    component_type: str = Field(
        description="组件类型：action/command/tool/event_handler/hook_handler/message_gateway"
    )
    """组件类型：`action`/`command`/`tool`/`event_handler`/`hook_handler`/`message_gateway`"""
    plugin_id: str = Field(description="所属插件 ID")
    """所属插件 ID"""
    metadata: Dict[str, Any] = Field(default_factory=dict, description="组件元数据")
    """组件元数据"""


class RegisterPluginPayload(BaseModel):
    """插件组件注册请求载荷。

    该模型同时用于 ``plugin.register_components`` 与兼容旧命名的
    ``plugin.register_plugin`` 请求。
    """

    plugin_id: str = Field(description="插件 ID")
    """插件 ID"""
    plugin_version: str = Field(default="1.0.0", description="插件版本")
    """插件版本"""
    components: List[ComponentDeclaration] = Field(default_factory=list, description="组件列表")
    """组件列表"""
    capabilities_required: List[str] = Field(default_factory=list, description="所需能力列表")
    """所需能力列表"""


class BootstrapPluginPayload(BaseModel):
    """plugin.bootstrap 请求 payload"""

    plugin_id: str = Field(description="插件 ID")
    """插件 ID"""
    plugin_version: str = Field(default="1.0.0", description="插件版本")
    """插件版本"""
    capabilities_required: List[str] = Field(default_factory=list, description="所需能力列表")
    """所需能力列表"""


# ====== 插件调用请求和响应 ======
class InvokePayload(BaseModel):
    """plugin.invoke.* 请求 payload"""

    component_name: str = Field(description="要调用的组件名称")
    """要调用的组件名称"""
    args: Dict[str, Any] = Field(default_factory=dict, description="调用参数")
    """调用参数"""


class InvokeResultPayload(BaseModel):
    """plugin.invoke.* 响应 payload"""

    success: bool = Field(description="是否成功")
    """是否成功"""
    result: Any = Field(default=None, description="返回值")
    """返回值"""


# ====== 能力调用消息 ======
class CapabilityRequestPayload(BaseModel):
    """cap.* 请求 payload（插件 -> Host 能力调用）"""

    capability: str = Field(description="能力名称，如 send.text, db.query")
    """能力名称，如 send.text, db.query"""
    args: Dict[str, Any] = Field(default_factory=dict, description="调用参数")
    """调用参数"""


class CapabilityResponsePayload(BaseModel):
    """cap.* 响应 payload"""

    success: bool = Field(description="是否成功")
    """是否成功"""
    result: Any = Field(default=None, description="返回值")
    """返回值"""


# ====== 健康检查 ======
class HealthPayload(BaseModel):
    """plugin.health 响应 payload"""

    healthy: bool = Field(description="是否健康")
    """是否健康"""
    loaded_plugins: List[str] = Field(default_factory=list, description="已加载的插件列表")
    """已加载的插件列表"""
    uptime_ms: int = Field(default=0, description="运行时长 (ms)")
    """运行时长 (ms)"""


class RunnerReadyPayload(BaseModel):
    """runner.ready 请求 payload"""

    loaded_plugins: List[str] = Field(default_factory=list, description="已完成初始化的插件列表")
    """已完成初始化的插件列表"""
    failed_plugins: List[str] = Field(default_factory=list, description="初始化失败的插件列表")
    """初始化失败的插件列表"""


# ====== 配置更新 ======
class ConfigUpdatedPayload(BaseModel):
    """plugin.config_updated 事件 payload"""

    plugin_id: str = Field(description="插件 ID")
    """插件 ID"""
    config_version: str = Field(description="新配置版本")
    """新配置版本"""
    config_data: Dict[str, Any] = Field(default_factory=dict, description="配置内容")
    """配置内容"""


# ====== 关停 ======
class ShutdownPayload(BaseModel):
    """plugin.shutdown / plugin.prepare_shutdown payload"""

    reason: str = Field(default="normal", description="关停原因")
    """关停原因"""
    drain_timeout_ms: int = Field(default=5000, description="排空超时 (ms)")
    """排空超时 (ms)"""


class UnregisterPluginPayload(BaseModel):
    """插件注销请求载荷。"""

    plugin_id: str = Field(description="插件 ID")
    """插件 ID"""
    reason: str = Field(default="manual", description="注销原因")
    """注销原因"""


class ReloadPluginPayload(BaseModel):
    """插件重载请求载荷。"""

    plugin_id: str = Field(description="目标插件 ID")
    """目标插件 ID"""
    reason: str = Field(default="manual", description="重载原因")
    """重载原因"""


class ReloadPluginResultPayload(BaseModel):
    """插件重载结果载荷。"""

    success: bool = Field(description="是否重载成功")
    """是否重载成功"""
    requested_plugin_id: str = Field(description="请求重载的插件 ID")
    """请求重载的插件 ID"""
    reloaded_plugins: List[str] = Field(default_factory=list, description="成功完成重载的插件列表")
    """成功完成重载的插件列表"""
    unloaded_plugins: List[str] = Field(default_factory=list, description="本次已卸载的插件列表")
    """本次已卸载的插件列表"""
    failed_plugins: Dict[str, str] = Field(default_factory=dict, description="重载失败的插件及原因")
    """重载失败的插件及原因"""


# ====== 日志传输 ======


class LogEntry(BaseModel):
    """单条日志记录（Runner → Host 传输格式）"""

    timestamp_ms: int = Field(description="日志时间戳，Unix epoch 毫秒")
    """日志时间戳，Unix epoch 毫秒"""
    level: int = Field(description="stdlib logging 整数级别：10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR, 50=CRITICAL")
    """stdlib logging 整数级别：10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR, 50=CRITICAL"""
    logger_name: str = Field(description="Logger 名称，如 plugin.my_plugin.submodule")
    """Logger 名称，如 plugin.my_plugin.submodule"""
    message: str = Field(description="经 Formatter 格式化后的完整日志消息（含 exc_info 文本）")
    """经 Formatter 格式化后的完整日志消息（含 exc_info 文本）"""
    exception_text: str = Field(
        default="",
        description="原始异常摘要（exc_text），供结构化消费；已嵌入 message 中",
    )
    """原始异常摘要（exc_text），供结构化消费；已嵌入 message 中"""
    log_color_in_hex: Optional[str] = Field(default=None, description="日志颜色的十六进制字符串（如 #RRGGBB）")

    @property
    def levelname(self) -> str:
        """返回对应的 stdlib logging 级别名称（如 'INFO'）。"""
        return stdlib_logging.getLevelName(self.level)


class LogBatchPayload(BaseModel):
    """runner.log_batch 事件 payload：Runner 端向 Host 批量推送日志记录"""

    entries: List[LogEntry] = Field(description="本批次日志记录列表，按时间升序排列")
    """本批次日志记录列表，按时间升序排列"""
