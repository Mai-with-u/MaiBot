"""
WebUI 防爬虫模块
提供爬虫检测和阻止功能，保护 WebUI 不被搜索引擎和恶意爬虫访问
"""

import time
import ipaddress
import re
from collections import deque
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from src.common.logger import get_logger

logger = get_logger("webui.anti_crawler")

# 常见爬虫 User-Agent 列表（使用更精确的关键词，避免误报）
CRAWLER_USER_AGENTS = {
    # 搜索引擎爬虫（精确匹配）
    "googlebot",
    "bingbot",
    "baiduspider",
    "yandexbot",
    "slurp",  # Yahoo
    "duckduckbot",
    "sogou",
    "exabot",
    "facebot",
    "ia_archiver",  # Internet Archive
    # 通用爬虫（移除过于宽泛的关键词）
    "crawler",
    "spider",
    "scraper",
    "wget",  # 保留wget，因为通常用于自动化脚本
    "scrapy",  # 保留scrapy，因为这是爬虫框架
    # 安全扫描工具（这些是明确的扫描工具）
    "masscan",
    "nmap",
    "nikto",
    "sqlmap",
    # 注意：移除了以下过于宽泛的关键词以避免误报：
    # - "bot" (会误匹配GitHub-Robot等)
    # - "curl" (正常工具)
    # - "python-requests" (正常库)
    # - "httpx" (正常库)
    # - "aiohttp" (正常库)
}

# 资产测绘工具 User-Agent 标识
ASSET_SCANNER_USER_AGENTS = {
    # 知名资产测绘平台
    "shodan",
    "censys",
    "zoomeye",
    "fofa",
    "quake",
    "hunter",
    "binaryedge",
    "onyphe",
    "securitytrails",
    "virustotal",
    "passivetotal",
    # 安全扫描工具
    "acunetix",
    "appscan",
    "burpsuite",
    "nessus",
    "openvas",
    "qualys",
    "rapid7",
    "tenable",
    "veracode",
    "zap",
    "awvs",  # Acunetix Web Vulnerability Scanner
    "netsparker",
    "skipfish",
    "w3af",
    "arachni",
    # 其他扫描工具
    "masscan",
    "zmap",
    "nmap",
    "whatweb",
    "wpscan",
    "joomscan",
    "dnsenum",
    "subfinder",
    "amass",
    "sublist3r",
    "theharvester",
}

# 资产测绘工具常用的HTTP头标识
ASSET_SCANNER_HEADERS = {
    # 常见的扫描工具自定义头
    "x-scan": {"shodan", "censys", "zoomeye", "fofa"},
    "x-scanner": {"nmap", "masscan", "zmap"},
    "x-probe": {"masscan", "zmap"},
    # 其他可疑头（移除反向代理标准头）
    "x-originating-ip": set(),
    "x-remote-ip": set(),
    "x-remote-addr": set(),
    # 注意：移除了以下反向代理标准头以避免误报：
    # - "x-forwarded-proto" (反向代理标准头)
    # - "x-real-ip" (反向代理标准头，已在_get_client_ip中使用)
}

# 仅检查特定HTTP头中的可疑模式（收紧匹配范围）
# 只检查这些特定头，不检查所有头
SCANNER_SPECIFIC_HEADERS = {
    "x-scan",
    "x-scanner",
    "x-probe",
    "x-originating-ip",
    "x-remote-ip",
    "x-remote-addr",
}

# 防爬虫模式配置
# false: 禁用
# strict: 严格模式（更严格的检测，更低的频率限制）
# loose: 宽松模式（较宽松的检测，较高的频率限制）
# basic: 基础模式（只记录恶意访问，不阻止，不限制请求数，不跟踪IP）


