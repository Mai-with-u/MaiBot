"""为 WebUI 答疑 Agent 提供有界的运行诊断投影。"""

from contextlib import closing
from hashlib import sha256
from heapq import nlargest
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Tuple
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import asyncio
import json
import math
import os
import re
import sqlite3

from src.common.logger import LOG_DIR, get_logger
from src.common.paths import get_instance_root
from src.llm_models.payload_content.tool_option import ToolDefinitionInput

DIAGNOSTIC_TOOL_NAMES = frozenset({"read_recent_logs", "list_recent_reasoning", "read_reasoning_record"})
_LOG_LEVELS = ("WARNING", "ERROR", "CRITICAL")
_LOG_FILE_COUNT = 3
_LOG_TAIL_BYTES = 128 * 1024
_LOG_MESSAGE_CHARS = 400
_REASONING_SCAN_LIMIT = 48
_REASONING_MAX_BYTES = 256 * 1024
_REASONING_METADATA_BYTES = 16 * 1024
_REASONING_ROOT = get_instance_root() / "logs" / "maisaka_prompt"
# 推理预览写入器维护该索引。诊断读取无需加载 Maisaka 的显示运行时。
_REASONING_INDEX_NAME = ".reasoning-steps.sqlite3"
_LOG_FILENAME = re.compile(r"app_\d{8}_\d{6}\.log\.jsonl")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,96}")
_TIMESTAMP = re.compile(r"[0-9TtZz:+., -]{1,40}")
_METADATA_KEY = re.compile(r'^  "metadata"\s*:\s*', flags=re.MULTILINE)
_URL = re.compile(r"https?://[^\s<>\"']+", flags=re.IGNORECASE)
_HTTP_STATUS = re.compile(r"\b(?:HTTP(?:/[0-9.]+)?\s+|status(?:_code)?\s*[:=]\s*)([1-5]\d{2})\b", re.I)
_PRIVATE_LOG_TEXT = re.compile(
    r"(?<![a-z0-9])(?:authorization|api[\s_-]*key|password|passwd|cookie|secret|"
    r"(?:access|refresh|auth)[\s_-]*token|(?:bearer|basic)\s+\S+|"
    r"(?:sk|rk|pk)-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{12,}|AKIA[A-Z0-9]{16}|eyJ[a-z0-9_-]+\.[a-z0-9_-]+\.|"
    r"(?:prompt|request[\s_-]*body|(?:user|chat)[\s_-]*message|token|messages|"
    r"request_items|output_items|reasoning_content)['\"]?\s*[:=])"
    r"|<(?:message|prompt)\b|密钥|密码|口令|令牌|凭据|消息正文|消息内容|请求正文",
    flags=re.IGNORECASE,
)
_REASONING_STATUS = {
    "failed",
    "final_failed",
    "retrying",
    "switching_model",
    "succeeded",
    "succeeded_after_retry",
    "cancelled",
}
logger = get_logger("webui.ai_help_diagnostics")


class RecentLogsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=8, ge=1, le=12)
    level: Literal["WARNING", "ERROR", "CRITICAL"] = Field(default="WARNING", description="Minimum log level")


class RecentReasoningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=6, ge=1, le=10)


class ReasoningRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{24}$", description="ID from list_recent_reasoning")
    record_path: str | None = Field(
        default=None, min_length=1, max_length=512, description="Exact relative .json path explicitly given by the user"
    )

    @model_validator(mode="after")
    def validate_selector(self) -> "ReasoningRecordRequest":
        if (self.record_id is None) == (self.record_path is None):
            raise ValueError("必须提供且只提供一种推理记录标识")
        return self


