"""主程序自更新。

Worker（WebUI）只负责检查更新与登记更新请求；真正改动代码与依赖的步骤由 Runner 在
两次 Worker 之间执行。此时没有业务进程占用文件，失败时也能在新代码运行前回滚。

本模块会被 Runner 加载，只能依赖标准库。
"""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

import base64
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib

from .git_source import GitSource
from .package_update import (
    PACKAGE_INDEXES,
    PACKAGE_RUNNER_ENV,
    SelfUpdateError,
    ensure_other_instances_stopped,
    fetch_releases,
    latest_release,
    package_index,
    release_exists,
    run_update,
    tool_environment,
    version_tuple,
)
from .paths import PACKAGED_INSTALL, SOURCE_ROOT, get_instance_root
from .version import read_project_version

PROJECT_ROOT = get_instance_root() if PACKAGED_INSTALL else SOURCE_ROOT

# 更新只拉取官方仓库或用户本次选择的镜像，不读取本地 remote 或插件市场设置。
OFFICIAL_REPOSITORY_PATH = "Mai-with-u/MaiBot"
OFFICIAL_REPOSITORY_URL = f"https://github.com/{OFFICIAL_REPOSITORY_PATH}.git"
OFFICIAL_SOURCE_ID = "github"
SUPPORTED_BRANCHES: Tuple[str, ...] = ("main", "dev")
DEPENDENCY_FILES = frozenset({"pyproject.toml", "requirements.txt"})

PENDING_MAX_AGE_SECONDS = 600.0
GIT_TIMEOUT_SECONDS = 120.0
GIT_FETCH_TIMEOUT_SECONDS = 300.0
DEPENDENCY_TIMEOUT_SECONDS = 1800.0
MAX_LISTED_COMMITS = 30
MAX_LISTED_DIRTY_FILES = 20

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_STATE_DIR_PARTS = ("data", "self_update")
# 连接上但几乎不传数据的来源尽快报错，交由用户重新选择。
_GIT_STALL_OPTIONS = ("-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30")
_COMMIT_FIELD_SEPARATOR = "\x1f"

BLOCKER_MESSAGES: Dict[str, str] = {
    "uv_missing": "未找到 uv，请将 uv 加入 MaiBot 启动环境的 PATH",
    "not_uv_tool": "当前不是 PyPI 来源的 uv tool 安装，请使用原安装工具更新",
    "package_runner_missing": "请通过 maibot run 启动，才能自动更新并重启",
    "other_instances_running": "同一工具环境还有其他实例运行，请先停止它们再更新",
    "package_busy": "工具环境正在更新，请稍后重试",
    "git_missing": "未找到 Git，无法自更新",
    "not_git_repo": "当前部署不是 Git 仓库（如 Docker 镜像或压缩包），请按对应部署方式更新",
    "detached_head": "当前不在任何分支上（分离头指针），无法自更新",
    "unsupported_branch": f"仅支持在 {' / '.join(SUPPORTED_BRANCHES)} 分支上自更新",
    "dirty_worktree": "存在未提交的本地改动，请先提交或还原后再更新",
    "diverged": "本地存在官方分支没有的提交，无法快进更新",
    "no_dependency_tool": "本次更新包含依赖变更，但未找到可用的依赖安装方式（uv 或 pip）",
    "python_requirement_changed": "本次更新修改了 Python 版本要求，请手动更新",
}


@dataclass
class LocalState:
    """本地仓库状态，不联网即可得到。"""

    version: str
    blocker: Optional[str] = None  # 导致完全无法检查更新的原因
    branch: Optional[str] = None
    commit: Optional[str] = None
    dirty_files: List[str] = field(default_factory=list)
    dependency_tool: Optional[str] = None
    deployment_type: str = "git"


@dataclass
class UpdateSource:
    """一个可以取回官方仓库的来源：官方地址或某个镜像。"""

    id: str
    name: str
    url: str


@dataclass
class UpdateCommit:
    """待更新的单个提交。"""

    short_id: str
    summary: str
    committed_at: str


