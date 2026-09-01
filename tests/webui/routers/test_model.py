from src.llm_models.exceptions import RespNotOkException
from src.webui.routers.model import _format_model_test_error


def test_format_model_test_http_error_keeps_upstream_detail_and_snapshot() -> None:
    upstream_error = ValueError("SDK 原始错误")
    error = RespNotOkException(
        400,
        'Error code: 400 | {"error":{"message":"tool_choice 参数不受支持"}}',
    )
    error.__cause__ = upstream_error
    error.request_snapshot_path = "C:/data/llm_request_snapshots/request.json"
    error.request_snapshot_replay_command = "uv run python scripts/replay_llm_request.py request.json"

    formatted = _format_model_test_error(error)

    assert "错误类型: RespNotOkException" in formatted
    assert "HTTP 状态码: 400" in formatted
    assert "错误摘要: 参数不正确" in formatted
    assert "tool_choice 参数不受支持" in formatted
    assert "底层异常: ValueError: SDK 原始错误" in formatted
    assert "调用完整信息" in formatted
    assert "replay_llm_request.py" in formatted


def test_format_model_test_generic_error_keeps_type_and_message() -> None:
    formatted = _format_model_test_error(RuntimeError("模型初始化失败"))

    assert formatted == "错误类型: RuntimeError\n错误摘要: 模型初始化失败"