def build_diagnostic_tools() -> List[ToolDefinitionInput]:
    return [
        {
            "name": "read_recent_logs",
            "description": "Read bounded and redacted recent warnings/errors from this MaiBot instance.",
            "parameters": RecentLogsRequest.model_json_schema(),
        },
        {
            "name": "list_recent_reasoning",
            "description": "List recent MaiBot reasoning records with opaque IDs and stages.",
            "parameters": RecentReasoningRequest.model_json_schema(),
        },
        {
            "name": "read_reasoning_record",
            "description": (
                "Inspect the model, timing, tokens, tool names and outcome of one reasoning record. "
                "Use an ID from the recent list or an exact relative .json path supplied by the user."
            ),
            "parameters": ReasoningRecordRequest.model_json_schema(),
        },
    ]


def _json_rows_from_tail(path: Path) -> Iterator[Dict[str, Any]]:
    """只读尾部固定字节数，避开轮转文件开头或被截断的首行。"""

    with path.open("rb") as stream:
        start = max(0, stream.seek(0, os.SEEK_END) - _LOG_TAIL_BYTES)
        stream.seek(start)
        content = stream.read(_LOG_TAIL_BYTES)
    if start:
        content = content.partition(b"\n")[2]
    for line in reversed(content.splitlines()):
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(item, dict):
            yield item


def _safe_identifier(value: Any) -> str:
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value) and not _PRIVATE_LOG_TEXT.search(value):
        return value
    return ""


def _url_origin(match: re.Match[str]) -> str:
    try:
        url = urlsplit(match[0])
        host, port = url.hostname, url.port
    except ValueError:
        return "[链接已隐藏]"
    if not host or _PRIVATE_LOG_TEXT.search(host):
        return "[链接已隐藏]"
    host = f"[{host}]" if ":" in host else host
    return f"{url.scheme}://{host}:{port}" if port is not None else f"{url.scheme}://{host}"


def redact_diagnostic_message(message: str) -> str:
    message = _URL.sub(_url_origin, message)
    # 日志自由文本不能按 JSON 字段逐个清洗，含凭据或正文标记的整条省略。
    if _PRIVATE_LOG_TEXT.search(message):
        return "[涉及凭据或消息正文的日志内容已隐藏]"
    return message.strip().partition("\n")[0][:_LOG_MESSAGE_CHARS]


def _read_recent_logs(request: RecentLogsRequest) -> Dict[str, Any]:
    root = LOG_DIR.resolve()
    files = nlargest(
        _LOG_FILE_COUNT, (path for path in root.glob("app_*.log.jsonl") if _LOG_FILENAME.fullmatch(path.name))
    )
    entries: List[Dict[str, Any]] = []
    levels = _LOG_LEVELS[_LOG_LEVELS.index(request.level) :]
    for path in files:
        if path.is_symlink():
            continue
        try:
            for item in _json_rows_from_tail(path):
                level, message = item.get("level"), item.get("event")
                if not isinstance(level, str) or level.upper() not in levels or not isinstance(message, str):
                    continue
                # 只投影已知字段，避免请求、聊天内容、异常堆栈等结构化附加数据进入模型。
                entry: Dict[str, Any] = {"level": level.upper(), "message": redact_diagnostic_message(message)}
                timestamp = item.get("timestamp")
                if isinstance(timestamp, str) and _TIMESTAMP.fullmatch(timestamp):
                    entry["timestamp"] = timestamp
                module = _safe_identifier(item.get("logger_name")) or _safe_identifier(item.get("module"))
                if module:
                    entry["module"] = module
                if status := _HTTP_STATUS.search(message):
                    entry["http_status"] = int(status[1])
                entries.append(entry)
                if len(entries) == request.limit:
                    return {"logs": entries}
        except FileNotFoundError:
            # 读取期间可能正好轮转清理。
            continue
    return {"logs": entries}


