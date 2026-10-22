"""使用公共 GitSource 的异步 Git 操作与插件进度服务。"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import asyncio
import os
import shutil
import subprocess

from src.common.git_source import GitSource
from src.common.logger import get_logger
from src.common.network_security import NonPublicAddressError, validate_public_url

logger = get_logger("webui.git_mirror")

# 导入进度更新函数（避免循环导入）
_update_progress = None


async def _run_git_process(cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    """在线程池等待 Git；取消或超时时终止进程树并等待退出。"""
    launch = asyncio.create_task(asyncio.to_thread(
        subprocess.Popen, cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=os.name != "nt",
    ))
    communicate = None
    try:
        process = await asyncio.shield(launch)
        communicate = asyncio.create_task(asyncio.to_thread(process.communicate))
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communicate), timeout=300)
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
        # 进程创建也可能与取消并发，先取得句柄，再终止所有 Git 子进程。
        process = await launch
        if process.poll() is None:
            if os.name == "nt":
                result = await asyncio.to_thread(
                    subprocess.run, ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                )
                if result.returncode and process.poll() is None:
                    raise RuntimeError(f"终止 Git 进程失败: {result.stderr!r}") from exc
            else:
                import signal

                os.killpg(process.pid, signal.SIGKILL)
        if communicate is not None:
            await communicate
        else:
            await asyncio.to_thread(process.communicate)
        if isinstance(exc, asyncio.TimeoutError):
            raise subprocess.TimeoutExpired(cmd, 300) from exc
        raise


def _validate_custom_outbound_url(url: str, source_name: str = "自定义源") -> str:
    try:
        return validate_public_url(url, allow_fake_ip=True)
    except NonPublicAddressError as e:
        logger.warning(
            f"下载源解析异常: source={source_name} host={e.hostname} "
            f"addresses={','.join(sorted(e.resolved_addresses))} blocked={e.address}"
        )
        raise ValueError(
            f"下载源 {source_name} 的目标 {e.hostname} 被当前系统解析为非公网地址 {e.address}，已阻止下载。"
            "请检查运行麦麦的机器或容器的 DNS、hosts 和代理设置，"
            "或在插件市场的下载源设置中切换来源后重试。"
        ) from e
    except ValueError as e:
        raise ValueError(f"目标 URL 非法: {e}") from e


def set_update_progress_callback(callback):
    """设置进度更新回调函数"""
    global _update_progress
    _update_progress = callback


class GitMirrorService:
    """WebUI 的异步 Git 执行与进度；源解析及 HTTP 统一委托 GitSource。"""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries

    @staticmethod
    def check_git_installed() -> Dict[str, Any]:
        """
        检查本机是否安装了 Git

        Returns:
            Dict 包含:
                - installed: bool - 是否已安装 Git
                - version: str - Git 版本号（如果已安装）
                - path: str - Git 可执行文件路径（如果已安装）
                - error: str - 错误信息（如果未安装或检测失败）
        """
        import shutil
        import subprocess

        try:
            # 查找 git 可执行文件路径
            git_path = shutil.which("git")

            if not git_path:
                logger.warning("未找到 Git 可执行文件")
                return {"installed": False, "error": "系统中未找到 Git，请先安装 Git"}

            # 获取 Git 版本
            result = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                version = result.stdout.strip()
                logger.info(f"检测到 Git: {version} at {git_path}")
                return {"installed": True, "version": version, "path": git_path}
            else:
                logger.warning(f"Git 命令执行失败: {result.stderr}")
                return {"installed": False, "error": f"Git 命令执行失败: {result.stderr}"}

        except subprocess.TimeoutExpired:
            logger.error("Git 版本检测超时")
            return {"installed": False, "error": "Git 版本检测超时"}
        except Exception as e:
            logger.error(f"检测 Git 时发生错误: {e}")
            return {"installed": False, "error": f"检测 Git 时发生错误: {str(e)}"}

    async def pull_repository(
        self,
        repository_path: Path,
        branch: Optional[str] = None,
        remote_url: Optional[str] = None,
        mirror_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        在已安装插件目录内执行 Git 更新。

        与重新克隆不同，该方法保留插件目录中的配置文件、数据文件和未跟踪文件。
        """
        git_dir = repository_path / ".git"
        if not repository_path.exists() or not repository_path.is_dir():
            return {"success": False, "error": "插件目录不存在", "status_code": 404}
        if not git_dir.exists() or not git_dir.is_dir():
            return {"success": False, "error": "插件目录不是 Git 仓库，无法通过 Git 更新", "status_code": 400}

        if not remote_url:
            try:
                origin = await _run_git_process(["git", "remote", "get-url", "origin"], cwd=repository_path)
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                return {"success": False, "error": str(exc)}
            if origin.returncode == 0:
                candidate = origin.stdout.strip()
                if urlparse(candidate).netloc.lower() == "github.com":
                    remote_url = candidate

        # GitHub 分支更新使用与安装相同的单个下载源；网络或本地操作失败均直接暴露。
        if remote_url:
            parsed = urlparse(remote_url)
            if parsed.query or parsed.fragment:
                return {"success": False, "error": "仓库 URL 不允许包含查询参数或片段", "status_code": 400}
            parts = parsed.path.strip("/").removesuffix(".git").split("/")
            if parsed.scheme == "https" and parsed.netloc.lower() == "github.com" and len(parts) == 2:
                return await self._pull_github_repository(repository_path, branch, parts[0], parts[1], mirror_id)

        commands: List[List[str]] = []
        if remote_url:
            try:
                remote_url = _validate_custom_outbound_url(remote_url)
            except ValueError as e:
                return {"success": False, "error": str(e), "status_code": 400}
            commands.append(["git", "remote", "set-url", "origin", remote_url])

        commands.append(["git", "fetch", "origin", "--prune"])
        if branch:
            commands.append(["git", "checkout", branch])
            commands.append(["git", "pull", "--ff-only", "origin", branch])
        else:
            commands.append(["git", "pull", "--ff-only"])

        executed: List[str] = []

        for cmd in commands:
            executed.append(" ".join(cmd))

            try:
                process = await _run_git_process(cmd, cwd=repository_path)
            except subprocess.TimeoutExpired:
                return {"success": False, "error": f"Git 命令超时: {' '.join(cmd)}", "commands": executed}
            except FileNotFoundError:
                return {"success": False, "error": "Git 未安装或不在 PATH 中", "commands": executed}

            if process.returncode != 0:
                error_output = process.stderr.strip() or process.stdout.strip()
                return {
                    "success": False,
                    "error": f"Git 更新失败: {error_output}",
                    "commands": executed,
                    "status_code": 500,
                }

        return {"success": True, "path": str(repository_path), "branch": branch or "current", "commands": executed}

    async def _pull_github_repository(
        self, repository_path: Path, branch: Optional[str], owner: str, repo: str, mirror_id: Optional[str]
    ) -> Dict[str, Any]:
        executed: List[str] = []
        try:
            if branch is None:
                current = await _run_git_process(["git", "symbolic-ref", "--short", "HEAD"], cwd=repository_path)
                if current.returncode:
                    return {"success": False, "error": "当前处于 detached HEAD，请明确选择更新分支", "status_code": 409}
                branch = current.stdout.strip()
            # 不允许以选项或 refspec 注入 Git 参数。
            checked = await _run_git_process(["git", "check-ref-format", f"refs/heads/{branch}"], cwd=repository_path)
            if checked.returncode or branch.startswith("-"):
                return {"success": False, "error": "无效的更新分支", "status_code": 400}
            from src.plugin_market.sources import download_source

            source = await asyncio.to_thread(download_source)
            try:
                url = await asyncio.to_thread(_validate_custom_outbound_url, source.clone_url(owner, repo), source.name)
            except ValueError as exc:
                return {"success": False, "error": str(exc), "mirror_used": source.id, "status_code": 400}
            cmd = ["git", "fetch", "--no-tags", url, f"refs/heads/{branch}"]
            executed.append(" ".join(cmd))
            fetched = await _run_git_process(cmd, cwd=repository_path)
            if fetched.returncode:
                return {"success": False, "error": f"Git fetch 失败: {fetched.stderr.strip() or fetched.stdout.strip()}",
                        "commands": executed, "mirror_used": source.id, "status_code": 502}
            # FETCH_HEAD 是本次实际下载的分支；不改变 origin 身份或触碰用户配置文件。
            for cmd in (["git", "checkout", branch], ["git", "merge", "--ff-only", "FETCH_HEAD"]):
                executed.append(" ".join(cmd))
                result = await _run_git_process(cmd, cwd=repository_path)
                if result.returncode:
                    return {"success": False, "error": f"Git 更新失败: {result.stderr.strip() or result.stdout.strip()}",
                            "commands": executed, "mirror_used": source.id, "status_code": 409}
            return {"success": True, "path": str(repository_path), "branch": branch,
                    "commands": executed, "mirror_used": source.id}
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return {"success": False, "error": str(exc), "commands": executed}

    async def clone_repository(
        self,
        owner: str,
        repo: str,
        target_path: Path,
        branch: Optional[str] = None,
        mirror_id: Optional[str] = None,
        custom_url: Optional[str] = None,
        depth: Optional[int] = None,
        operation: str = "install",
        plugin_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        克隆 GitHub 仓库

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            target_path: 目标路径
            branch: 分支名称（可选）
            mirror_id: 指定的镜像源 ID
            custom_url: 自定义克隆 URL
            depth: 克隆深度（浅克隆）
            operation: 进度推送的操作类型
            plugin_id: 当前安装或更新的插件 ID，用于前端定位进度卡片

        Returns:
            Dict 包含:
                - success: bool - 是否成功
                - path: str - 克隆路径（成功时）
                - error: str - 错误信息（失败时）
                - mirror_used: str - 使用的镜像源
                - attempts: int - 尝试次数
        """
        logger.info(f"开始克隆仓库: {owner}/{repo} 到 {target_path}")

        if custom_url:
            try:
                custom_url = _validate_custom_outbound_url(custom_url)
            except ValueError as e:
                return {
                    "success": False,
                    "error": str(e),
                    "mirror_used": "custom",
                    "attempts": 0,
                    "url": custom_url,
                    "status_code": 400,
                }

            return await self._clone_with_url(
                custom_url,
                target_path,
                branch,
                depth,
                "custom",
                operation,
                plugin_id=plugin_id,
                mirror_name="自定义源",
                mirror_index=1,
                total_mirrors=1,
            )

        # 插件下载固定使用设置中唯一选中的源，旧请求的 mirror_id 不再覆盖全局选择。
        from src.plugin_market.sources import download_source

        source = await asyncio.to_thread(download_source)
        return await self._clone_from_mirror(
            owner, repo, target_path, branch, depth, source, operation,
            plugin_id=plugin_id, mirror_index=1, total_mirrors=1,
        )

    async def _clone_from_mirror(
        self,
        owner: str,
        repo: str,
        target_path: Path,
        branch: Optional[str],
        depth: Optional[int],
        source: GitSource,
        operation: str = "install",
        plugin_id: Optional[str] = None,
        mirror_index: Optional[int] = None,
        total_mirrors: Optional[int] = None,
    ) -> Dict[str, Any]:
        """从指定镜像源克隆仓库"""
        try:
            url = await asyncio.to_thread(_validate_custom_outbound_url, source.clone_url(owner, repo), source.name)
        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
                "mirror_used": source.id,
                "attempts": 0,
                "status_code": 400,
            }

        return await self._clone_with_url(
            url,
            target_path,
            branch,
            depth,
            source.id,
            operation,
            plugin_id=plugin_id,
            mirror_name=source.name,
            mirror_index=mirror_index,
            total_mirrors=total_mirrors,
        )

    async def _clone_with_url(
        self,
        url: str,
        target_path: Path,
        branch: Optional[str],
        depth: Optional[int],
        mirror_type: str,
        operation: str = "install",
        plugin_id: Optional[str] = None,
        mirror_name: Optional[str] = None,
        mirror_index: Optional[int] = None,
        total_mirrors: Optional[int] = None,
    ) -> Dict[str, Any]:
        """使用指定 URL 克隆仓库，支持重试"""
        attempts = 0
        last_error = None

        for attempt in range(self.max_retries):
            attempts += 1

            try:
                # 确保目标路径不存在
                if target_path.exists():
                    logger.warning(f"目标路径已存在，删除: {target_path}")
                    shutil.rmtree(target_path, ignore_errors=True)

                # 构建 git clone 命令
                cmd = ["git", "clone"]

                # 添加分支参数
                if branch:
                    cmd.extend(["-b", branch])

                # 添加深度参数（浅克隆）
                if depth:
                    cmd.extend(["--depth", str(depth)])

                # 添加 URL 和目标路径
                cmd.extend([url, str(target_path)])

                logger.info(f"尝试克隆 #{attempt + 1}: {' '.join(cmd)}")

                # 推送进度
                if _update_progress:
                    try:
                        mirror_progress_base = 20
                        if mirror_index is not None and total_mirrors:
                            mirror_progress_base = 20 + int((mirror_index - 1) / total_mirrors * 60)
                        attempt_progress = int((attempt / max(self.max_retries, 1)) * 15)
                        await _update_progress(
                            stage="loading",
                            progress=min(mirror_progress_base + attempt_progress, 82),
                            message=(
                                f"正在从 {mirror_name or mirror_type} 克隆仓库"
                                f"（镜像源 {mirror_index or 1}/{total_mirrors or 1}，"
                                f"尝试 {attempt + 1}/{self.max_retries}）..."
                            ),
                            operation=operation,
                            plugin_id=plugin_id,
                            mirror_id=mirror_type,
                            mirror_name=mirror_name or mirror_type,
                            mirror_index=mirror_index,
                            total_mirrors=total_mirrors,
                            attempt=attempt + 1,
                            max_attempts=self.max_retries,
                        )
                    except Exception as e:
                        logger.warning(f"推送进度失败: {e}")

                # 执行 git clone（在线程池中运行以避免阻塞），取消时终止进程树。
                process = await _run_git_process(cmd)

                if process.returncode == 0:
                    logger.info(f"成功克隆仓库: {url} -> {target_path}")
                    if _update_progress:
                        try:
                            await _update_progress(
                                stage="loading",
                                progress=82,
                                message=f"已从 {mirror_name or mirror_type} 克隆完成，正在校验插件文件...",
                                operation=operation,
                                plugin_id=plugin_id,
                                mirror_id=mirror_type,
                                mirror_name=mirror_name or mirror_type,
                                mirror_index=mirror_index,
                                total_mirrors=total_mirrors,
                                attempt=attempts,
                                max_attempts=self.max_retries,
                            )
                        except Exception as e:
                            logger.warning(f"推送进度失败: {e}")
                    return {
                        "success": True,
                        "path": str(target_path),
                        "mirror_used": mirror_type,
                        "attempts": attempts,
                        "url": url,
                        "branch": branch or "default",
                    }
                else:
                    last_error = f"Git 克隆失败: {process.stderr}"
                    logger.warning(f"克隆失败 (尝试 {attempt + 1}/{self.max_retries}): {last_error}")

                    # git clone 失败时可能已经创建 .git 等半成品，立即清理避免下次安装误判为插件已存在。
                    if target_path.exists():
                        shutil.rmtree(target_path, ignore_errors=True)

                    if _update_progress and attempt + 1 < self.max_retries:
                        try:
                            mirror_progress_base = 20
                            if mirror_index is not None and total_mirrors:
                                mirror_progress_base = 20 + int((mirror_index - 1) / total_mirrors * 60)
                            await _update_progress(
                                stage="loading",
                                progress=min(mirror_progress_base + int((attempt + 1) / self.max_retries * 15), 82),
                                message=(
                                    f"{mirror_name or mirror_type} 克隆失败，"
                                    f"准备重试 {attempt + 2}/{self.max_retries}"
                                ),
                                operation=operation,
                                plugin_id=plugin_id,
                                error=last_error,
                                mirror_id=mirror_type,
                                mirror_name=mirror_name or mirror_type,
                                mirror_index=mirror_index,
                                total_mirrors=total_mirrors,
                                attempt=attempt + 1,
                                max_attempts=self.max_retries,
                            )
                        except Exception as e:
                            logger.warning(f"推送进度失败: {e}")

            except asyncio.CancelledError:
                # Git 已退出后才删除半成品，避免子进程继续写入临时目录。
                if target_path.exists():
                    await asyncio.to_thread(shutil.rmtree, target_path)
                raise

            except subprocess.TimeoutExpired:
                last_error = "克隆超时（超过 5 分钟）"
                logger.warning(f"克隆超时 (尝试 {attempt + 1}/{self.max_retries})")

                # 清理可能的部分克隆
                if target_path.exists():
                    shutil.rmtree(target_path, ignore_errors=True)

            except FileNotFoundError:
                last_error = "Git 未安装或不在 PATH 中"
                logger.error(f"Git 未找到: {last_error}")
                break  # Git 不存在，不需要重试

            except Exception as e:
                last_error = f"未知错误: {e}"
                logger.error(f"克隆错误 (尝试 {attempt + 1}/{self.max_retries}): {last_error}")

                # 清理可能的部分克隆
                if target_path.exists():
                    shutil.rmtree(target_path, ignore_errors=True)

        return {"success": False, "error": last_error, "mirror_used": mirror_type, "attempts": attempts, "url": url}


# 全局服务实例
_git_mirror_service: Optional[GitMirrorService] = None


def get_git_mirror_service() -> GitMirrorService:
    """获取 Git 镜像源服务实例（单例）"""
    global _git_mirror_service
    if _git_mirror_service is None:
        _git_mirror_service = GitMirrorService()
    return _git_mirror_service
