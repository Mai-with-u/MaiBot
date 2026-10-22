"""uv 安装版更新；也可复制后交给环境外的 Python 独立执行，只依赖标准库。"""

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib

PYPI_URL = "https://pypi.org/pypi/maibot/json"
PYPI_INDEX = "https://pypi.org/simple"
PACKAGE_INDEXES: Dict[str, Tuple[str, str]] = {
    "pypi": ("PyPI 官方", PYPI_INDEX),
    "tuna": ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    "aliyun": ("阿里镜像", "https://mirrors.aliyun.com/pypi/simple"),
}
PACKAGE_RUNNER_ENV = "MAIBOT_PACKAGE_RUNNER"
UPDATE_TIMEOUT = 1800
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class SelfUpdateError(Exception):
    """自更新流程中的可预期失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def package_index(source_id: str) -> str:
    if source_id not in PACKAGE_INDEXES:
        raise SelfUpdateError("invalid_source", "包安装更新只支持 PyPI 官方、清华或阿里")
    return PACKAGE_INDEXES[source_id][1]


def version_tuple(version: str) -> Tuple[int, ...]:
    if _VERSION.fullmatch(version) is None:
        raise SelfUpdateError("invalid_version", "只能更新到形如 1.3.9 的正式版本")
    return tuple(int(part) for part in version.split("."))


@dataclass
class ToolEnvironment:
    prefix: Path
    python: Path
    base_python: Path
    uv: str

    @property
    def locks(self) -> Path:
        # 放在工具环境之外，uv 同步环境时不会影响实例锁。
        return self.prefix.parent / ".maibot-instance-locks"


def tool_environment() -> ToolEnvironment:
    """只接受当前解释器所属的 uv tool 环境，不猜测全局工具位置。"""
    prefix = Path(sys.prefix).resolve()
    receipt_path = prefix / "uv-receipt.toml"
    uv = shutil.which("uv")
    if uv is None:
        raise SelfUpdateError("uv_missing", "未找到 uv，请将 uv 加入 MaiBot 启动环境的 PATH")
    if not receipt_path.is_file() or prefix.name.lower() != "maibot":
        raise SelfUpdateError("not_uv_tool", "当前安装不属于 uv tool 环境，请使用原安装工具更新")
    receipt = tomllib.loads(receipt_path.read_text(encoding="utf-8"))["tool"]
    requirements = receipt["requirements"]
    requirement = next((item for item in requirements if item.get("name", "").lower() == "maibot"), None)
    if requirement is None or len(requirements) != 1 or set(requirement) - {"name", "specifier"}:
        raise SelfUpdateError("not_uv_tool", "本地 wheel、URL 或可编辑安装请先切换为 PyPI 工具安装")
    if any(receipt.get(key) for key in ("constraints", "overrides", "build-constraints")):
        raise SelfUpdateError("not_uv_tool", "带额外依赖约束的工具环境请手动更新")
    # Worker 与普通第三方启动器不能自行更新正在使用的环境，必须经过本程序的 Runner。
    if os.environ.get(PACKAGE_RUNNER_ENV) != "1":
        raise SelfUpdateError("package_runner_missing", "请通过 maibot run 启动，才能自动更新并重启")
    base_python = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    if not base_python.is_file() or base_python.resolve().is_relative_to(prefix):
        raise SelfUpdateError("not_uv_tool", "找不到工具环境之外的 Python，无法执行独立更新")
    return ToolEnvironment(prefix, Path(sys.executable), base_python, uv)


class FileLock:
    """进程退出即释放的跨平台文件锁；陈旧锁文件不会被误认为仍有实例。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise

    def close(self) -> None:
        self.file.close()

    def __enter__(self) -> "FileLock":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def instance_lock_path(environment: ToolEnvironment, root: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()
    return environment.locks / f"{key}.lock"


def ensure_other_instances_stopped(environment: ToolEnvironment, root: Path) -> None:
    for path in environment.locks.glob("*.lock"):
        if path == instance_lock_path(environment, root):
            continue
        try:
            with FileLock(path):
                pass
        except OSError as exc:
            raise SelfUpdateError("other_instances_running", "同一工具环境还有其他实例运行，请先停止它们再更新") from exc


def register_instance(root: Path) -> FileLock:
    """Runner 持有实例租约；更新锁关闭新实例启动与更新之间的竞态窗口。"""
    prefix = Path(sys.prefix).resolve()
    environment = ToolEnvironment(prefix, Path(sys.executable), Path(sys.executable), "")
    try:
        with FileLock(environment.locks / "update.guard"):
            return FileLock(instance_lock_path(environment, root))
    except OSError as exc:
        raise SelfUpdateError("package_busy", "这个实例正在运行，或工具环境正在更新，请等待完成") from exc


class _PackageLinks(HTMLParser):
    """从 Python Simple 包源读取正式版本，保留撤回标记。"""

    def __init__(self) -> None:
        super().__init__()
        self.releases: Dict[str, List[Dict[str, Any]]] = {}

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Any]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        filename = unquote(urlsplit(attributes.get("href", "")).path).rsplit("/", 1)[-1]
        match = re.fullmatch(r"maibot-(\d+\.\d+\.\d+)(?:\.tar\.gz|-.*\.whl)", filename, re.IGNORECASE)
        if match and _VERSION.fullmatch(match[1]):
            self.releases.setdefault(match[1], []).append({"yanked": "data-yanked" in attributes})