def _recent_reasoning_files() -> Iterator[Tuple[str, str, Path]]:
    root = _REASONING_ROOT.resolve()
    index = root / _REASONING_INDEX_NAME
    if index.is_symlink() or not index.is_file():
        return
    # rowid 顺序来自预览写入器；仅查看最近的索引行，无需扫描所有聊天目录。
    with closing(sqlite3.connect(f"{index.as_uri()}?mode=ro", uri=True, timeout=1.0)) as connection:
        rows = connection.execute(
            "SELECT stage, session, stem FROM steps ORDER BY rowid DESC LIMIT ?", (_REASONING_SCAN_LIMIT,)
        ).fetchall()
    for stage, session, stem in rows:
        if not _safe_identifier(stage) or any(
            not isinstance(part, str) or part in {"", ".", ".."} or any(char in part for char in "/\\:")
            for part in (stage, session, stem)
        ):
            continue
        relative = Path(stage, session, f"{stem}.json")
        path = root / relative
        if path.resolve() != path or not path.is_file():
            continue
        record_id = _record_id(relative)
        yield record_id, stage, path


def _record_id(relative_path: Path) -> str:
    return sha256(relative_path.as_posix().encode()).hexdigest()[:24]


def _read_snapshot(path: Path) -> Dict[str, Any]:
    with path.open("rb") as stream:
        content = stream.read(_REASONING_MAX_BYTES + 1)
    if len(content) <= _REASONING_MAX_BYTES:
        snapshot = json.loads(content)
        if not isinstance(snapshot, dict):
            raise ValueError("推理记录格式错误")
        return snapshot
    # 大文件只提取 v6 快照头部的元数据，跳过请求和模型输出。
    head = content[:_REASONING_METADATA_BYTES].decode("utf-8", errors="ignore")
    match = _METADATA_KEY.search(head)
    try:
        metadata = json.JSONDecoder().raw_decode(head, match.end())[0] if match else {}
    except json.JSONDecodeError:
        # 头部读取有限，元数据本身也可能跨过该边界。
        metadata = {}
    return {"metadata": metadata, "partial": True}