@dataclass
class UpdateCheck:
    """一次更新检查的结论。"""

    local: LocalState
    checked_at: Optional[float] = None  # None 表示尚未联网检查
    latest_commit: Optional[str] = None
    latest_version: Optional[str] = None
    behind: int = 0
    ahead: int = 0
    dependencies_changed: bool = False
    commits: List[UpdateCommit] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    source: Optional[UpdateSource] = None  # 本次检查实际使用的来源

    @property
    def update_available(self) -> bool:
        return self.behind > 0

    @property
    def can_apply(self) -> bool:
        return self.update_available and not self.blockers


@dataclass
class PendingUpdate:
    """Worker 登记、等待 Runner 执行的更新请求。"""

    target_commit: str
    base_commit: str
    branch: str
    requested_at: float
    source_id: Optional[str] = None
    custom_prefix: str = ""


@dataclass
class UpdateResult:
    """Runner 执行一次更新后的结果，供 WebUI 展示。"""

    success: bool
    started_at: float
    finished_at: float = 0.0
    branch: str = ""
    from_commit: str = ""
    to_commit: str = ""
    from_version: str = ""
    to_version: str = ""
    stage: str = ""  # 结束时所处阶段：request / precheck / verify / merge / dependencies / done
    error_code: str = ""
    error: str = ""
    rolled_back: bool = False
    rollback_error: str = ""
    dependencies_synced: bool = False
    source_name: str = ""


def _state_dir(root: Path) -> Path:
    instance_root = get_instance_root() if root == PROJECT_ROOT else root
    return instance_root.joinpath(*_STATE_DIR_PARTS)


def _pending_path(root: Path) -> Path:
    return _state_dir(root) / "pending.json"