def fetch_releases(source_id: str = "pypi") -> Dict[str, Any]:
    index = package_index(source_id)
    url = PYPI_URL if source_id == "pypi" else f"{index}/maibot/"
    try:
        request = Request(url, headers={
            "User-Agent": "MaiBot-Self-Update",
            "Accept": "application/json" if source_id == "pypi" else "text/html",
        })
        with urlopen(request, timeout=5) as response:
            if source_id == "pypi":
                return json.load(response)
            parser = _PackageLinks()
            parser.feed(response.read().decode("utf-8"))
            if not parser.releases:
                raise ValueError("包源没有可识别的正式版本")
            return {"releases": parser.releases}
    except (OSError, URLError, ValueError) as exc:
        raise SelfUpdateError("fetch_failed", f"无法从 {PACKAGE_INDEXES[source_id][0]} 检查正式版本：{exc}") from exc


def release_exists(payload: Dict[str, Any], version: str) -> bool:
    files = payload["releases"].get(version, [])
    return any(not item.get("yanked", False) for item in files)


def latest_release(current: str, source_id: str = "pypi") -> str:
    payload = fetch_releases(source_id)
    candidates = [
        version for version in payload["releases"]
        if _VERSION.fullmatch(version)
    ]
    # 只接受有可下载、未撤回文件的正式版；不依赖可能指向预发布的 info.version。
    available = [version for version in candidates if release_exists(payload, version)]
    return max([current, *available], key=version_tuple)


def _run(command: List[str], root: Path, env: Dict[str, str]) -> str:
    result = subprocess.run(
        command, cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=UPDATE_TIMEOUT,
    )
    if result.returncode:
        raise SelfUpdateError("package_upgrade_failed", (result.stderr or result.stdout)[-4000:])
    return result.stdout.strip()


def backup_instance(root: Path) -> Path:
    """业务进程停止后备份配置与 SQLite；备份自身不进入后续快照。"""
    backup = root / "data/self_update/backups" / str(time.time_ns())
    backup.mkdir(parents=True)
    if (root / "config").is_dir():
        shutil.copytree(root / "config", backup / "config")
    for path in (root / "data").rglob("*"):
        if not path.is_file() or path.suffix not in {".db", ".sqlite", ".sqlite3"}:
            continue
        if path.is_relative_to(root / "data/self_update"):
            continue
        destination = backup / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(path)) as source, sqlite3.connect(str(destination)) as target:
            source.backup(target)
    return backup


