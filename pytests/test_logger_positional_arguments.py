from pathlib import Path

import json
import os
import subprocess
import sys


def test_positional_arguments_are_formatted_in_json_logs(tmp_path: Path) -> None:
    # 在子进程中加载真实日志配置，避免重配 pytest 所在进程的全局日志。
    script = """
from io import StringIO
import logging

from src.common.logger import file_formatter, get_logger

output = StringIO()
handler = logging.StreamHandler(output)
handler.setFormatter(file_formatter)
target = logging.getLogger("positional-arguments-test")
target.handlers = [handler]
target.propagate = False
target.setLevel(logging.INFO)
logger = get_logger("positional-arguments-test")
logger.warning("value=%s", 42)
try:
    raise ValueError("broken")
except ValueError as exc:
    logger.exception("operation failed: %s", exc)
print(output.getvalue(), end="")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "MAIBOT_INSTANCE_DIR": str(tmp_path),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    warning, error = [json.loads(line) for line in result.stdout.splitlines()]
    assert warning["event"] == "value=42"
    assert error["event"] == "operation failed: broken"
    assert "ValueError: broken" in error["exception"]
    assert "positional_args" not in warning
    assert "positional_args" not in error