def _result_path(root: Path) -> Path:
    return _state_dir(root) / "last_result.json"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    """先写临时文件再替换，避免读到写了一半的状态文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _run_command(command: List[str], root: Path, timeout: float) -> "subprocess.CompletedProcess[str]":
    env = os.environ.copy()
    # 凭据提示会让无人值守的更新一直挂起；官方仓库是公开的，不需要凭据。
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        command,
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    """TimeoutExpired 即使启用 text 也可能携带 bytes，保留命令超时前的诊断输出。"""
    parts: List[str] = []
    for label, output in (("标准输出", exc.stdout), ("错误输出", exc.stderr)):
        if output:
            text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
            parts.append(f"{label}（末尾最多 4000 字符）：\n{text[-4000:].strip()}")
    return "\n".join(parts) if parts else "命令超时前没有输出。"


def _git(root: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> str:
    """执行必须成功的 Git 命令，返回未裁剪的标准输出。"""

    command_text = f"git {' '.join(args)}"
    try:
        result = _run_command(["git", *args], root, timeout)
    except subprocess.TimeoutExpired as exc:
        raise SelfUpdateError(
            "git_timeout", f"Git 命令超时（{timeout:g} 秒）: {command_text}\n{_timeout_output(exc)}",
        ) from exc
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip()
        raise SelfUpdateError("git_failed", f"Git 命令失败: {command_text}\n{output}")
    return result.stdout


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = _run_command(["git", "merge-base", "--is-ancestor", ancestor, descendant], root, GIT_TIMEOUT_SECONDS)
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise SelfUpdateError("git_failed", f"无法判断提交关系: {result.stderr.strip()}")


def _parse_pyproject(text: str) -> Tuple[str, str]:
    """返回 (主程序版本号, Python 版本要求)。"""

    project: Dict[str, Any] = tomllib.loads(text)["project"]
    return str(project["version"]).strip(), str(project.get("requires-python", "")).strip()


def _read_pyproject_at(root: Path, commit: str) -> Tuple[str, str]:
    return _parse_pyproject(_git(root, "show", f"{commit}:pyproject.toml"))


def _changed_files(root: Path, base: str, target: str) -> Set[str]:
    output = _git(root, "diff", "--name-only", base, target)
    return {line.strip() for line in output.splitlines() if line.strip()}


def _list_commits(root: Path, base: str, target: str) -> List[UpdateCommit]:
    log_format = _COMMIT_FIELD_SEPARATOR.join(("%h", "%s", "%cI"))
    output = _git(root, "log", f"--format={log_format}", "-n", str(MAX_LISTED_COMMITS), f"{base}..{target}")
    commits: List[UpdateCommit] = []
    for line in output.splitlines():
        short_id, summary, committed_at = line.split(_COMMIT_FIELD_SEPARATOR)
        commits.append(UpdateCommit(short_id=short_id, summary=summary, committed_at=committed_at))
    return commits


def detect_dependency_tool(root: Path = PROJECT_ROOT) -> Optional[str]:
    """判断当前运行环境的依赖应当用哪种方式同步。"""

    # uv 管理的项目环境直接按项目依赖及源策略安装。
    in_project_venv = Path(sys.prefix).resolve() == (root / ".venv").resolve()
    if in_project_venv and (root / "pyproject.toml").is_file() and shutil.which("uv") is not None:
        return "uv"
    if importlib.util.find_spec("pip") is not None and (root / "requirements.txt").is_file():
        return "pip"
    return None


def _dependency_command(tool: str) -> List[str]:
    if tool == "uv":
        # 与普通 uv run 一样检查并更新依赖解析，沿用 pyproject 中的源和版本约束。
        # --inexact 保留插件额外安装的包；这里只同步运行依赖，不安装项目本体。
        return ["uv", "sync", "--inexact", "--no-dev", "--no-install-project"]
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"]


def _failed_dependency_index(root: Path, output: str) -> Optional[Dict[str, Any]]:
    """只排除能从网络错误明确定位到的命名索引，不猜测下载 CDN 或依赖冲突的归属。"""
    if not re.search(
        r"operation timed out|connection (?:reset|refused|closed)|dns error|"
        r"failed to lookup address|network is unreachable|connection timed out",
        output, re.IGNORECASE,
    ):
        return None
    failed = re.search(r"Failed to fetch:\s*`(https?://[^`]+)`", output)
    if failed is None:
        return None
    config_path = root / "uv.toml"
    if config_path.is_file():
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("tool", {}).get("uv", {})
    for index in config.get("index", []):
        name, url = index.get("name"), index.get("url", "")
        if name and url and failed.group(1).startswith(url.rstrip("/") + "/"):
            return index
    return None


def _sync_dependencies(root: Path, tool: str, logger: Any = None) -> None:
    command = _dependency_command(tool)
    excluded: Set[str] = set()
    deadline = time.monotonic() + DEPENDENCY_TIMEOUT_SECONDS
    logger = logger if logger is not None else logging.getLogger(__name__)
    # uv 没有排除单个索引的开关。用同名的空本地索引覆盖故障源，保留其余配置、
    # 选版本策略及 sources 的显式绑定；不修改 pyproject，也不把绑定包切到别的源。
    with TemporaryDirectory(prefix="maibot-update-index-") as empty_index:
        while True:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, DEPENDENCY_TIMEOUT_SECONDS)
                result = _run_command(command, root, remaining)
            except subprocess.TimeoutExpired as exc:
                raise SelfUpdateError(
                    "dependency_timeout",
                    f"依赖同步超时（{DEPENDENCY_TIMEOUT_SECONDS:g} 秒）: {' '.join(command)}\n{_timeout_output(exc)}",
                ) from exc
            if result.returncode == 0:
                return
            output = result.stderr.strip() or result.stdout.strip()
            index = _failed_dependency_index(root, output) if tool == "uv" else None
            if index is None or index["name"] in excluded:
                detail = f"\n本次已临时排除故障源：{', '.join(sorted(excluded))}" if excluded else ""
                raise SelfUpdateError("dependency_failed", f"依赖同步失败: {' '.join(command)}\n{output[-4000:]}{detail}")
            name = index["name"]
            excluded.add(name)
            logger.warning(f"自更新：依赖源 {name} 网络故障，本次临时排除后重试；保留原版本约束和选版本策略")
            option = "--default-index" if index.get("default", False) else "--index"
            command.extend([option, f"{name}={Path(empty_index).as_uri()}"])


def inspect_local(root: Path = PROJECT_ROOT) -> LocalState:
    """读取本地仓库状态；只执行只读的 Git 命令。"""

    if PACKAGED_INSTALL:
        state = LocalState(version=read_project_version(), dependency_tool="uv", deployment_type="package")
        try:
            environment = tool_environment()
            ensure_other_instances_stopped(environment, root)
        except SelfUpdateError as exc:
            state.blocker = exc.code
        return state
    version, _ = _parse_pyproject((root / "pyproject.toml").read_text(encoding="utf-8"))
    state = LocalState(version=version, dependency_tool=detect_dependency_tool(root))
    if shutil.which("git") is None:
        state.blocker = "git_missing"
        return state
    if not (root / ".git").exists():
        state.blocker = "not_git_repo"
        return state

    state.commit = _git(root, "rev-parse", "HEAD").strip()
    branch_result = _run_command(["git", "symbolic-ref", "--short", "-q", "HEAD"], root, GIT_TIMEOUT_SECONDS)
    if branch_result.returncode != 0:
        state.blocker = "detached_head"
        return state
    state.branch = branch_result.stdout.strip()
    if state.branch not in SUPPORTED_BRANCHES:
        state.blocker = "unsupported_branch"
        return state

    # 未跟踪文件不影响快进；若与新版本文件冲突，Git 会拒绝合并且不改动任何文件。
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    state.dirty_files = [line[3:] for line in status.splitlines() if line.strip()][:MAX_LISTED_DIRTY_FILES]
    return state


def _collect_apply_blockers(root: Path, local: LocalState, target: str, changed: Set[str]) -> List[str]:
    """列出阻止把本地仓库更新到 target 的原因；检查、登记、执行三处共用同一套判断。"""

    blockers: List[str] = []
    if local.dirty_files:
        blockers.append("dirty_worktree")
    if not _is_ancestor(root, "HEAD", target):
        blockers.append("diverged")
    if DEPENDENCY_FILES & changed and local.dependency_tool is None:
        blockers.append("no_dependency_tool")
    # Python 版本要求变化时，uv 可能重建整个虚拟环境，交给用户手动处理。
    if _read_pyproject_at(root, "HEAD")[1] != _read_pyproject_at(root, target)[1]:
        blockers.append("python_requirement_changed")
    return blockers


def evaluate_update(local: LocalState, target: str, checked_at: float, root: Path = PROJECT_ROOT) -> UpdateCheck:
    """基于已经取回的目标提交计算更新结论，不联网。"""

    if local.deployment_type == "package":
        newer = version_tuple(target) > version_tuple(local.version)
        return UpdateCheck(
            local=local, checked_at=checked_at, latest_commit=target, latest_version=target,
            behind=int(newer), dependencies_changed=newer,
            blockers=[local.blocker] if local.blocker else [],
            source=UpdateSource(id="pypi", name="PyPI", url="https://pypi.org/project/maibot/"),
        )
    if local.commit is None:
        raise ValueError("本地仓库状态缺少当前提交，无法评估更新")

    counts = _git(root, "rev-list", "--left-right", "--count", f"{local.commit}...{target}").split()
    changed = _changed_files(root, local.commit, target)
    return UpdateCheck(
        local=local,
        checked_at=checked_at,
        latest_commit=target,
        latest_version=_read_pyproject_at(root, target)[0],
        ahead=int(counts[0]),
        behind=int(counts[1]),
        dependencies_changed=bool(DEPENDENCY_FILES & changed),
        commits=_list_commits(root, local.commit, target),
        blockers=_collect_apply_blockers(root, local, target, changed),
    )


def select_update_source(source_id: str, custom_prefix: str = "") -> UpdateSource:
    """本体源码只允许单个 CNB、GitHub 或自定义 HTTPS 前缀。"""
    try:
        source = GitSource.select(source_id, custom_prefix)
        return UpdateSource(source.id, source.name, source.clone_url("Mai-with-u", "MaiBot"))
    except ValueError as exc:
        raise SelfUpdateError("invalid_source", str(exc)) from exc


def _fetch_branch_head(
    root: Path, branch: str, source_id: str = "github", custom_prefix: str = ""
) -> Tuple[str, UpdateSource]:
    """只从本次选择的来源取回指定分支，失败不自动换源。"""
    source = select_update_source(source_id, custom_prefix)
    # CNB 公开镜像匿名读取，避免凭据管理器里的过期 Token 把公开请求变成认证失败。
    anonymous_options = (
        "-c", "credential.helper=", "-c", "http.extraHeader=",
        "-c", "http.https://cnb.cool/.extraHeader=",
    ) if source_id == "cnb" else ()
    try:
        _git(root, *anonymous_options, *_GIT_STALL_OPTIONS, "fetch", "--no-tags", "--quiet", source.url,
             f"refs/heads/{branch}", timeout=GIT_FETCH_TIMEOUT_SECONDS)
    except SelfUpdateError as exc:
        raise SelfUpdateError("fetch_failed", f"{source.name} 获取更新失败：{exc.message}") from exc
    return _git(root, "rev-parse", "FETCH_HEAD^{commit}").strip(), source


def _get_update_json(path: str, api_source: str, deadline: float) -> Dict[str, Any]:
    """读取选定的公开元数据接口，每次请求共用整轮检查的截止时间。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SelfUpdateError("fetch_failed", "检查更新超时（60 秒）")
    if api_source == "cnb":
        url = f"https://cnb.cool/Mai-with-u/maibot/-/git/{path}"
    else:
        url = GitSource.select("github").api_url("Mai-with-u", "MaiBot", path)
    try:
        # CNB 需要明确的 JSON Accept，避免返回网页 HTML。
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "MaiBot"})
        with urlopen(request, timeout=remaining) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError("API 响应不是对象")
        return payload
    except (OSError, ValueError) as exc:
        raise SelfUpdateError(
            "fetch_failed", f"请求 {url} 失败（本次请求超时上限 {remaining:.1f} 秒）：{type(exc).__name__}: {exc}",
        ) from exc


