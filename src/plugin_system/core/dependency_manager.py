import importlib.metadata
import sys
import asyncio
import shutil
import time
import os
import urllib.parse
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import aiohttp
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from src.common.logger import get_logger
from src.plugin_system.base.component_types import PythonDependency

logger = get_logger("dependency_manager")

@dataclass
class DependencyInfo:
    """依赖信息
    
    Attributes:
        name (str): 依赖包名称
        required_version (str): 要求的版本范围
        installed_version (Optional[str]): 已安装的版本
        is_satisfied (bool): 是否满足依赖要求
        install_name (str): 用于pip安装的名称
    """
    name: str
    required_version: str
    installed_version: Optional[str] = None
    is_satisfied: bool = False
    install_name: str = "" # 用于pip安装的名称

class MirrorManager:
    """PyPI镜像源管理器"""

    MIRRORS = {
        "aliyun": "https://mirrors.aliyun.com/pypi/simple",
        "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
        "huawei": "https://repo.huaweicloud.com/repository/pypi/simple",
        "official": "https://pypi.org/simple"
    }

    def __init__(self):
        self._fastest_mirror: Optional[str] = None

    async def get_fastest_mirror(self) -> str:
        """获取最快的镜像源
        
        如果已经测试过，直接返回缓存的结果。
        否则并发测试所有镜像源的响应速度。
        """
        if self._fastest_mirror:
            return self._fastest_mirror

        logger.info("正在测试PyPI镜像源速度...")

        async def test_mirror(url: str) -> Tuple[str, float]:
            try:
                start = time.time()
                # 禁用 SSL 验证以避免证书问题干扰测速
                # trust_env=True 让 aiohttp 读取系统代理设置 (HTTP_PROXY/HTTPS_PROXY)，解决 pip 能连但测速连不上的问题
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=False),
                    trust_env=True
                ) as session:
                    async with session.head(url, timeout=3, allow_redirects=True) as response:
                        if response.status == 200:
                            latency = time.time() - start
                            logger.debug(f"镜像源 {url} 测速成功，延迟: {latency*1000:.2f}ms")
                            return url, latency
                        logger.debug(f"镜像源 {url} 测速失败，状态码: {response.status}")
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f"镜像源 {url} 连接异常: {repr(e)}")
                pass
            return url, float('inf')

        tasks = [test_mirror(url) for _, url in self.MIRRORS.items()]
        results = await asyncio.gather(*tasks)

        # 过滤掉超时的inf,按时间排序
        valid_results = sorted([r for r in results if r[1] != float('inf')], key=lambda x: x[1])

        if valid_results:
            best_url, latency = valid_results[0]
            logger.info(f"选用最快镜像源: {best_url} (延迟: {latency*1000:.2f}ms)")
            self._fastest_mirror = best_url
            return best_url

        # 默认回退到阿里云
        logger.warning("所有镜像源测速失败，默认使用阿里云源")
        return self.MIRRORS["aliyun"]