def execute_update(request: Dict[str, Any], result: Dict[str, Any]) -> None:
    """在基础 Python 中执行；旧 Runner 只加载标准库，Worker 与插件进程均已退出。"""
    root = Path(request["root"])
    prefix = Path(request["prefix"])
    environment = ToolEnvironment(prefix, Path(request["python"]), Path(sys.executable), request["uv"])
    target = request["target"]
    version_tuple(target)
    source_id = request.get("source_id", "pypi")
    index = package_index(source_id)
    result.update(stage="precheck", from_version=request["current"], to_version=target, source_name=PACKAGE_INDEXES[source_id][0])
    env = os.environ.copy()
    # 防止继承 UV_PYTHON 等变量导致重建正在运行的 Python，或把其他实例环境当作目标。
    for name in list(env):
        if name.startswith("UV_") or name in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}:
            env.pop(name)
    env["UV_TOOL_DIR"] = str(prefix.parent)
    receipt = tomllib.loads((prefix / "uv-receipt.toml").read_text(encoding="utf-8"))["tool"]
    entrypoints = [item for item in receipt["entrypoints"] if item["name"] in {"maibot", "maibot.exe"}]
    if len(entrypoints) != 1 or not Path(entrypoints[0]["install-path"]).is_file():
        raise SelfUpdateError("not_uv_tool", "找不到 uv 登记的 maibot 命令入口，请手动更新")
    entrypoint = entrypoints[0]["install-path"]
    receipt_path = prefix / "uv-receipt.toml"
    original_receipt = receipt_path.read_text(encoding="utf-8")
    requirement = receipt["requirements"]
    if len(requirement) != 1 or set(requirement[0]) - {"name", "specifier"}:
        raise SelfUpdateError("not_uv_tool", "自定义工具依赖请通过原安装方式更新")
    updated_receipt = original_receipt
    if "specifier" in requirement[0]:
        # uv 的工具入口在 Windows 下仍被外层 maibot.exe 占用，不能调用会重写入口的
        # tool install/upgrade。因此只原地更新 site-packages，并同步 receipt 中的版本约束。
        # 只修改 uv 自己生成的单行 requirements 字段，其他设置与入口路径原样保留。
        updated_receipt, count = re.subn(
            r'(?m)^requirements = \[\{ name = "maibot", specifier = "[^"\r\n]*" \}\][ \t]*$',
            f'requirements = [{{ name = "maibot", specifier = "=={target}" }}]',
            original_receipt,
        )
        if count != 1:
            raise SelfUpdateError("not_uv_tool", "uv 安装记录格式不支持自动更新，请手动更新")
    env["PYTHONIOENCODING"] = "utf-8"
    verify = [str(environment.python), "-I", "-c", "from importlib.metadata import version; print(version('maibot'))"]
    def install_command(version: str) -> List[str]:
        return [environment.uv, "--no-config", "pip", "install", f"maibot=={version}",
                "--reinstall-package", "maibot", "--python", str(environment.python),
                "--default-index", index, "--no-python-downloads"]

    # 指定当前工具解释器；不重建环境、不重写全局命令入口，也不删除插件额外安装的依赖。
    with FileLock(environment.locks / "update.guard"):
        ensure_other_instances_stopped(environment, root)
        if _run(verify, root, env) != request["current"]:
            raise SelfUpdateError("package_changed", "主程序版本已被外部更新，请重新检查")
        backup = backup_instance(root)
        result["stage"] = "dependencies"
        # 保存全部已安装依赖的版本，回滚时同时恢复插件共用的依赖。
        inventory = _run([
            str(environment.python), "-I", "-c",
            "from importlib.metadata import distributions; print('\\n'.join(f'{d.name}=={d.version}' for d in distributions()))",
        ], root, env)
        requirements = backup / "requirements.txt"
        requirements.write_text(inventory + "\n", encoding="utf-8")
        try:
            _run(install_command(target), root, env)
            result["stage"] = "verify"
            if _run(verify, root, env) != target:
                raise SelfUpdateError("package_version_mismatch", "升级后安装版本与确认的目标版本不一致")
            if _run([entrypoint, "--version"], root, env) != target:
                raise SelfUpdateError("package_entrypoint_failed", "升级后的 maibot 命令入口验证失败")
            _run([environment.uv, "--no-config", "pip", "check", "--python", str(environment.python)], root, env)
            temp_receipt = receipt_path.with_suffix(".toml.tmp")
            temp_receipt.write_text(updated_receipt, encoding="utf-8")
            os.replace(temp_receipt, receipt_path)
        except (SelfUpdateError, OSError, subprocess.SubprocessError) as exc:
            result["error_code"] = exc.code if isinstance(exc, SelfUpdateError) else "package_upgrade_failed"
            result["error"] = str(exc)
            try:
                # 精确恢复快照并清除本次新增的依赖，不能只降级主包、留下新版本依赖。
                _run([environment.uv, "--no-config", "pip", "sync", "--python", str(environment.python),
                      "--default-index", index, "--reinstall", str(requirements)], root, env)
                if _run(verify, root, env) != request["current"]:
                    raise SelfUpdateError("rollback_failed", "回滚后的主程序版本不一致")
                if _run([entrypoint, "--version"], root, env) != request["current"]:
                    raise SelfUpdateError("rollback_failed", "回滚后的命令入口验证失败")
                _run([environment.uv, "--no-config", "pip", "check", "--python", str(environment.python)], root, env)
                receipt_path.write_text(original_receipt, encoding="utf-8")
                result["rolled_back"] = True
            except (SelfUpdateError, OSError, subprocess.SubprocessError) as rollback_error:
                result["rollback_error"] = str(rollback_error)
            return
    result.update(success=True, stage="done", dependencies_synced=True)


def run_update(root: Path, current: str, target: str, result: Dict[str, Any], source_id: str = "pypi") -> Dict[str, Any]:
    """复制执行器到实例目录，避免升级时替换正在读取的执行器源码。"""
    package_index(source_id)
    environment = tool_environment()
    state = root / "data/self_update"
    state.mkdir(parents=True, exist_ok=True)
    script = state / "package_updater.py"
    shutil.copyfile(Path(__file__), script)
    request = {"root": str(root.resolve()), "prefix": str(environment.prefix), "python": str(environment.python),
               "uv": environment.uv, "current": current, "target": target, "result": result, "source_id": source_id}
    request_path = state / "package_request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output_path = state / "package_result.json"
    output_path.unlink(missing_ok=True)
    # 与终端信号隔离：用户停止旧 Runner 时，让已经开始的更新/回滚走完，不能中途打断安装。
    process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.run([str(environment.base_python), "-I", str(script), str(request_path)], cwd=root,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", **process_options)
    if not output_path.is_file():
        raise SelfUpdateError("package_updater_failed", f"独立更新进程失败: {process.stderr[-4000:]}")
    return json.loads(output_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = request["result"]
    try:
        execute_update(request, result)
    except (SelfUpdateError, OSError, ValueError, KeyError, StopIteration, sqlite3.Error, subprocess.SubprocessError) as exc:
        result["error_code"] = exc.code if isinstance(exc, SelfUpdateError) else "package_updater_failed"
        result["error"] = str(exc)
    result["finished_at"] = time.time()
    output = args.request.with_name("package_result.json")
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