# IP白名单配置（从配置文件读取，逗号分隔）
# 支持格式：
# - 精确IP：127.0.0.1, 192.168.1.100
# - CIDR格式：192.168.1.0/24, 172.17.0.0/16 (适用于Docker网络)
# - 通配符：192.168.*.*, 10.*.*.*, *.*.*.* (匹配所有)
# - IPv6：::1, 2001:db8::/32
def _parse_allowed_ips(ip_string: str) -> list:
    """
    解析IP白名单字符串，支持精确IP、CIDR格式和通配符

    Args:
        ip_string: 逗号分隔的IP字符串

    Returns:
        IP白名单列表，每个元素可能是：
        - ipaddress.IPv4Network/IPv6Network对象（CIDR格式）
        - ipaddress.IPv4Address/IPv6Address对象（精确IP）
        - str（通配符模式，已转换为正则表达式）
    """
    allowed = []
    if not ip_string:
        return allowed

    for ip_entry in ip_string.split(","):
        ip_entry = ip_entry.strip()  # 去除空格
        if not ip_entry:
            continue

        # 跳过注释行（以#开头）
        if ip_entry.startswith("#"):
            continue

        # 检查通配符格式（包含*）
        if "*" in ip_entry:
            # 处理通配符
            pattern = _convert_wildcard_to_regex(ip_entry)
            if pattern:
                allowed.append(pattern)
            else:
                logger.warning(f"无效的通配符IP格式，已忽略: {ip_entry}")
            continue

        try:
            # 尝试解析为CIDR格式（包含/）
            if "/" in ip_entry:
                allowed.append(ipaddress.ip_network(ip_entry, strict=False))
            else:
                # 精确IP地址
                allowed.append(ipaddress.ip_address(ip_entry))
        except (ValueError, AttributeError) as e:
            logger.warning(f"无效的IP白名单条目，已忽略: {ip_entry} ({e})")

    return allowed


def _convert_wildcard_to_regex(wildcard_pattern: str) -> Optional[str]:
    """
    将通配符IP模式转换为正则表达式

    支持的格式：
    - 192.168.*.* 或 192.168.*
    - 10.*.*.* 或 10.*
    - *.*.*.* 或 *

    Args:
        wildcard_pattern: 通配符模式字符串

    Returns:
        正则表达式字符串，如果格式无效则返回None
    """
    # 去除空格
    pattern = wildcard_pattern.strip()

    # 处理单个*（匹配所有）
    if pattern == "*":
        return r".*"

    # 处理IPv4通配符格式
    # 支持：192.168.*.*, 192.168.*, 10.*.*.*, 10.* 等
    parts = pattern.split(".")

    if len(parts) > 4:
        return None  # IPv4最多4段

    # 构建正则表达式
    regex_parts = []
    for part in parts:
        part = part.strip()
        if part == "*":
            regex_parts.append(r"\d+")  # 匹配任意数字
        elif part.isdigit():
            # 验证数字范围（0-255）
            num = int(part)
            if 0 <= num <= 255:
                regex_parts.append(re.escape(part))
            else:
                return None  # 无效的数字
        else:
            return None  # 无效的格式

    # 如果部分少于4段，补充.*
    while len(regex_parts) < 4:
        regex_parts.append(r"\d+")

    # 组合成正则表达式
    regex = r"^" + r"\.".join(regex_parts) + r"$"
    return regex


# 从配置读取防爬虫设置（延迟导入避免循环依赖）
def _get_anti_crawler_config():
    """获取防爬虫配置"""
    from src.config.config import global_config

    return {
        "mode": global_config.webui.anti_crawler_mode,
        "allowed_ips": _parse_allowed_ips(global_config.webui.allowed_ips),
        "trusted_proxies": _parse_allowed_ips(global_config.webui.trusted_proxies),
        "trust_xff": global_config.webui.trust_xff,
    }


# 初始化配置（将在模块加载时执行）
_config = _get_anti_crawler_config()
ANTI_CRAWLER_MODE = _config["mode"]
ALLOWED_IPS = _config["allowed_ips"]
TRUSTED_PROXIES = _config["trusted_proxies"]
TRUST_XFF = _config["trust_xff"]