def _check_source_api(
    local: LocalState, root: Path, source: UpdateSource, api_source: str, deadline: float,
    on_progress: Optional[Callable[[str], None]] = None,
) -> UpdateCheck:
    """从单个信息源获取完整检查结果，下载源仍由用户选择。"""
    started = time.monotonic()
    stage = "读取分支信息"

    def report(message: str) -> None:
        nonlocal stage
        stage = message
        if on_progress is not None:
            on_progress(message)

    def read(path: str) -> Dict[str, Any]:
        return _get_update_json(path, api_source, deadline)

    if local.branch is None or local.commit is None:
        raise ValueError("本地仓库状态缺少当前分支或提交")
    try:
        report(f"读取 {local.branch} 分支信息")
        branch = read(f"branches/{quote(local.branch, safe='')}")
        target = branch["commit"]["sha"]
        if not isinstance(target, str) or _COMMIT_PATTERN.fullmatch(target) is None:
            raise ValueError("无效的远端提交 SHA")
        check = UpdateCheck(
            local=local, checked_at=time.time(), latest_commit=target,
            source=UpdateSource(source.id, f"{source.name}（版本检查：{api_source}）", source.url),
        )
        if local.dirty_files:
            check.blockers.append("dirty_worktree")
        if target == local.commit:
            check.latest_version = local.version
            return check
        report("比较本地与远端提交")
        comparison = read(f"compare/{local.commit}...{target}?per_page=100")
        if api_source == "cnb":
            check.behind = int(comparison["total_commits"])
            # CNB 没有 behind_by；只有分叉或本地领先时才反向比较。
            if comparison["merge_base_commit"]["sha"] != local.commit:
                report("检查分支是否分叉")
                check.ahead = int(read(f"compare/{target}...{local.commit}")["total_commits"])
        else:
            check.behind = int(comparison["ahead_by"])
            check.ahead = int(comparison["behind_by"])
        if check.ahead:
            check.blockers.append("diverged")
        changed = {item["path" if api_source == "cnb" else "filename"] for item in comparison["files"]}
        check.dependencies_changed = bool(DEPENDENCY_FILES & changed)
        if check.dependencies_changed and local.dependency_tool is None:
            check.blockers.append("no_dependency_tool")
        check.commits = [
            UpdateCommit(item["sha"][:8], item["commit"]["message"].splitlines()[0], item["commit"]["committer"]["date"])
            for item in comparison["commits"][:MAX_LISTED_COMMITS]
        ]
        report("读取远端版本与依赖要求")
        if api_source == "cnb":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SelfUpdateError("fetch_failed", "检查更新超时（60 秒）")
            url = GitSource.select("cnb").raw_url("Mai-with-u", "MaiBot", target, "pyproject.toml")
            try:
                project = GitSource.read_text(url, timeout=remaining)
            except (OSError, ValueError) as exc:
                raise SelfUpdateError("fetch_failed", f"请求 {url} 失败：{type(exc).__name__}: {exc}") from exc
        else:
            contents = read(f"contents/pyproject.toml?ref={target}")
            project = base64.b64decode(contents["content"]).decode("utf-8")
        check.latest_version, requires_python = _parse_pyproject(project)
        if _parse_pyproject((root / "pyproject.toml").read_text(encoding="utf-8"))[1] != requires_python:
            check.blockers.append("python_requirement_changed")
        return check
    except (SelfUpdateError, OSError, KeyError, TypeError, ValueError) as exc:
        raise SelfUpdateError(
            "fetch_failed",
            f"{api_source} 检查失败（{stage}，已耗时 {time.monotonic() - started:.1f} 秒）：{type(exc).__name__}: {exc}",
        ) from exc


