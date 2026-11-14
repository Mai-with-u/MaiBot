"""在新解释器中验证安装版入口的实例目录初始化顺序。"""

from pathlib import Path
from typing import List

import os
import subprocess
import sys
import textwrap

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("entry", ["bootstrap", "host-list", "host-help"])
def test_packaged_entry_prepares_directory_before_business_import(tmp_path: Path, entry: str) -> None:
    environment = os.environ.copy()
    environment.pop("MAIBOT_INSTANCE_DIR", None)
    script = """
        from pathlib import Path
        import importlib.abc
        import os
        import sys

        sys.path.insert(0, sys.argv[1])
        from src.common import paths, version
        paths.PACKAGED_INSTALL = True
        version.read_project_version = lambda: "1.0.0"
        root = Path(sys.argv[2]).resolve()
        entry = sys.argv[3]

        class DirectoryReady(Exception):
            pass

        class CheckBusinessImport(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in {"src.host.layout", "src.common.logger", "src.host.adopt"}:
                    assert os.environ.get(paths.INSTANCE_DIRECTORY_ENV) == str(root)
                    raise DirectoryReady(fullname)

        sys.meta_path.insert(0, CheckBusinessImport())
        if entry == "host-help":
            from src.cli.tool import main
            try:
                main(["host", "--help"])
            except SystemExit as exc:
                assert exc.code == 0
            else:
                raise AssertionError("help did not exit")
            assert paths.INSTANCE_DIRECTORY_ENV not in os.environ
        else:
            try:
                if entry == "bootstrap":
                    from src.host.bootstrap import main
                    main(root)
                else:
                    from src.cli.tool import main
                    main(["host", "list", "--directory", str(root)])
            except DirectoryReady:
                pass
            else:
                raise AssertionError("business import was not reached")
    """
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(script), str(PROJECT_ROOT), str(tmp_path), entry],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("previous_directory", [None, "another-workspace"])
def test_launcher_supplies_directory_to_start_update_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous_directory: str | None
) -> None:
    from src.host import launcher

    if previous_directory is None:
        monkeypatch.delenv("MAIBOT_INSTANCE_DIR", raising=False)
    else:
        monkeypatch.setenv("MAIBOT_INSTANCE_DIR", previous_directory)
    stages = []

    def command(stage: str, root: Path) -> List[str]:
        stages.append(stage)
        code = launcher.UPDATE_REQUESTED if len(stages) == 1 else 0
        # 实际启动新解释器，确保目录在 Python 执行任何业务代码之前已可见。
        script = (
            "import os, sys; "
            "from pathlib import Path; "
            "assert Path(os.environ['MAIBOT_INSTANCE_DIR']) == Path(sys.argv[1]); "
            f"sys.exit({code})"
        )
        return [sys.executable, "-I", "-c", script, str(root)]

    monkeypatch.setattr(launcher, "_command", command)
    assert launcher.main(tmp_path) == 0
    assert stages == ["bootstrap", "updater", "bootstrap"]
    assert os.environ.get("MAIBOT_INSTANCE_DIR") == previous_directory