def _get_mode_config(mode: str) -> dict:
    """
    根据模式获取配置参数

    Args:
        mode: 防爬虫模式 (false/strict/loose/basic)

    Returns:
        配置字典，包含所有相关参数
    """
    mode = mode.lower()

    if mode == "false":
        return {
            "enabled": False,
            "rate_limit_window": 60,
            "rate_limit_max_requests": 1000,  # 禁用时设置很高的值
            "max_tracked_ips": 0,
            "check_user_agent": False,
            "check_asset_scanner": False,
            "check_rate_limit": False,
            "block_on_detect": False,  # 不阻止
        }
    elif mode == "strict":
        return {
            "enabled": True,
            "rate_limit_window": 60,
            "rate_limit_max_requests": 15,  # 严格模式：更低的请求数
            "max_tracked_ips": 20000,
            "check_user_agent": True,
            "check_asset_scanner": True,
            "check_rate_limit": True,
            "block_on_detect": True,  # 阻止恶意访问
        }
    elif mode == "loose":
        return {
            "enabled": True,
            "rate_limit_window": 60,
            "rate_limit_max_requests": 60,  # 宽松模式：更高的请求数
            "max_tracked_ips": 5000,
            "check_user_agent": True,
            "check_asset_scanner": True,
            "check_rate_limit": True,
            "block_on_detect": True,  # 阻止恶意访问
        }
    else:  # basic (默认模式)
        return {
            "enabled": True,
            "rate_limit_window": 60,
            "rate_limit_max_requests": 1000,  # 不限制请求数
            "max_tracked_ips": 0,  # 不跟踪IP
            "check_user_agent": True,  # 检测但不阻止
            "check_asset_scanner": True,  # 检测但不阻止
            "check_rate_limit": False,  # 不限制请求频率
            "block_on_detect": False,  # 只记录，不阻止
        }