def check_for_update(
    root: Path = PROJECT_ROOT, source_id: Optional[str] = None, custom_prefix: str = "",
    on_progress: Optional[Callable[[str], None]] = None,
) -> UpdateCheck:
    """联网检查官方仓库是否有新版本。"""

    if on_progress is not None:
        on_progress("检查本地安装与仓库状态")
    local = inspect_local(root)
    if local.blocker is not None:
        return UpdateCheck(local=local, blockers=[local.blocker])
    if local.deployment_type == "package":
        selected = source_id or "pypi"
        index = package_index(selected)
        if on_progress is not None:
            on_progress(f"{PACKAGE_INDEXES[selected][0]}：读取正式版本列表")
        check = evaluate_update(local, latest_release(local.version, selected), time.time(), root)
        check.source = UpdateSource(selected, PACKAGE_INDEXES[selected][0], index)
        return check
    if local.branch is None:
        raise ValueError("本地仓库状态缺少当前分支，无法检查更新")
    source = select_update_source(source_id or "github", custom_prefix)
    # 两个源同时检查，失败的一路不掩盖另一路的有效结果；不等待慢源退出。
    deadline = time.monotonic() + 60
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="update-check")
    progress = {name: "等待查询" for name in ("github", "cnb")}
    progress_lock = Lock()

    def report(name: str, message: str) -> None:
        # 两路并行查询共享诊断快照，超时时保留失败原因和仍在等待的阶段。
        with progress_lock:
            progress[name] = message
        if on_progress is not None:
            on_progress(f"{name}：{message}")

    def check_source(name: str) -> UpdateCheck:
        try:
            result = _check_source_api(local, root, source, name, deadline, lambda message: report(name, message))
        except (SelfUpdateError, OSError, ValueError, KeyError, TypeError) as exc:
            report(name, f"失败：{exc}")
            raise
        report(name, "检查完成")
        return result

    pending = {executor.submit(check_source, name) for name in progress}
    errors: List[str] = []
    try:
        while pending:
            done, pending = wait(pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            if not done:
                with progress_lock:
                    details = "；".join(f"{name}：{message}" for name, message in progress.items())
                raise SelfUpdateError("fetch_failed", f"检查更新超时（60 秒）。各源最后状态：{details}")
            for future in done:
                try:
                    return future.result()
                except (SelfUpdateError, OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append(str(exc))
        raise SelfUpdateError("fetch_failed", "GitHub 和 CNB 检查均失败：" + "；".join(errors))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def request_update(
    expected_commit: str, root: Path = PROJECT_ROOT, source_id: Optional[str] = None, custom_prefix: str = ""
) -> PendingUpdate:
    """登记更新请求，等待 Runner 在下次重启时执行。

    expected_commit 是用户在界面上确认过的目标提交；Runner 只会更新到这个提交。
    """

    if PACKAGED_INSTALL:
        selected = source_id or "pypi"
        package_index(selected)
        local = inspect_local(root)
        if local.blocker:
            raise SelfUpdateError(local.blocker, BLOCKER_MESSAGES[local.blocker])
        if version_tuple(expected_commit) <= version_tuple(local.version):
            raise SelfUpdateError("already_up_to_date", "目标版本必须高于当前版本")
        if not release_exists(fetch_releases(selected), expected_commit):
            raise SelfUpdateError("unknown_version", "所选包源没有这个可用的正式版本，请重新检查")
        pending = PendingUpdate(expected_commit, local.version, "pypi", time.time(), selected)
        path = _pending_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as output:
                json.dump(asdict(pending), output)
        except FileExistsError as exc:
            raise SelfUpdateError("package_busy", "已有更新请求，请等待更新完成") from exc
        return pending
    selected = source_id or "github"
    select_update_source(selected, custom_prefix)
    if _COMMIT_PATTERN.fullmatch(expected_commit) is None:
        raise SelfUpdateError("invalid_commit", "目标提交格式不正确")

    local = inspect_local(root)
    if local.blocker is not None:
        raise SelfUpdateError(local.blocker, BLOCKER_MESSAGES[local.blocker])
    if local.branch is None or local.commit is None:
        raise ValueError("本地仓库状态缺少当前分支或提交，无法登记更新")
    if expected_commit == local.commit:
        raise SelfUpdateError("already_up_to_date", "当前已经是这个版本")

    # 用户确认更新后才拉取对象；Runner 重启后仍会重新验证来源与快进条件。
    _fetch_branch_head(root, local.branch, selected, custom_prefix)
    object_check = _run_command(["git", "cat-file", "-e", f"{expected_commit}^{{commit}}"], root, GIT_TIMEOUT_SECONDS)
    if object_check.returncode != 0:
        raise SelfUpdateError("unknown_commit", "本地没有这个目标提交，请重新检查更新")

    changed = _changed_files(root, local.commit, expected_commit)
    blockers = _collect_apply_blockers(root, local, expected_commit, changed)
    if blockers:
        raise SelfUpdateError(blockers[0], BLOCKER_MESSAGES[blockers[0]])

    pending = PendingUpdate(
        target_commit=expected_commit,
        base_commit=local.commit,
        branch=local.branch,
        requested_at=time.time(),
        source_id=selected,
        custom_prefix=custom_prefix if selected == "custom" else "",
    )
    _write_json(_pending_path(root), asdict(pending))
    return pending


def clear_pending_update(root: Path = PROJECT_ROOT) -> None:
    """丢弃尚未执行的更新请求。"""

    _pending_path(root).unlink(missing_ok=True)


def read_last_result(root: Path = PROJECT_ROOT) -> Optional[UpdateResult]:
    """读取最近一次更新的执行结果。"""

    result_path = _result_path(root)
    if not result_path.is_file():
        return None
    return UpdateResult(**json.loads(result_path.read_text(encoding="utf-8")))


def _execute_update(pending: PendingUpdate, result: UpdateResult, logger: Any, root: Path) -> None:
    result.stage = "precheck"
    if time.time() - pending.requested_at > PENDING_MAX_AGE_SECONDS:
        raise SelfUpdateError("request_expired", "更新请求已过期，请重新发起")
    local = inspect_local(root)
    if local.blocker is not None:
        raise SelfUpdateError(local.blocker, BLOCKER_MESSAGES[local.blocker])
    if local.branch != pending.branch or local.commit != pending.base_commit:
        raise SelfUpdateError("state_changed", "登记更新之后本地仓库已发生变化，请重新检查更新")
    result.from_version = local.version

    # 重新取回官方分支确认：目标提交必须在它的历史里，状态文件本身不被信任。
    # 来源限制为本次选中的内置源或一个合法的 HTTPS 自定义前缀。
    result.stage = "verify"
    logger.info(f"自更新：正在确认官方仓库的 {pending.branch} 分支")
    official_head, source = _fetch_branch_head(root, pending.branch, pending.source_id or "github", pending.custom_prefix)
    result.source_name = source.name
    logger.info(f"自更新：更新来源 {source.name}")
    if not _is_ancestor(root, pending.target_commit, official_head):
        raise SelfUpdateError("untrusted_commit", f"目标提交不在官方仓库 {pending.branch} 分支的历史中")
    changed = _changed_files(root, pending.base_commit, pending.target_commit)
    blockers = _collect_apply_blockers(root, local, pending.target_commit, changed)
    if blockers:
        raise SelfUpdateError(blockers[0], BLOCKER_MESSAGES[blockers[0]])
    result.to_version = _read_pyproject_at(root, pending.target_commit)[0]

    result.stage = "merge"
    logger.info(f"自更新：{pending.base_commit[:10]} -> {pending.target_commit[:10]}")
    try:
        _git(root, "merge", "--ff-only", pending.target_commit)
    except SelfUpdateError:
        # 文件被占用等原因可能让检出停在一半，需要把工作区恢复到更新前。
        _rollback(pending, None, result, logger, root)
        raise

    dependencies_changed = bool(DEPENDENCY_FILES & changed)
    if dependencies_changed and local.dependency_tool is not None:
        result.stage = "dependencies"
        logger.info(f"自更新：正在同步依赖（{local.dependency_tool}），可能需要几分钟")
        try:
            _sync_dependencies(root, local.dependency_tool, logger)
        except SelfUpdateError:
            _rollback(pending, local.dependency_tool, result, logger, root)
            raise
        result.dependencies_synced = True

    result.stage = "done"
    result.success = True


def _rollback(
    pending: PendingUpdate,
    dependency_tool: Optional[str],
    result: UpdateResult,
    logger: Any,
    root: Path,
) -> None:
    """退回更新前的提交；dependency_tool 不为空时把依赖也同步回旧版本。"""

    logger.error(f"自更新：{result.stage} 阶段失败，正在回滚到更新前的版本")
    try:
        # precheck 已确认没有未提交的已跟踪改动，这里丢弃的只有本次更新写入的内容。
        _git(root, "reset", "--hard", pending.base_commit)
        if dependency_tool is not None:
            _sync_dependencies(root, dependency_tool, logger)
    except SelfUpdateError as exc:
        result.rollback_error = exc.message
        logger.error(f"自更新：回滚失败，请手动检查仓库与依赖状态: {exc.message}")
        return
    result.rolled_back = True


def apply_pending_update(logger: Any, root: Path = PROJECT_ROOT) -> Optional[UpdateResult]:
    """由 Runner 在两次 Worker 之间调用：执行已登记的更新并记录结果。

    没有待执行的请求时返回 None。无论成功与否，请求都只会被执行一次。
    更新成功后，调用方应让新代码的 Runner 接手（见 process_runner.run_runner）。
    """

    pending_path = _pending_path(root)
    if not pending_path.is_file():
        return None

    raw_request = pending_path.read_text(encoding="utf-8")
    # 先删除请求再执行：即使更新中途失败，下一次重启也不会重复尝试。
    pending_path.unlink()

    result = UpdateResult(success=False, started_at=time.time(), stage="request")
    try:
        pending = PendingUpdate(**json.loads(raw_request))
        result.branch = pending.branch
        result.from_commit = pending.base_commit
        result.to_commit = pending.target_commit
        if PACKAGED_INSTALL:
            result.from_version = pending.base_commit
            result.to_version = pending.target_commit
            # 包安装没有 Git 提交，结果显示只使用版本号。
            result.from_commit = result.to_commit = ""
            if pending.branch != "pypi" or not 0 <= time.time() - pending.requested_at <= PENDING_MAX_AGE_SECONDS:
                raise SelfUpdateError("stale_request", "更新请求已过期或不属于包安装，请重新检查更新")
            if os.environ.get(PACKAGE_RUNNER_ENV) != "1":
                raise SelfUpdateError("package_runner_missing", BLOCKER_MESSAGES["package_runner_missing"])
            result = UpdateResult(**run_update(
                root, pending.base_commit, pending.target_commit, asdict(result), pending.source_id or "pypi"
            ))
        else:
            _execute_update(pending, result, logger, root)
    except SelfUpdateError as exc:
        result.error_code = exc.code
        result.error = exc.message
        if PACKAGED_INSTALL and exc.code == "package_updater_failed":
            result.rollback_error = "独立更新进程未返回结果，无法确认环境状态，请手动检查工具环境"
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        result.error_code = "unexpected_error"
        result.error = f"{type(exc).__name__}: {exc}"

    result.finished_at = time.time()
    _write_json(_result_path(root), asdict(result))
    if result.success:
        logger.info(f"自更新完成：{result.from_version} -> {result.to_version}，正在启动新版本")
    else:
        logger.error(f"自更新失败（阶段 {result.stage}），继续使用当前版本: {result.error}")
    return result
