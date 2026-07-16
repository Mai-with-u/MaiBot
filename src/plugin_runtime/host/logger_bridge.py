from typing import Optional

import logging as stdlib_logging

from src.plugin_runtime.protocol.errors import ErrorCode
from src.plugin_runtime.protocol.envelope import Envelope, LogBatchPayload


class RunnerLogBridge:
    """将 Runner 进程上报的批量日志重放到主进程的 Logger 中。

    Runner 通过 ``runner.log_batch`` IPC 事件批量到达。
    每条 LogEntry 被重建为一个真实的 :class:`logging.LogRecord` 并直接
    Runner 内部日志可按运行时分组重命名后接入主进程已配置好的
    structlog Handler 链；插件自身日志保留原始 Logger 名称。
    """

    def __init__(self, runtime_logger_name: Optional[str] = None) -> None:
        """初始化 Runner 日志桥接器。

        Args:
            runtime_logger_name: Runner 内部运行时日志在 Host 侧使用的 Logger 名称。
                插件自身日志仍保留原始 Logger 名称。
        """

        self._runtime_logger_name = runtime_logger_name

    async def handle_log_batch(self, envelope: Envelope) -> Envelope:
        """IPC 事件处理器：解析批量日志并重放到主进程 Logger。

        Args:
            envelope: 方法名为 ``runner.log_batch`` 的 IPC 事件信封。

        Returns:
            空响应信封（事件模式下将被忽略）。
        """
        try:
            batch = LogBatchPayload.model_validate(envelope.payload)
        except Exception as exc:
            return envelope.make_error_response(ErrorCode.E_BAD_PAYLOAD.value, str(exc))

        for entry in batch.entries:
            logger_name = entry.logger_name
            if self._runtime_logger_name is not None and logger_name.startswith("plugin_runtime."):
                logger_name = self._runtime_logger_name

            # 重建一个与原始日志尽量相符的 LogRecord
            record = stdlib_logging.LogRecord(
                name=logger_name,
                level=entry.level,
                pathname="<runner>",
                lineno=0,
                msg=entry.message,
                args=(),
                exc_info=None,
            )
            record.created = entry.timestamp_ms / 1000.0
            record.msecs = entry.timestamp_ms % 1000
            if entry.exception_text:
                record.exc_text = entry.exception_text

            stdlib_logging.getLogger(logger_name).handle(record)

        return envelope.make_response(payload={"accepted": True, "count": len(batch.entries)})
