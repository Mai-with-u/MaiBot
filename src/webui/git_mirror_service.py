"""Git 镜像源服务 - 支持多镜像源、错误重试、Git 克隆和 Raw 文件获取"""

from typing import Optional, List, Dict, Any
from enum import Enum
import httpx
import json
import asyncio
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from src.common.logger import get_logger

logger = get_logger("webui.git_mirror")

# 导入进度更新函数（避免循环导入）
_update_progress = None


def set_update_progress_callback(callback):
    """设置进度更新回调函数"""
    global _update_progress
    _update_progress = callback


class MirrorType(str, Enum):
    """镜像源类型"""

    GH_PROXY = "gh-proxy"  # gh-proxy 主节点
    HK_GH_PROXY = "hk-gh-proxy"  # gh-proxy 香港节点
    CDN_GH_PROXY = "cdn-gh-proxy"  # gh-proxy CDN 节点
    EDGEONE_GH_PROXY = "edgeone-gh-proxy"  # gh-proxy EdgeOne 节点
    MEYZH_GITHUB = "meyzh-github"  # Meyzh GitHub 镜像
    GITHUB = "github"  # GitHub 官方源（兜底）
    CUSTOM = "custom"  # 自定义镜像源


class GitMirrorConfig:
    """Git 镜像源配置管理"""

    # 配置文件路径
    CONFIG_FILE = Path("data/webui.json")

    # 默认镜像源配置
    DEFAULT_MIRRORS = [
        {
            "id": "gh-proxy",
            "name": "gh-proxy 镜像",
            "raw_prefix": "https://gh-proxy.org/https://raw.githubusercontent.com",
            "clone_prefix": "https://gh-proxy.org/https://github.com",
            "enabled": True,
            "priority": 1,
            "created_at": None,
        },
        {
            "id": "hk-gh-proxy",
            "name": "gh-proxy 香港节点",
            "raw_prefix": "https://hk.gh-proxy.org/https://raw.githubusercontent.com",
            "clone_prefix": "https://hk.gh-proxy.org/https://github.com",
            "enabled": True,
            "priority": 2,
            "created_at": None,
        },
        {
            "id": "cdn-gh-proxy",
            "name": "gh-proxy CDN 节点",
            "raw_prefix": "https://cdn.gh-proxy.org/https://raw.githubusercontent.com",
            "clone_prefix": "https://cdn.gh-proxy.org/https://github.com",
            "enabled": True,
            "priority": 3,
            "created_at": None,
        },
        {
            "id": "edgeone-gh-proxy",
            "name": "gh-proxy EdgeOne 节点",
            "raw_prefix": "https://edgeone.gh-proxy.org/https://raw.githubusercontent.com",
            "clone_prefix": "https://edgeone.gh-proxy.org/https://github.com",
            "enabled": True,
            "priority": 4,
            "created_at": None,
        },
        {
            "id": "meyzh-github",
            "name": "Meyzh GitHub 镜像",
            "raw_prefix": "https://meyzh.github.io/https://raw.githubusercontent.com",
            "clone_prefix": "https://meyzh.github.io/https://github.com",
            "enabled": True,
            "priority": 5,
            "created_at": None,
        },
        {
            "id": "github",
            "name": "GitHub 官方源（兜底）",
            "raw_prefix": "https://raw.githubusercontent.com",
            "clone_prefix": "https://github.com",
            "enabled": True,
            "priority": 999,
            "created_at": None,
        },
    ]

    def __init__(self):
        """初始化配置管理器"""
        self.config_file = self.CONFIG_FILE
        self.mirrors: List[Dict[str, Any]] = []
        self._load_config()

    def _load_config(self) -> None:
        """加载配置文件"""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # 检查是否有镜像源配置
                if "git_mirrors" not in data or not data["git_mirrors"]:
                    logger.info("配置文件中未找到镜像源配置，使用默认配置")
                    self._init_default_mirrors()
                else:
                    self.mirrors = data["git_mirrors"]
                    logger.info(f"已加载 {len(self.mirrors)} 个镜像源配置")
            else:
                logger.info("配置文件不存在，创建默认配置")
                self._init_default_mirrors()
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self._init_default_mirrors()

    def _init_default_mirrors(self) -> None:
        """初始化默认镜像源"""
        current_time = datetime.now().isoformat()
        self.mirrors = []

        for mirror in self.DEFAULT_MIRRORS:
            mirror_copy = mirror.copy()
            mirror_copy["created_at"] = current_time
            self.mirrors.append(mirror_copy)

        self._save_config()
        logger.info(f"已初始化 {len(self.mirrors)} 个默认镜像源")

    def _save_config(self) -> None:
        """保存配置到文件"""
        try:
            # 确保目录存在
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            # 读取现有配置
            existing_data = {}
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)

            # 更新镜像源配置
            existing_data["git_mirrors"] = self.mirrors

            # 写入文件
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)

            logger.debug(f"配置已保存到 {self.config_file}")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def get_all_mirrors(self) -> List[Dict[str, Any]]:
        """获取所有镜像源"""
        return self.mirrors.copy()

    def get_enabled_mirrors(self) -> List[Dict[str, Any]]:
        """获取所有启用的镜像源，按优先级排序"""
        enabled = [m for m in self.mirrors if m.get("enabled", False)]
        return sorted(enabled, key=lambda x: x.get("priority", 999))

    def get_mirror_by_id(self, mirror_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取镜像源"""
        for mirror in self.mirrors:
            if mirror.get("id") == mirror_id:
                return mirror.copy()
        return None

    def add_mirror(
        self,
        mirror_id: str,
        name: str,
        raw_prefix: str,
        clone_prefix: str,
        enabled: bool = True,
        priority: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        添加新的镜像源

        Returns:
            添加的镜像源配置

        Raises:
            ValueError: 如果镜像源 ID 已存在
        """
        # 检查 ID 是否已存在
        if self.get_mirror_by_id(mirror_id):
            raise ValueError(f"镜像源 ID 已存在: {mirror_id}")

        # 如果未指定优先级，使用最大优先级 + 1
        if priority is None:
            max_priority = max((m.get("priority", 0) for m in self.mirrors), default=0)
            priority = max_priority + 1

        new_mirror = {
            "id": mirror_id,
            "name": name,
            "raw_prefix": raw_prefix,
            "clone_prefix": clone_prefix,
            "enabled": enabled,
            "priority": priority,
            "created_at": datetime.now().isoformat(),
        }

        self.mirrors.append(new_mirror)
        self._save_config()

        logger.info(f"已添加镜像源: {mirror_id} - {name}")
        return new_mirror.copy()

    def update_mirror(
        self,
        mirror_id: str,
        name: Optional[str] = None,
        raw_prefix: Optional[str] = None,
        clone_prefix: Optional[str] = None,
        enabled: Optional[bool] = None,
        priority: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        更新镜像源配置

        Returns:
            更新后的镜像源配置，如果不存在则返回 None
        """
        for mirror in self.mirrors:
            if mirror.get("id") == mirror_id:
                if name is not None:
                    mirror["name"] = name
                if raw_prefix is not None:
                    mirror["raw_prefix"] = raw_prefix
                if clone_prefix is not None:
                    mirror["clone_prefix"] = clone_prefix
                if enabled is not None:
                    mirror["enabled"] = enabled
                if priority is not None:
                    mirror["priority"] = priority

                mirror["updated_at"] = datetime.now().isoformat()
                self._save_config()

                logger.info(f"已更新镜像源: {mirror_id}")
                return mirror.copy()

        return None

    def delete_mirror(self, mirror_id: str) -> bool:
        """
        删除镜像源

        Returns:
            True 如果删除成功，False 如果镜像源不存在
        """
        for i, mirror in enumerate(self.mirrors):
            if mirror.get("id") == mirror_id:
                self.mirrors.pop(i)
                self._save_config()
                logger.info(f"已删除镜像源: {mirror_id}")
                return True

        return False

    def get_default_priority_list(self) -> List[str]:
        """获取默认优先级列表（仅启用的镜像源 ID）"""
        enabled = self.get_enabled_mirrors()
        return [m["id"] for m in enabled]


class GitMirrorService:
    """Git 镜像源服务"""

    def __init__(self, max_retries: int = 3, timeout: int = 30, config: Optional[GitMirrorConfig] = None):
        """
        初始化 Git 镜像源服务

        Args:
            max_retries: 最大重试次数
            timeout: 请求超时时间（秒）
            config: 镜像源配置管理器（可选，默认创建新实例）
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.config = config or GitMirrorConfig()
        logger.info(f"Git镜像源服务初始化完成，已加载 {len(self.config.get_enabled_mirrors())} 个启用的镜像源")

    def get_mirror_config(self) -> GitMirrorConfig:
        """获取镜像源配置管理器"""
        return self.config

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
        import subprocess
        import shutil

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

    async def fetch_raw_file(
        self,
        owner: str,
        repo: str,
        branch: str,
        file_path: str,
        mirror_id: Optional[str] = None,
        custom_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取 GitHub 仓库的 Raw 文件内容

        Args:
            owner: 仓库所有者
            repo: 仓库名称
            branch: 分支名称
            file_path: 文件路径
            mirror_id: 指定的镜像源 ID
            custom_url: 自定义完整 URL（如果提供，将忽略其他参数）

        Returns:
            Dict 包含:
                - success: bool - 是否成功
                - data: str - 文件内容（成功时）
                - error: str - 错误信息（失败时）
                - mirror_used: str - 使用的镜像源
                - attempts: int - 尝试次数
        """
        logger.info(f"开始获取 Raw 文件: {owner}/{repo}/{branch}/{file_path}")

        if custom_url:
            # 使用自定义 URL
            return await self._fetch_with_url(custom_url, "custom")

        # 确定要使用的镜像源列表
        if mirror_id:
            # 使用指定的镜像源
            mirror = self.config.get_mirror_by_id(mirror_id)
            if not mirror:
                return {"success": False, "error": f"未找到镜像源: {mirror_id}", "mirror_used": None, "attempts": 0}
            mirrors_to_try = [mirror]
        else:
            # 使用所有启用的镜像源
            mirrors_to_try = self.config.get_enabled_mirrors()

        total_mirrors = len(mirrors_to_try)

        # 依次尝试每个镜像源
        for index, mirror in enumerate(mirrors_to_try, 1):
            # 推送进度：正在尝试第 N 个镜像源
            if _update_progress:
                try:
                    progress = 30 + int((index - 1) / total_mirrors * 40)  # 30% - 70%
                    await _update_progress(
                        stage="loading",
                        progress=progress,
                        message=f"正在尝试镜像源 {index}/{total_mirrors}: {mirror['name']}",
                        total_plugins=0,
                        loaded_plugins=0,
                    )
                except Exception as e:
                    logger.warning(f"推送进度失败: {e}")

            result = await self._fetch_raw_from_mirror(owner, repo, branch, file_path, mirror)

            if result["success"]:
                # 成功，推送进度
                if _update_progress:
                    try:
                        await _update_progress(
                            stage="loading",
                            progress=70,
                            message=f"成功从 {mirror['name']} 获取数据",
                            total_plugins=0,
                            loaded_plugins=0,
                        )
                    except Exception as e:
                        logger.warning(f"推送进度失败: {e}")
                return result

            # 失败，记录日志并推送失败信息
            logger.warning(f"镜像源 {mirror['id']} 失败: {result.get('error')}")

            if _update_progress and index < total_mirrors:
                try:
                    await _update_progress(
                        stage="loading",
                        progress=30 + int(index / total_mirrors * 40),
                        message=f"镜像源 {mirror['name']} 失败，尝试下一个...",
                        total_plugins=0,
                        loaded_plugins=0,
                    )
                except Exception as e:
                    logger.warning(f"推送进度失败: {e}")

        # 所有镜像源都失败
        return {"success": False, "error": "所有镜像源均失败", "mirror_used": None, "attempts": len(mirrors_to_try)}

    async def _fetch_raw_from_mirror(
        self, owner: str, repo: str, branch: str, file_path: str, mirror: Dict[str, Any]
    ) -> Dict[str, Any]:
        """从指定镜像源获取文件"""
        # 构建 URL
        raw_prefix = mirror["raw_prefix"]
        url = f"{raw_prefix}/{owner}/{repo}/{branch}/{file_path}"

        return await self._fetch_with_url(url, mirror["id"])

    async def _fetch_with_url(self, url: str, mirror_type: str) -> Dict[str, Any]:
        """使用指定 URL 获取文件，支持重试"""
        attempts = 0
        last_error = None

        for attempt in range(self.max_retries):
            attempts += 1
            try:
                logger.debug(f"尝试 #{attempt + 1}: {url}")
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url)
                    response.raise_for_status()

                    logger.info(f"成功获取文件: {url}")
                    return {
                        "success": True,
                        "data": response.text,
                        "mirror_used": mirror_type,
                        "attempts": attempts,
                        "url": url,
                    }
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}: {e}"
                logger.warning(f"HTTP 错误 (尝试 {attempt + 1}/{self.max_retries}): {last_error}")
            except httpx.TimeoutException as e:
                last_error = f"请求超时: {e}"
                logger.warning(f"超时 (尝试 {attempt + 1}/{self.max_retries}): {last_error}")
            except Exception as e:
                last_error = f"未知错误: {e}"
                logger.error(f"错误 (尝试 {attempt + 1}/{self.max_retries}): {last_error}")

        return {"success": False, "error": last_error, "mirror_used": mirror_type, "attempts": attempts, "url": url}

    async def clone_repository(
        self,
        owner: str,
        repo: str,
        target_path: Path,
        branch: Optional[str] = None,
        mirror_id: Optional[str] = None,
        custom_url: Optional[str] = None,
        depth: Optional[int] = None,
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
            # 使用自定义 URL
            return await self._clone_with_url(custom_url, target_path, branch, depth, "custom")

        # 确定要使用的镜像源列表
        if mirror_id:
            # 使用指定的镜像源
            mirror = self.config.get_mirror_by_id(mirror_id)
            if not mirror:
                return {"success": False, "error": f"未找到镜像源: {mirror_id}", "mirror_used": None, "attempts": 0}
            mirrors_to_try = [mirror]
        else:
            # 使用所有启用的镜像源
            mirrors_to_try = self.config.get_enabled_mirrors()

        # 依次尝试每个镜像源
        for mirror in mirrors_to_try:
            result = await self._clone_from_mirror(owner, repo, target_path, branch, depth, mirror)
            if result["success"]:
                return result
            logger.warning(f"镜像源 {mirror['id']} 克隆失败: {result.get('error')}")

        # 所有镜像源都失败
        return {"success": False, "error": "所有镜像源克隆均失败", "mirror_used": None, "attempts": len(mirrors_to_try)}

    async def _clone_from_mirror(
        self,
        owner: str,
        repo: str,
        target_path: Path,
        branch: Optional[str],
        depth: Optional[int],
        mirror: Dict[str, Any],
    ) -> Dict[str, Any]:
        """从指定镜像源克隆仓库"""
        # 构建克隆 URL
        clone_prefix = mirror["clone_prefix"]
        url = f"{clone_prefix}/{owner}/{repo}.git"

        return await self._clone_with_url(url, target_path, branch, depth, mirror["id"])

    async def _clone_with_url(
        self, url: str, target_path: Path, branch: Optional[str], depth: Optional[int], mirror_type: str
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
                        await _update_progress(
                            stage="loading",
                            progress=20 + attempt * 10,
                            message=f"正在克隆仓库 (尝试 {attempt + 1}/{self.max_retries})...",
                            operation="install",
                        )
                    except Exception as e:
                        logger.warning(f"推送进度失败: {e}")

                # 执行 git clone（在线程池中运行以避免阻塞）
                loop = asyncio.get_event_loop()

                def run_git_clone(clone_cmd=cmd):
                    return subprocess.run(
                        clone_cmd,
                        capture_output=True,
                        text=True,
                        timeout=300,  # 5分钟超时
                    )

                process = await loop.run_in_executor(None, run_git_clone)

                if process.returncode == 0:
                    logger.info(f"成功克隆仓库: {url} -> {target_path}")
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