class AntiCrawlerMiddleware(BaseHTTPMiddleware):
    """防爬虫中间件"""

    def __init__(self, app, mode: str = "standard"):
        """
        初始化防爬虫中间件

        Args:
            app: FastAPI 应用实例
            mode: 防爬虫模式 (false/strict/loose/standard)
        """
        super().__init__(app)
        self.mode = mode.lower()
        # 根据模式获取配置
        config = _get_mode_config(self.mode)
        self.enabled = config["enabled"]
        self.rate_limit_window = config["rate_limit_window"]
        self.rate_limit_max_requests = config["rate_limit_max_requests"]
        self.max_tracked_ips = config["max_tracked_ips"]
        self.check_user_agent = config["check_user_agent"]
        self.check_asset_scanner = config["check_asset_scanner"]
        self.check_rate_limit = config["check_rate_limit"]
        self.block_on_detect = config["block_on_detect"]  # 是否阻止检测到的恶意访问

        # 用于存储每个IP的请求时间戳（使用deque提高性能）
        self.request_times: dict[str, deque] = {}
        # 上次清理时间
        self.last_cleanup = time.time()
        # 将关键词列表转换为集合以提高查找性能
        self.crawler_keywords_set = set(CRAWLER_USER_AGENTS)
        self.scanner_keywords_set = set(ASSET_SCANNER_USER_AGENTS)

    def _is_crawler_user_agent(self, user_agent: Optional[str]) -> bool:
        """
        检测是否为爬虫 User-Agent

        Args:
            user_agent: User-Agent 字符串

        Returns:
            如果是爬虫则返回 True
        """
        if not user_agent:
            # 没有 User-Agent 的请求记录日志但不直接阻止
            # 改为只记录，让频率限制来处理
            logger.debug("请求缺少User-Agent")
            return False  # 不再直接阻止无User-Agent的请求

        user_agent_lower = user_agent.lower()

        # 使用集合查找提高性能（检查是否包含爬虫关键词）
        for crawler_keyword in self.crawler_keywords_set:
            if crawler_keyword in user_agent_lower:
                return True

        return False

    def _is_asset_scanner_header(self, request: Request) -> bool:
        """
        检测是否为资产测绘工具的HTTP头（只检查特定头，收紧匹配）

        Args:
            request: 请求对象

        Returns:
            如果检测到资产测绘工具头则返回 True
        """
        # 只检查特定的扫描工具头，不检查所有头
        for header_name, header_value in request.headers.items():
            header_name_lower = header_name.lower()
            header_value_lower = header_value.lower() if header_value else ""

            # 检查已知的扫描工具头
            if header_name_lower in ASSET_SCANNER_HEADERS:
                # 如果该头有特定的工具集合，检查值是否匹配
                expected_tools = ASSET_SCANNER_HEADERS[header_name_lower]
                if expected_tools:
                    for tool in expected_tools:
                        if tool in header_value_lower:
                            return True
                else:
                    # 如果没有特定工具集合，只要存在该头就视为可疑
                    if header_value_lower:
                        return True

            # 只检查特定头中的可疑模式（收紧匹配）
            if header_name_lower in SCANNER_SPECIFIC_HEADERS:
                # 检查头值中是否包含已知扫描工具名称
                for tool in self.scanner_keywords_set:
                    if tool in header_value_lower:
                        return True

        return False

    def _detect_asset_scanner(self, request: Request) -> tuple[bool, Optional[str]]:
        """
        检测资产测绘工具

        Args:
            request: 请求对象

        Returns:
            (是否检测到, 检测到的工具名称)
        """
        user_agent = request.headers.get("User-Agent")

        # 检查 User-Agent（使用集合查找提高性能）
        if user_agent:
            user_agent_lower = user_agent.lower()
            for scanner_keyword in self.scanner_keywords_set:
                if scanner_keyword in user_agent_lower:
                    return True, scanner_keyword

        # 检查HTTP头
        if self._is_asset_scanner_header(request):
            # 尝试从User-Agent或头中提取工具名称
            detected_tool = None
            if user_agent:
                user_agent_lower = user_agent.lower()
                for tool in self.scanner_keywords_set:
                    if tool in user_agent_lower:
                        detected_tool = tool
                        break

            # 检查HTTP头中的工具标识（只检查特定头）
            if not detected_tool:
                for header_name, header_value in request.headers.items():
                    header_name_lower = header_name.lower()
                    if header_name_lower in SCANNER_SPECIFIC_HEADERS:
                        header_value_lower = (header_value or "").lower()
                        for tool in self.scanner_keywords_set:
                            if tool in header_value_lower:
                                detected_tool = tool
                                break
                        if detected_tool:
                            break

            return True, detected_tool or "unknown_scanner"

        return False, None

    def _check_rate_limit(self, client_ip: str) -> bool:
        """
        检查请求频率限制

        Args:
            client_ip: 客户端IP地址

        Returns:
            如果超过限制则返回 True（需要阻止）
        """
        # 检查IP白名单
        if self._is_ip_allowed(client_ip):
            return False

        current_time = time.time()

        # 定期清理过期的请求记录（每5分钟清理一次）
        if current_time - self.last_cleanup > 300:
            self._cleanup_old_requests(current_time)
            self.last_cleanup = current_time

        # 限制跟踪的IP数量，防止内存泄漏
        if self.max_tracked_ips > 0 and len(self.request_times) > self.max_tracked_ips:
            # 清理最旧的记录（删除最久未访问的IP）
            self._cleanup_oldest_ips()

        # 获取或创建该IP的请求时间deque（不使用maxlen，避免限流变松）
        if client_ip not in self.request_times:
            self.request_times[client_ip] = deque()

        request_times = self.request_times[client_ip]

        # 移除时间窗口外的请求记录（从左侧弹出过期记录）
        while request_times and current_time - request_times[0] >= self.rate_limit_window:
            request_times.popleft()

        # 检查是否超过限制
        if len(request_times) >= self.rate_limit_max_requests:
            return True

        # 记录当前请求时间
        request_times.append(current_time)
        return False

    def _cleanup_old_requests(self, current_time: float):
        """清理过期的请求记录（只清理当前需要检查的IP，不全量遍历）"""
        # 这个方法现在主要用于定期清理，实际清理在_check_rate_limit中按需进行
        # 清理最久未访问的IP记录
        if len(self.request_times) > self.max_tracked_ips * 0.8:
            self._cleanup_oldest_ips()

    def _cleanup_oldest_ips(self):
        """清理最久未访问的IP记录（全量遍历找真正的oldest）"""
        if not self.request_times:
            return

        # 先收集空deque的IP（优先删除）
        empty_ips = []
        # 找到最久未访问的IP（最旧时间戳）
        oldest_ip = None
        oldest_time = float("inf")

        # 全量遍历找真正的oldest（超限时性能可接受）
        for ip, times in self.request_times.items():
            if not times:
                # 空deque，记录待删除
                empty_ips.append(ip)
            else:
                # 找到最旧的时间戳
                if times[0] < oldest_time:
                    oldest_time = times[0]
                    oldest_ip = ip

        # 先删除空deque的IP
        for ip in empty_ips:
            del self.request_times[ip]

        # 如果没有空deque可删除，且仍需要清理，删除最旧的一个IP
        if not empty_ips and oldest_ip:
            del self.request_times[oldest_ip]

    def _is_trusted_proxy(self, ip: str) -> bool:
        """
        检查IP是否在信任的代理列表中

        Args:
            ip: IP地址字符串

        Returns:
            如果是信任的代理则返回 True
        """
        if not TRUSTED_PROXIES or ip == "unknown":
            return False

        # 检查代理列表中的每个条目
        for trusted_entry in TRUSTED_PROXIES:
            # 通配符模式（字符串，正则表达式）
            if isinstance(trusted_entry, str):
                try:
                    if re.match(trusted_entry, ip):
                        return True
                except re.error:
                    continue
            # CIDR格式（网络对象）
            elif isinstance(trusted_entry, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                try:
                    client_ip_obj = ipaddress.ip_address(ip)
                    if client_ip_obj in trusted_entry:
                        return True
                except (ValueError, AttributeError):
                    continue
            # 精确IP（地址对象）
            elif isinstance(trusted_entry, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
                try:
                    client_ip_obj = ipaddress.ip_address(ip)
                    if client_ip_obj == trusted_entry:
                        return True
                except (ValueError, AttributeError):
                    continue

        return False

    def _get_client_ip(self, request: Request) -> str:
        """
        获取客户端真实IP地址（带基本验证和代理信任检查）

        Args:
            request: 请求对象

        Returns:
            客户端IP地址
        """
        # 获取直接连接的客户端IP（用于验证代理）
        direct_client_ip = None
        if request.client:
            direct_client_ip = request.client.host

        # 检查是否信任X-Forwarded-For头
        # TRUST_XFF 只表示"启用代理解析能力"，但仍要求直连 IP 在 TRUSTED_PROXIES 中
        use_xff = False
        if TRUST_XFF and TRUSTED_PROXIES and direct_client_ip:
            # 只有在启用 TRUST_XFF 且直连 IP 在信任列表中时，才信任 XFF
            use_xff = self._is_trusted_proxy(direct_client_ip)

        # 如果信任代理，优先从 X-Forwarded-For 获取
        if use_xff:
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                # X-Forwarded-For 可能包含多个IP，取第一个
                ip = forwarded_for.split(",")[0].strip()
                # 基本验证IP格式
                if self._validate_ip(ip):
                    return ip

        # 从 X-Real-IP 获取（如果信任代理）
        if use_xff:
            real_ip = request.headers.get("X-Real-IP")
            if real_ip:
                ip = real_ip.strip()
                if self._validate_ip(ip):
                    return ip

        # 使用直接连接的客户端IP
        if direct_client_ip and self._validate_ip(direct_client_ip):
            return direct_client_ip

        return "unknown"

    def _validate_ip(self, ip: str) -> bool:
        """
        验证IP地址格式

        Args:
            ip: IP地址字符串

        Returns:
            如果格式有效则返回 True
        """
        try:
            ipaddress.ip_address(ip)
            return True
        except (ValueError, AttributeError):
            return False

    def _is_ip_allowed(self, ip: str) -> bool:
        """
        检查IP是否在白名单中（支持精确IP、CIDR格式和通配符）

        Args:
            ip: 客户端IP地址

        Returns:
            如果IP在白名单中则返回 True
        """
        if not ALLOWED_IPS or ip == "unknown":
            return False

        # 检查白名单中的每个条目
        for allowed_entry in ALLOWED_IPS:
            # 通配符模式（字符串，正则表达式）
            if isinstance(allowed_entry, str):
                try:
                    if re.match(allowed_entry, ip):
                        return True
                except re.error:
                    # 正则表达式错误，跳过
                    continue
            # CIDR格式（网络对象）
            elif isinstance(allowed_entry, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
                try:
                    client_ip_obj = ipaddress.ip_address(ip)
                    if client_ip_obj in allowed_entry:
                        return True
                except (ValueError, AttributeError):
                    # IP格式无效，跳过
                    continue
            # 精确IP（地址对象）
            elif isinstance(allowed_entry, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
                try:
                    client_ip_obj = ipaddress.ip_address(ip)
                    if client_ip_obj == allowed_entry:
                        return True
                except (ValueError, AttributeError):
                    # IP格式无效，跳过
                    continue

        return False

    async def dispatch(self, request: Request, call_next):
        """
        处理请求

        Args:
            request: 请求对象
            call_next: 下一个中间件或路由处理函数

        Returns:
            响应对象
        """
        # 如果未启用，直接通过
        if not self.enabled:
            return await call_next(request)

        # 允许访问 robots.txt（由专门的路由处理）
        if request.url.path == "/robots.txt":
            return await call_next(request)

        # 允许访问静态资源（CSS、JS、图片等）
        # 注意：.json 已移除，避免 API 路径绕过防护
        # 静态资源只在特定前缀下放行（/static/、/assets/、/dist/）
        static_extensions = {
            ".css",
            ".js",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
        }
        static_prefixes = {"/static/", "/assets/", "/dist/"}

        # 检查是否是静态资源路径（特定前缀下的静态文件）
        path = request.url.path
        is_static_path = any(path.startswith(prefix) for prefix in static_prefixes) and any(
            path.endswith(ext) for ext in static_extensions
        )

        # 也允许根路径下的静态文件（如 /favicon.ico）
        is_root_static = path.count("/") == 1 and any(path.endswith(ext) for ext in static_extensions)

        if is_static_path or is_root_static:
            return await call_next(request)

        # 获取客户端IP（只获取一次，避免重复调用）
        client_ip = self._get_client_ip(request)

        # 检查IP白名单（优先检查，白名单IP直接通过）
        if self._is_ip_allowed(client_ip):
            return await call_next(request)

        # 获取 User-Agent
        user_agent = request.headers.get("User-Agent")

        # 检测资产测绘工具（优先检测，因为更危险）
        if self.check_asset_scanner:
            is_scanner, scanner_name = self._detect_asset_scanner(request)
            if is_scanner:
                logger.warning(
                    f"🚫 检测到资产测绘工具请求 - IP: {client_ip}, 工具: {scanner_name}, "
                    f"User-Agent: {user_agent}, Path: {request.url.path}"
                )
                # 根据配置决定是否阻止
                if self.block_on_detect:
                    return PlainTextResponse(
                        "Access Denied: Asset scanning tools are not allowed",
                        status_code=403,
                    )

        # 检测爬虫 User-Agent
        if self.check_user_agent and self._is_crawler_user_agent(user_agent):
            logger.warning(f"🚫 检测到爬虫请求 - IP: {client_ip}, User-Agent: {user_agent}, Path: {request.url.path}")
            # 根据配置决定是否阻止
            if self.block_on_detect:
                return PlainTextResponse(
                    "Access Denied: Crawlers are not allowed",
                    status_code=403,
                )

        # 检查请求频率限制
        if self.check_rate_limit and self._check_rate_limit(client_ip):
            logger.warning(f"🚫 请求频率过高 - IP: {client_ip}, User-Agent: {user_agent}, Path: {request.url.path}")
            return PlainTextResponse(
                "Too Many Requests: Rate limit exceeded",
                status_code=429,
            )

        # 正常请求，继续处理
        return await call_next(request)


def create_robots_txt_response() -> PlainTextResponse:
    """
    创建 robots.txt 响应

    Returns:
        robots.txt 响应对象
    """
    robots_content = """User-agent: *
Disallow: /

# 禁止所有爬虫访问
"""
    return PlainTextResponse(
        content=robots_content,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"},  # 缓存24小时
    )