def _snapshot_projection(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    request = snapshot.get("request")
    if isinstance(request, dict) and (kind := _safe_identifier(request.get("kind"))):
        result["request_kind"] = kind
    metadata = snapshot.get("metadata")
    if isinstance(metadata, dict):
        if model := _safe_identifier(metadata.get("model_name") or metadata.get("model")):
            result["model"] = model
        status = metadata.get("status")
        if isinstance(status, str) and status in _REASONING_STATUS:
            result["status"] = status
        duration = metadata.get("duration_ms")
        if type(duration) in {int, float} and 0 <= duration < 86_400_000 and math.isfinite(duration):
            result["duration_ms"] = duration
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = metadata.get(name)
            if type(value) is int and 0 <= value <= 1_000_000_000:
                result[name] = value
        source = metadata.get("request_source")
        if source in ("context_injection", "planner_snapshot"):
            result["source"] = source
    output_items = snapshot.get("output_items")
    tools: Dict[str, Any] = {}
    if isinstance(output_items, list):
        if result.get("source") == "context_injection":
            # 记忆/画像记录来自宿主注入，没有模型名。只报告是否有注入，不发送注入正文。
            result["has_injected_context"] = any(
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
                and part["text"].strip() not in {"", "本轮没有注入内容。"}
                for item in output_items
                if isinstance(item, dict) and isinstance(item.get("parts"), list)
                for part in item["parts"]
            )
        for item in output_items:
            if not isinstance(item, dict):
                continue
            if item.get("item_type") == "FunctionCallItem" and isinstance(item.get("tool_call"), dict):
                name = _safe_identifier(item["tool_call"].get("func_name"))
                if name:
                    tools.setdefault(name, {"name": name})
            if item.get("item_type") == "FunctionCallOutputItem":
                name = _safe_identifier(item.get("tool_name"))
                if name and isinstance(item.get("success"), bool):
                    tools[name] = {"name": name, "success": item["success"]}
    if tools:
        result["tools"] = list(tools.values())[:6]
    attempts = snapshot.get("generation_attempts")
    if isinstance(attempts, list) and attempts:
        result["attempt_count"] = len(attempts)
        latest = attempts[-1]
        if isinstance(latest, dict):
            if model := _safe_identifier(latest.get("model")):
                result.setdefault("model", model)
            status = latest.get("status")
            if isinstance(status, str) and status in _REASONING_STATUS:
                result.setdefault("status", status)
        for attempt in reversed(attempts):
            error = attempt.get("error") if isinstance(attempt, dict) else None
            if not isinstance(error, dict):
                continue
            outcome: Dict[str, Any] = {}
            if error_type := _safe_identifier(error.get("type")):
                outcome["type"] = error_type
            code = error.get("status_code")
            if type(code) is int and 100 <= code <= 599:
                outcome["http_status"] = code
            if outcome:
                result["last_error"] = outcome
                break
    return result


def _read_reasoning_record(request: ReasoningRecordRequest) -> Dict[str, Any]:
    if request.record_path is not None:
        from src.webui.routers.reasoning_process import PROMPT_LOG_ROOT, _resolve_prompt_log_path

        try:
            path = _resolve_prompt_log_path(request.record_path, {".json"})
        except HTTPException as exc:
            return {"error": str(exc.detail)}
        relative = path.relative_to(PROMPT_LOG_ROOT)
        if len(relative.parts) != 3:
            return {"error": "推理记录路径必须包含类型、会话和 JSON 文件名"}
        return _project_reasoning_record(_record_id(relative), _safe_identifier(relative.parts[0]), path)

    for record_id, stage, path in _recent_reasoning_files():
        if record_id == request.record_id:
            return _project_reasoning_record(record_id, stage, path)
    return {"error": "推理记录未找到或已移出最近记录范围"}


def _project_reasoning_record(record_id: str, stage: str, path: Path) -> Dict[str, Any]:
    snapshot = _read_snapshot(path)
    record = {"id": record_id, "stage": stage, **_snapshot_projection(snapshot)}
    if snapshot.get("partial") is True:
        record["partial"] = True

    events_path = path.with_suffix(".events.jsonl")
    if stage == "llm_error" and events_path.is_file() and not events_path.is_symlink():
        # 请求快照不随重试改写，最终状态以追加的诊断事件为准。
        for event in _json_rows_from_tail(events_path):
            event_type, status = event.get("type"), event.get("status")
            if event_type == "attempt_status" and isinstance(status, str) and status in _REASONING_STATUS:
                record["status"] = status
                break
            latest = event.get("snapshot") if event_type == "failed_attempt" else event
            if event_type in ("failed_attempt", "succeeded") and isinstance(latest, dict):
                record.update(_snapshot_projection(latest))
                break
    return {"record": record}


def _list_recent_reasoning(request: RecentReasoningRequest) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    for record_id, stage, path in _recent_reasoning_files():
        try:
            recorded_at = path.stat().st_mtime
        except FileNotFoundError:
            continue
        records.append({"id": record_id, "stage": stage, "timestamp": recorded_at})
        if len(records) == request.limit:
            break
    return {"records": records}


async def execute_diagnostic_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """校验模型参数，在工作线程内读取并投影诊断证据。"""

    try:
        if name == "read_recent_logs":
            return await asyncio.to_thread(_read_recent_logs, RecentLogsRequest.model_validate(arguments))
        if name == "list_recent_reasoning":
            return await asyncio.to_thread(_list_recent_reasoning, RecentReasoningRequest.model_validate(arguments))
        if name == "read_reasoning_record":
            return await asyncio.to_thread(_read_reasoning_record, ReasoningRecordRequest.model_validate(arguments))
    except ValidationError:
        return {"error": "诊断工具参数无效"}
    except (OSError, sqlite3.Error, ValueError, RecursionError) as exc:
        logger.warning("答疑诊断记录读取失败", tool=name, error_type=type(exc).__name__)
        return {"error": "暂时无法读取诊断记录"}
    return {"error": "未知答疑诊断工具"}