class PluginDependencyManager:
    """插件依赖管理器
    
    负责管理插件的Python依赖，包括扫描已安装包、检查依赖状态以及自动安装缺失依赖。
    """

    def __init__(self):
        self._installed_packages: Dict[str, str] = {}
        self.mirror_manager = MirrorManager()
        self.uv_path = shutil.which("uv")
        if self.uv_path:
            logger.info(f"检测到 uv 工具: {self.uv_path}")
        else:
            logger.info("未检测到 uv 工具，将使用 pip")
        self.scan_installed_packages()

    def scan_installed_packages(self) -> Dict[str, str]:
        """扫描已安装的所有Python包。
        
        使用 importlib.metadata.distributions() 获取所有已安装的包，
        并将包名规范化以便后续匹配。

        Returns:
            Dict[str, str]: 包含 {规范化包名: 版本号} 的字典。
        """
        self._installed_packages = {}
        try:
            for dist in importlib.metadata.distributions():
                # 使用 packaging.utils.canonicalize_name 规范化包名
                name = canonicalize_name(dist.metadata['Name'])
                version = dist.version
                self._installed_packages[name] = version
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"扫描已安装包失败: {e}")
        return self._installed_packages

    def parse_plugin_dependencies(
        self,
        python_dependencies: List[PythonDependency]
        ) -> List[DependencyInfo]:
        """解析插件配置中的依赖信息。

        Args:
            python_dependencies: 插件定义的Python依赖列表。

        Returns:
            List[DependencyInfo]: 解析后的依赖详细信息列表。
        """
        dependencies = []
        for dep in python_dependencies:
            # 使用 install_name 进行查找，因为它是 PyPI 包名
            # 并使用 canonicalize_name 规范化
            target_name = dep.install_name or dep.package_name
            pkg_name_canonical = canonicalize_name(target_name)

            installed_version = self._installed_packages.get(pkg_name_canonical)

            # 构建版本要求字符串
            specifier_str = dep.version if dep.version else ""

            is_satisfied = False
            if installed_version:
                if not specifier_str:
                    is_satisfied = True
                else:
                    try:
                        spec = SpecifierSet(specifier_str)
                        is_satisfied = spec.contains(installed_version)
                    except Exception as e:  # pylint: disable=broad-except
                        logger.warning(f"版本说明符解析失败 {dep.package_name} {specifier_str}: {e}")
                        # 如果解析失败，保守起见认为不满足，或者根据策略处理
                        is_satisfied = False

            dependencies.append(DependencyInfo(
                name=dep.package_name,
                required_version=specifier_str,
                installed_version=installed_version,
                is_satisfied=is_satisfied,
                install_name=target_name
            ))
        return dependencies

    def check_dependencies(
        self,
        python_dependencies: List[PythonDependency]
    ) -> Tuple[List[DependencyInfo], List[DependencyInfo]]:
        """检查插件依赖是否满足。

        Args:
            python_dependencies: 插件定义的Python依赖列表。

        Returns:
            Tuple[List[DependencyInfo], List[DependencyInfo]]: 
                返回一个元组 (satisfied, unsatisfied)，
                分别包含满足和不满足的依赖信息列表。
        """
        dependencies = self.parse_plugin_dependencies(python_dependencies)
        satisfied = []
        unsatisfied = []

        for dep in dependencies:
            if dep.is_satisfied:
                satisfied.append(dep)
            else:
                unsatisfied.append(dep)

        return satisfied, unsatisfied

    async def _get_install_command(self, args: List[str], upgrade: bool = False) -> List[str]:
        """构建安装命令，自动选择 uv 或 pip，并添加镜像源"""
        mirror_url = await self.mirror_manager.get_fastest_mirror()

        cmd = []
        if self.uv_path:
            # uv pip install ...
            cmd = [self.uv_path, "pip", "install"]
        else:
            # python -m pip install ...
            cmd = [sys.executable, "-m", "pip", "install"]
            # 添加 pip 的安全/静默参数
            cmd.extend(["--disable-pip-version-check", "--no-input"])

        if upgrade:
            cmd.append("--upgrade")

        cmd.extend(["-i", mirror_url])

        # 自动添加 trusted-host 参数，解决部分镜像源 SSL 问题
        if mirror_url:
            try:
                host = urllib.parse.urlparse(mirror_url).hostname
                if host:
                    cmd.extend(["--trusted-host", host])
            except Exception:  # pylint: disable=broad-except
                pass

        cmd.extend(args)
        return cmd

    def _parse_pip_error(self, output: str) -> str:
        """解析并简化 pip 错误信息"""
        patterns = {
            "No matching distribution": "包不存在或版本不可用",
            "Could not find a version": "找不到指定版本",
            "SSL: CERTIFICATE_VERIFY_FAILED": "SSL 验证失败，尝试检查网络或镜像源",
            "403": "访问被拒绝（镜像源可能需要认证）",
            "404": "资源不存在",
            "Connection refused": "连接被拒绝，检查镜像源地址",
            "Network is unreachable": "网络不可达",
            "Permission denied": "权限不足，请尝试使用管理员权限运行",
        }
        for k, v in patterns.items():
            if k in output:
                return f"{v} (原始错误: {k})"

        # 如果没有匹配到已知模式，返回截断的错误信息
        return output[:300] + "..." if len(output) > 300 else output

    async def install_from_file(self, file_path: str) -> bool:
        """从 requirements.txt 或 pyproject.toml 安装依赖

        Args:
            file_path: 依赖文件路径

        Returns:
            bool: 安装是否成功
        """
        if not file_path:
            return False

        logger.info(f"正在从文件安装依赖: {file_path}")

        # 构建参数
        args = ["-r", file_path]

        # 如果是 pyproject.toml 且使用 pip，可能需要不同的处理
        # 但 uv pip install -r pyproject.toml 是支持的
        # 对于 pyproject.toml，通常是 pip install .
        # 但这里我们假设用户希望安装依赖，而不是安装包本身
        # 如果是 pyproject.toml，尝试直接作为 requirements 传入

        cmd = await self._get_install_command(args, upgrade=True)

        return await self._run_install_command(cmd)

    async def install_auto_from_directory(self, plugin_dir: str) -> bool:
        """自动检测并安装插件目录下的依赖文件        
        优先检查 pyproject.toml，其次检查 requirements.txt
        
        Args:
            plugin_dir: 插件目录路径
            
        Returns:
            bool: 如果找到文件且安装成功返回 True，未找到文件返回 True (视为成功)，安装失败返回 False
        """


        dependency_file = None
        pyproject_path = os.path.join(plugin_dir, "pyproject.toml")
        requirements_path = os.path.join(plugin_dir, "requirements.txt")

        if os.path.exists(pyproject_path):
            dependency_file = pyproject_path
        elif os.path.exists(requirements_path):
            dependency_file = requirements_path

        if not dependency_file:
            return True

        logger.info(f"在 {plugin_dir} 检测到依赖文件: {dependency_file}")
        return await self.install_from_file(dependency_file)

    async def install_dependencies(
        self, 
        dependencies: List[DependencyInfo],
        *,
        upgrade: bool = False
    ) -> bool:
        """安装缺失或版本不匹配的依赖。
        
        Args:
            dependencies: 需要安装的依赖信息列表。
            upgrade: 是否使用 --upgrade 参数升级包。

        Returns:
            bool: 安装是否成功。
        """
        if not dependencies:
            return True

        packages_to_install = []
        for dep in dependencies:
            pkg_str = dep.install_name
            if dep.required_version:
                if any(op in dep.required_version for op in ['=', '>', '<', '~', '!']):
                    pkg_str += dep.required_version
                else:
                    pkg_str += f"=={dep.required_version}"
            packages_to_install.append(pkg_str)

        cmd = await self._get_install_command(packages_to_install, upgrade=upgrade)

        logger.info(f"正在自动安装依赖: {' '.join(packages_to_install)}")
        return await self._run_install_command(cmd)

    async def _run_install_command(self, cmd: List[str]) -> bool:
        """执行安装命令"""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info("依赖安装成功")
                # 安装成功后重新扫描已安装包
                self.scan_installed_packages()
                return True
            else:
                error_msg = stderr.decode()
                friendly_error = self._parse_pip_error(error_msg)
                logger.error(f"依赖安装失败: {friendly_error}")
                logger.debug(f"完整错误日志: {error_msg}")
                return False
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"执行安装命令失败: {e}")
            return False

# 全局依赖管理器实例
plugin_dependency_manager = PluginDependencyManager()
