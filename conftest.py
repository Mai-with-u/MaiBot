"""测试以 Worker 身份运行在独立实例目录中：不读取或升级开发者真实的 config/ 与 data/。"""

from pathlib import Path

import json
import os
import shutil
import tempfile

# 必须在导入任何 src 模块之前设置：配置与路径模块在导入时固定实例目录。
_INSTANCE_DIRECTORY = Path(tempfile.mkdtemp(prefix="maibot-test-instance-"))
os.environ["MAIBOT_INSTANCE_DIR"] = str(_INSTANCE_DIRECTORY)
# 与宿主下发给 Worker 的运行契约相同；需要验证"没有契约"的测试自行移除。
os.environ["MAIBOT_RUNTIME_CONTEXT"] = json.dumps({
    "bot_id": "0" * 32,
    "http_endpoint": str(_INSTANCE_DIRECTORY / "test-http.sock"),
    "public_base_url": "http://127.0.0.1:8001",
    "status_path": str(_INSTANCE_DIRECTORY / "host-status.json"),
})


def pytest_unconfigure(config) -> None:
    shutil.rmtree(_INSTANCE_DIRECTORY, ignore_errors=True)
