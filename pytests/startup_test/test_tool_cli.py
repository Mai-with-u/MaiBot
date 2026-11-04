"""在独立目录验证 CLI 不依赖仓库工作目录，初始化不会覆盖用户配置。"""

from pathlib import Path
from typing import List

import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def invoke(directory: Path, args: List[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("MAIBOT_INSTANCE_DIR", None)
    env.pop("MAIBOT_INITIALIZE_ONLY", None)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "src.cli.tool", *args],
        cwd=directory, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def test_help_and_version_do_not_initialize(tmp_path: Path) -> None:
    for args in (["--help"], ["--version"]):
        result = invoke(tmp_path, args)
        assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())


def test_init_preserves_existing_files_and_does_not_create_database(tmp_path: Path) -> None:
    instance = tmp_path / "麦麦 instance"
    result = invoke(tmp_path, ["init", "-d", str(instance)])
    assert result.returncode == 0, result.stderr
    bot_config = instance / "config" / "bot_config.toml"
    model_config = instance / "config" / "model_config.toml"
    bot_config.write_bytes(bot_config.read_bytes() + b'\n# user setting\n')
    before = (bot_config.read_bytes(), model_config.read_bytes())
    result = invoke(tmp_path, ["init", "-d", str(instance)])
    assert result.returncode == 0, result.stderr
    assert before == (bot_config.read_bytes(), model_config.read_bytes())
    assert not (instance / "data" / "MaiBot.db").exists()
    assert not (instance / "eula.confirmed").exists()
    for name in ("data", "logs", "plugins", "temp"):
        assert (instance / name).is_dir()


def test_run_refuses_directory_moved_into_workspace(tmp_path: Path) -> None:
    # adopt --mode move 之后的旧位置不能被当作空目录，初始化出一个空 Bot。
    (tmp_path / "MOVED_TO_WORKSPACE.txt").write_text("D:/workspace/bots/abc", encoding="utf-8")
    result = invoke(tmp_path, ["run"])
    assert result.returncode == 2
    assert "D:/workspace/bots/abc" in result.stderr
    assert [path.name for path in tmp_path.iterdir()] == ["MOVED_TO_WORKSPACE.txt"]
