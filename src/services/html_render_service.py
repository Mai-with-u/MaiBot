"""HTML 浏览器渲染服务。

负责在 Host 侧复用已有浏览器，并将 HTML 内容渲染为 PNG 图片。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple, cast
from urllib.parse import urlparse

import asyncio
import base64
import contextlib
import functools
import json
import os
import shutil
import sys
import time

from src.common.logger import PROJECT_ROOT, get_logger
from src.config.config import config_manager
from src.config.official_configs import PluginRuntimeRenderConfig

logger = get_logger("services.html_render_service")

_NETWORK_ALLOW_SCHEMES = frozenset({"about", "blob", "data", "file"})
_WINDOWS_BROWSER_PATHS = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
)
_MACOS_BROWSER_PATHS = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
)
_UNIX_BROWSER_NAMES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
    "msedge",
)
_PLAYWRIGHT_MANAGED_BROWSER_PREFIXES = ("chromium-", "chrome-", "chrome-headless-shell-")


@dataclass(slots=True)
class HtmlRenderRequest:
    """描述一次 HTML 转 PNG 请求。"""

    html: str
    selector: str = "body"
    viewport_width: int = 900
    viewport_height: int = 500
    device_scale_factor: float = 2.0
    full_page: bool = False
    omit_background: bool = False
    wait_until: str = "load"
    wait_for_selector: str = ""
    wait_for_timeout_ms: int = 0
    timeout_ms: int = 10000
    allow_network: bool = False


@dataclass(slots=True)
class HtmlRenderResult:
    """描述一次 HTML 转 PNG 的输出结果。"""

    image_base64: str
    mime_type: str
    width: int
    height: int
    render_ms: int

    def to_payload(self) -> Dict[str, Any]:
        """将结果序列化为能力层返回结构。

        Returns:
            Dict[str, Any]: 可直接返回给插件运行时的结构化数据。
        """

        return {
            "image_base64": self.image_base64,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "render_ms": self.render_ms,
        }


@dataclass(slots=True)
class ManagedBrowserRecord:
    """记录 Playwright 托管浏览器的本地状态。"""

    browser_name: str
    browsers_path: str
    install_source: Literal["auto_download", "existing_cache"]
    playwright_version: str
    recorded_at: str
    last_verified_at: str

    def to_dict(self) -> Dict[str, str]:
        """将浏览器记录转换为可持久化字典。

        Returns:
            Dict[str, str]: 可写入 JSON 文件的字典结构。
        """

        return {
            "browser_name": self.browser_name,
            "browsers_path": self.browsers_path,
            "install_source": self.install_source,
            "playwright_version": self.playwright_version,
            "recorded_at": self.recorded_at,
            "last_verified_at": self.last_verified_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> Optional["ManagedBrowserRecord"]:
        """从字典中恢复浏览器状态记录。

        Args:
            payload: 原始字典数据。

        Returns:
            Optional[ManagedBrowserRecord]: 解析成功时返回记录对象，否则返回 ``None``。
        """

        browser_name = str(payload.get("browser_name", "") or "").strip()
        browsers_path = str(payload.get("browsers_path", "") or "").strip()
        install_source = str(payload.get("install_source", "") or "").strip()
        playwright_version = str(payload.get("playwright_version", "") or "").strip()
        recorded_at = str(payload.get("recorded_at", "") or "").strip()
        last_verified_at = str(payload.get("last_verified_at", "") or "").strip()
        if not all([browser_name, browsers_path, install_source, playwright_version, recorded_at, last_verified_at]):
            return None
        if install_source not in {"auto_download", "existing_cache"}:
            return None
        validated_install_source = cast(Literal["auto_download", "existing_cache"], install_source)
        return cls(
            browser_name=browser_name,
            browsers_path=browsers_path,
            install_source=validated_install_source,
            playwright_version=playwright_version,
            recorded_at=recorded_at,
            last_verified_at=last_verified_at,
        )


class HTMLRenderService:
    """HTML 浏览器渲染服务。"""

    def __init__(self) -> None:
        """初始化渲染服务。"""

        self._browser: Any = None
        self._browser_lock: asyncio.Lock = asyncio.Lock()
        self._connected_via_cdp: bool = False
        self._intentional_browser_close: bool = False
        self._playwright: Any = None
        self._render_count: int = 0
        self._render_semaphore: Optional[asyncio.Semaphore] = None
        self._render_semaphore_limit: int = 0

    def _get_render_config(self) -> PluginRuntimeRenderConfig:
        """读取当前插件运行时的浏览器渲染配置。

        Returns:
            PluginRuntimeRenderConfig: 当前生效的浏览器渲染配置。
        """

        return config_manager.get_global_config().plugin_runtime.render

    def _get_render_semaphore(self) -> asyncio.Semaphore:
        """根据当前配置返回渲染并发信号量。

        Returns:
            asyncio.Semaphore: 控制并发的信号量对象。
        """

        config = self._get_render_config()
        limit = max(1, int(config.concurrency_limit))
        if self._render_semaphore is None or self._render_semaphore_limit != limit:
            self._render_semaphore = asyncio.Semaphore(limit)
            self._render_semaphore_limit = limit
        return self._render_semaphore

    async def create_browser_context(
        self,
        *,
        accept_downloads: bool = False,
        locale: str = "zh-CN",
        service_workers: Literal["allow", "block"] = "allow",
        viewport_height: int = 720,
        viewport_width: int = 1280,
    ) -> Any:
        """使用现有浏览器启动策略创建一个独立上下文。

        调用方负责校验自身功能开关、配置页面网络策略并关闭返回的上下文。
        该入口复用本机浏览器探测、托管 Chromium 自动安装和启动配置，避免各业务模块
        分别维护 Playwright 生命周期。

        Args:
            accept_downloads: 是否允许页面下载文件。
            locale: 浏览器上下文语言。
            service_workers: 是否允许页面注册和运行 Service Worker。
            viewport_height: 视口高度。
            viewport_width: 视口宽度。

        Returns:
            Any: Playwright BrowserContext 对象。
        """

        config = self._get_render_config()
        browser = await self._ensure_browser(config)
        return await browser.new_context(
            accept_downloads=accept_downloads,
            locale=locale,
            service_workers=service_workers,
            viewport={"width": viewport_width, "height": viewport_height},
        )

    async def render_html_to_png(self, request: HtmlRenderRequest) -> HtmlRenderResult:
        """将 HTML 内容渲染为 PNG 图片。

        Args:
            request: 本次渲染请求。

        Returns:
            HtmlRenderResult: 渲染结果。

        Raises:
            RuntimeError: 浏览器能力被禁用、Playwright 不可用或浏览器启动失败时抛出。
            ValueError: 请求参数非法时抛出。
        """

        config = self._get_render_config()
        if not config.enabled:
            raise RuntimeError("插件运行时浏览器渲染能力已禁用")

        normalized_request = self._normalize_request(request, config)
        semaphore = self._get_render_semaphore()
        async with semaphore:
            start_time = time.perf_counter()
            browser = await self._ensure_browser(config)
            context: Any = None
            try:
                context = await browser.new_context(
                    device_scale_factor=normalized_request.device_scale_factor,
                    locale="zh-CN",
                    viewport={
                        "width": normalized_request.viewport_width,
                        "height": normalized_request.viewport_height,
                    },
                )
                page = await context.new_page()
                await self._configure_page(page, normalized_request)
                image_bytes = await self._capture_image(page, normalized_request)
                width, height = self._measure_image_size(image_bytes)
                self._render_count += 1
                await self._maybe_restart_browser(config)
                return HtmlRenderResult(
                    image_base64=base64.b64encode(image_bytes).decode("utf-8"),
                    mime_type="image/png",
                    width=width,
                    height=height,
                    render_ms=int((time.perf_counter() - start_time) * 1000),
                )
            except Exception:
                await self.reset_browser(restart_playwright=False)
                raise
            finally:
                if context is not None:
                    with contextlib.suppress(Exception):
                        await context.close()

    async def reset_browser(self, restart_playwright: bool = False) -> None:
        """关闭当前缓存的浏览器实例。

        Args:
            restart_playwright: 是否同时关闭 Playwright 运行时。
        """

        async with self._browser_lock:
            await self._close_browser_unlocked(restart_playwright=restart_playwright)

    async def _close_browser_unlocked(self, restart_playwright: bool = False) -> None:
        """在已持有锁的情况下关闭浏览器与 Playwright。

        Args:
            restart_playwright: 是否同时关闭 Playwright 运行时。
        """

        if self._browser is not None:
            self._intentional_browser_close = True
            try:
                with contextlib.suppress(Exception):
                    await self._browser.close()
            finally:
                self._intentional_browser_close = False
        self._browser = None
        self._connected_via_cdp = False
        if restart_playwright and self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    async def _ensure_browser(self, config: PluginRuntimeRenderConfig) -> Any:
        """获取可复用的浏览器实例。

        Args:
            config: 当前浏览器渲染配置。

        Returns:
            Any: Playwright Browser 对象。

        Raises:
            RuntimeError: 当无法连接或启动浏览器时抛出。
        """

        async with self._browser_lock:
            if self._is_browser_connected(self._browser):
                logger.debug("HTML 渲染服务复用进程内缓存浏览器实例")
                return self._browser

            await self._close_browser_unlocked(restart_playwright=False)
            self._prepare_playwright_environment(config)
            playwright = await self._ensure_playwright()
            browser = await self._connect_to_existing_browser(playwright, config)
            if browser is None:
                browser = await self._launch_browser(playwright, config)
                self._connected_via_cdp = False
            else:
                self._connected_via_cdp = True

            self._browser = browser
            self._bind_browser_events(browser)
            return browser

    async def _ensure_playwright(self) -> Any:
        """懒加载并启动 Playwright 运行时。

        Returns:
            Any: 已启动的 Playwright 对象。

        Raises:
            RuntimeError: 当前环境未安装 Playwright 时抛出。
        """

        if self._playwright is not None:
            return self._playwright

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "当前环境未安装 Python Playwright，请先在宿主环境安装 `playwright` 依赖。"
            ) from exc

        self._playwright = await async_playwright().start()
        return self._playwright

    @staticmethod
    def _is_browser_connected(browser: Any) -> bool:
        """判断浏览器对象当前是否仍然可用。

        Args:
            browser: 待检查的浏览器对象。

        Returns:
            bool: 若浏览器仍连接，则返回 ``True``。
        """

        if browser is None:
            return False
        try:
            return bool(browser.is_connected())
        except Exception:
            return False

    async def _connect_to_existing_browser(self, playwright: Any, config: PluginRuntimeRenderConfig) -> Any:
        """优先连接外部已有的 Chromium 浏览器。

        Args:
            playwright: 已启动的 Playwright 对象。
            config: 当前浏览器渲染配置。

        Returns:
            Any: 连接成功时返回 Browser；否则返回 ``None``。
        """

        if not config.browser_ws_endpoint.strip():
            return None

        try:
            timeout_ms = int(config.startup_timeout_sec * 1000)
            logger.info(
                "HTML 渲染服务准备连接现有浏览器: "
                f"endpoint={config.browser_ws_endpoint.strip()}, timeout_ms={timeout_ms}"
            )
            browser = await playwright.chromium.connect_over_cdp(
                config.browser_ws_endpoint.strip(),
                timeout=timeout_ms,
            )
            logger.info("HTML 渲染服务已连接到现有浏览器")
            return browser
        except Exception as exc:
            logger.warning(f"连接现有浏览器失败，将回退为本地启动: {exc}")
            return None

    async def _launch_browser(self, playwright: Any, config: PluginRuntimeRenderConfig) -> Any:
        """启动本地 Chromium 浏览器。

        Args:
            playwright: 已启动的 Playwright 对象。
            config: 当前浏览器渲染配置。

        Returns:
            Any: 新启动的 Browser 对象。

        Raises:
            RuntimeError: 浏览器启动失败时抛出。
        """

        launch_options = self._build_launch_options(config)
        logger.info(
            "HTML 渲染服务准备启动浏览器: "
            f"source={'system' if 'executable_path' in launch_options else 'managed'}, "
            f"headless={bool(launch_options.get('headless'))}, "
            f"timeout_ms={int(launch_options.get('timeout', 0))}"
        )
        try:
            browser = await playwright.chromium.launch(**launch_options)
            if "executable_path" in launch_options:
                logger.info(f"HTML 渲染服务已启动本机浏览器: executable_path={launch_options['executable_path']}")
            else:
                self._update_managed_browser_record(config, install_source="existing_cache")
                logger.info("HTML 渲染服务已启动 Playwright 托管浏览器")
            return browser
        except Exception as exc:
            if self._should_auto_download_browser(exc, launch_options, config):
                logger.warning(f"HTML 渲染服务未找到可用浏览器，将尝试自动下载 Chromium: {exc}")
                await self._install_chromium_browser(config)
                retry_browser = await playwright.chromium.launch(**launch_options)
                self._update_managed_browser_record(config, install_source="auto_download")
                logger.info("HTML 渲染服务已自动下载并启动 Chromium")
                return retry_browser
            raise RuntimeError(f"启动本地浏览器失败: {exc}") from exc

    def _bind_browser_events(self, browser: Any) -> None:
        """为浏览器绑定断线回调。

        Args:
            browser: 需要绑定事件的浏览器对象。
        """

        try:
            browser.on("disconnected", self._handle_browser_disconnected)
        except Exception:
            return

    def _handle_browser_disconnected(self, *_args: Any) -> None:
        """处理浏览器断线事件。

        Args:
            *_args: 浏览器断线事件透传的参数。
        """

        self._browser = None
        self._connected_via_cdp = False
        if self._intentional_browser_close:
            logger.debug("HTML 渲染浏览器已主动释放")
            return
        logger.warning("HTML 渲染浏览器意外断开，将在下次请求时重新建立连接")

    def _build_launch_options(self, config: PluginRuntimeRenderConfig) -> Dict[str, Any]:
        """构造本地浏览器启动参数。

        Args:
            config: 当前浏览器渲染配置。

        Returns:
            Dict[str, Any]: 可直接传给 Playwright 的启动参数。
        """

        launch_options: Dict[str, Any] = {
            "args": list(config.launch_args),
            "headless": bool(config.headless),
            "timeout": int(config.startup_timeout_sec * 1000),
        }
        executable_path = self._resolve_executable_path(config)
        if executable_path:
            launch_options["executable_path"] = executable_path
        return launch_options

    @staticmethod
    def _should_auto_download_browser(
        exc: Exception,
        launch_options: Dict[str, Any],
        config: PluginRuntimeRenderConfig,
    ) -> bool:
        """判断当前启动错误是否适合自动下载 Chromium 后重试。

        Args:
            exc: 浏览器启动异常。
            launch_options: 本次启动参数。
            config: 当前浏览器渲染配置。

        Returns:
            bool: 若应自动下载后重试，则返回 ``True``。
        """

        if "executable_path" in launch_options:
            logger.debug("当前启动参数已指定本机浏览器路径，不进入自动下载分支")
            return False
        if not config.auto_download_chromium:
            logger.warning("HTML 渲染服务未检测到可用浏览器，且已禁用自动下载 Chromium")
            return False
        error_text = str(exc).lower()
        should_download = "executable doesn't exist" in error_text or "browser executable" in error_text
        if not should_download:
            logger.warning(f"浏览器启动失败，但错误不属于可自动下载恢复的类型: {exc}")
        return should_download

    def _resolve_executable_path(self, config: PluginRuntimeRenderConfig) -> str:
        """解析实际应使用的浏览器可执行文件路径。

        Args:
            config: 当前浏览器渲染配置。

        Returns:
            str: 命中的浏览器可执行文件路径；未命中时返回空字符串。
        """

        configured_path = config.executable_path.strip()
        if configured_path:
            path = Path(configured_path).expanduser()
            if path.exists():
                logger.info(f"HTML 渲染服务使用配置指定的浏览器路径: {path}")
                return str(path)
            logger.warning(f"配置的浏览器路径不存在，将尝试自动探测: {configured_path}")

        detected_path = self._detect_local_browser_executable()
        if detected_path:
            logger.info(f"HTML 渲染服务自动探测到本机浏览器: {detected_path}")
        else:
            logger.info("HTML 渲染服务未探测到本机浏览器，将尝试使用 Playwright 托管浏览器")
        return detected_path

    def _prepare_playwright_environment(self, config: PluginRuntimeRenderConfig) -> Path:
        """准备 Playwright 运行所需的共享浏览器目录环境变量。

        Args:
            config: 当前浏览器渲染配置。

        Returns:
            Path: Playwright 浏览器缓存目录。
        """

        browsers_path = self._get_managed_browsers_path(config)
        browsers_path.mkdir(parents=True, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
        logger.debug(f"HTML 渲染服务使用 Playwright 浏览器目录: {browsers_path}")
        return browsers_path

    def _get_managed_browsers_path(self, config: PluginRuntimeRenderConfig) -> Path:
        """获取 Playwright 托管浏览器目录。

        Args:
            config: 当前浏览器渲染配置。

        Returns:
            Path: 托管浏览器目录的绝对路径。
        """

        configured_path = config.browser_install_root.strip()
        if not configured_path:
            return (PROJECT_ROOT / "data" / "playwright-browsers").resolve()
        candidate_path = Path(configured_path).expanduser()
        if candidate_path.is_absolute():
            return candidate_path.resolve()
        return (PROJECT_ROOT / candidate_path).resolve()

    def _get_browser_state_path(self) -> Path:
        """获取托管浏览器状态文件路径。

        Returns:
            Path: 浏览器状态文件路径。
        """

        return (PROJECT_ROOT / "data" / "plugin_runtime" / "html_render_browser_state.json").resolve()

    def _load_managed_browser_record(self) -> Optional[ManagedBrowserRecord]:
        """读取最近一次成功使用的托管浏览器记录。

        Returns:
            Optional[ManagedBrowserRecord]: 解析成功时返回记录对象，否则返回 ``None``。
        """

        state_path = self._get_browser_state_path()
        if not state_path.exists():
            return None

        try:
            raw_payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning(f"HTML 渲染浏览器状态文件读取失败，将忽略并继续: {state_path}")
            return None
        if not isinstance(raw_payload, dict):
            logger.warning(f"HTML 渲染浏览器状态文件格式无效，将忽略并继续: {state_path}")
            return None
        browser_record = ManagedBrowserRecord.from_dict(raw_payload)
        if browser_record is not None:
            logger.debug(
                "HTML 渲染服务已加载浏览器状态记录: "
                f"source={browser_record.install_source}, path={browser_record.browsers_path}, "
                f"verified_at={browser_record.last_verified_at}"
            )
        return browser_record

    def _save_managed_browser_record(self, record: ManagedBrowserRecord) -> None:
        """保存托管浏览器记录。

        Args:
            record: 待保存的浏览器记录。
        """

        state_path = self._get_browser_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "HTML 渲染服务已写入浏览器状态记录: "
            f"path={state_path}, source={record.install_source}, browsers_path={record.browsers_path}"
        )

    def _update_managed_browser_record(
        self,
        config: PluginRuntimeRenderConfig,
        install_source: Literal["auto_download", "existing_cache"],
    ) -> None:
        """更新托管 Chromium 的使用记录。

        Args:
            config: 当前浏览器渲染配置。
            install_source: 本次记录的浏览器来源。
        """

        browsers_path = self._get_managed_browsers_path(config)
        if not self._has_managed_browser_artifact(browsers_path):
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        existing_record = self._load_managed_browser_record()
        recorded_at = now_iso
        if existing_record is not None and existing_record.browsers_path == str(browsers_path):
            recorded_at = existing_record.recorded_at

        self._save_managed_browser_record(
            ManagedBrowserRecord(
                browser_name="chromium",
                browsers_path=str(browsers_path),
                install_source=install_source,
                playwright_version=self._get_playwright_version(),
                recorded_at=recorded_at,
                last_verified_at=now_iso,
            )
        )
        logger.info(
            "HTML 渲染服务已更新托管浏览器记录: "
            f"source={install_source}, browsers_path={browsers_path}, last_verified_at={now_iso}"
        )

    async def _install_chromium_browser(self, config: PluginRuntimeRenderConfig) -> None:
        """自动下载 Playwright Chromium 浏览器。

        Args:
            config: 当前浏览器渲染配置。

        Raises:
            RuntimeError: 下载失败时抛出。
        """

        browsers_path = self._prepare_playwright_environment(config)
        logger.warning(
            "HTML 渲染服务开始自动下载 Chromium: "
            f"target_dir={browsers_path}, timeout_sec={config.download_connection_timeout_sec}"
        )
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
        env["PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT"] = str(int(config.download_connection_timeout_sec * 1000))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="ignore").strip()
            stdout_text = stdout_bytes.decode("utf-8", errors="ignore").strip()
            error_detail = stderr_text or stdout_text or f"退出码 {process.returncode}"
            raise RuntimeError(f"自动下载 Chromium 失败: {error_detail}")

        if not self._has_managed_browser_artifact(browsers_path):
            raise RuntimeError("Chromium 下载完成后未检测到可用浏览器文件")
        logger.info(f"HTML 渲染服务自动下载 Chromium 完成: target_dir={browsers_path}")

    @staticmethod
    def _get_playwright_version() -> str:
        """读取当前环境中的 Playwright 版本号。

        Returns:
            str: Playwright 版本字符串；读取失败时返回 ``unknown``。
        """

        try:
            return metadata.version("playwright")
        except metadata.PackageNotFoundError:
            return "unknown"

    @staticmethod
    def _has_managed_browser_artifact(browsers_path: Path) -> bool:
        """检查共享目录中是否存在可用的 Playwright 托管浏览器。

        Args:
            browsers_path: Playwright 浏览器目录。

        Returns:
            bool: 若检测到 Chromium/Chrome 相关浏览器文件夹，则返回 ``True``。
        """

        if not browsers_path.exists():
            return False
        for child_path in browsers_path.iterdir():
            if not child_path.is_dir():
                continue
            if child_path.name.startswith(_PLAYWRIGHT_MANAGED_BROWSER_PREFIXES):
                return True
        return False

    def _detect_local_browser_executable(self) -> str:
        """自动探测当前宿主系统中的可复用浏览器路径。

        Returns:
            str: 命中的浏览器可执行文件路径；未命中时返回空字符串。
        """

        for browser_name in _UNIX_BROWSER_NAMES:
            resolved_path = shutil.which(browser_name)
            if resolved_path:
                return resolved_path

        for candidate_path in self._get_candidate_executable_paths():
            if candidate_path.exists():
                return str(candidate_path)
        return ""

    @staticmethod
    def _get_candidate_executable_paths() -> Tuple[Path, ...]:
        """返回当前平台常见浏览器路径候选集合。

        Returns:
            Tuple[Path, ...]: 可能存在浏览器可执行文件的路径列表。
        """

        if sys.platform.startswith("win"):
            return _WINDOWS_BROWSER_PATHS
        if sys.platform == "darwin":
            return _MACOS_BROWSER_PATHS
        return ()

    async def _configure_page(self, page: Any, request: HtmlRenderRequest) -> None:
        """为页面设置超时、网络策略并写入 HTML。

        Args:
            page: Playwright 页面对象。
            request: 当前渲染请求。
        """

        page.set_default_timeout(request.timeout_ms)
        await page.route(
            "**/*",
            functools.partial(self._handle_network_route, allow_network=request.allow_network),
        )
        await page.set_content(
            request.html,
            timeout=request.timeout_ms,
            wait_until=request.wait_until,
        )
        if request.wait_for_selector:
            await page.locator(request.wait_for_selector).first.wait_for(
                state="attached",
                timeout=request.timeout_ms,
            )
        if request.wait_for_timeout_ms > 0:
            await page.wait_for_timeout(request.wait_for_timeout_ms)

    async def _handle_network_route(self, route: Any, allow_network: bool) -> None:
        """处理页面资源请求的网络准入策略。

        Args:
            route: Playwright 路由对象。
            allow_network: 是否允许页面访问外部网络资源。
        """

        request_url = str(route.request.url)
        if allow_network or self._is_network_request_allowed(request_url):
            await route.continue_()
            return
        await route.abort()

    @staticmethod
    def _is_network_request_allowed(request_url: str) -> bool:
        """判断某个资源 URL 是否属于本地安全资源。

        Args:
            request_url: 待判断的资源地址。

        Returns:
            bool: 若请求可在无网络模式下放行，则返回 ``True``。
        """

        if not request_url:
            return False
        parsed_url = urlparse(request_url)
        return parsed_url.scheme in _NETWORK_ALLOW_SCHEMES

    async def _capture_image(self, page: Any, request: HtmlRenderRequest) -> bytes:
        """从页面或目标元素中截取 PNG 图片。

        Args:
            page: Playwright 页面对象。
            request: 当前渲染请求。

        Returns:
            bytes: PNG 二进制内容。

        Raises:
            RuntimeError: 目标元素不存在或截图结果为空时抛出。
        """

        if request.full_page and request.selector == "body":
            image_bytes = await page.screenshot(
                full_page=True,
                omit_background=request.omit_background,
                timeout=request.timeout_ms,
                type="png",
            )
        else:
            locator = page.locator(request.selector).first
            await locator.wait_for(state="visible", timeout=request.timeout_ms)
            image_bytes = await locator.screenshot(
                omit_background=request.omit_background,
                timeout=request.timeout_ms,
                type="png",
            )

        if not image_bytes:
            raise RuntimeError("浏览器截图结果为空")
        return image_bytes

    @staticmethod
    def _measure_image_size(image_bytes: bytes) -> Tuple[int, int]:
        """读取 PNG 图片的真实像素尺寸。

        Args:
            image_bytes: PNG 图片二进制内容。

        Returns:
            Tuple[int, int]: 图片宽高像素值。
        """

        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as image:
            return int(image.width), int(image.height)

    async def _maybe_restart_browser(self, config: PluginRuntimeRenderConfig) -> None:
        """按策略决定是否重建本地浏览器实例。

        Args:
            config: 当前浏览器渲染配置。
        """

        restart_after = int(config.restart_after_render_count)
        if restart_after <= 0 or self._connected_via_cdp:
            return
        if self._render_count % restart_after != 0:
            return
        await self.reset_browser(restart_playwright=False)
        logger.info("HTML 渲染服务已按累计次数策略重建本地浏览器")

    @staticmethod
    def _normalize_request(
        request: HtmlRenderRequest,
        config: PluginRuntimeRenderConfig,
    ) -> HtmlRenderRequest:
        """规范化并补齐 HTML 渲染请求。

        Args:
            request: 原始渲染请求。
            config: 当前浏览器渲染配置。

        Returns:
            HtmlRenderRequest: 规范化后的请求对象。

        Raises:
            ValueError: 请求缺少必要字段或取值非法时抛出。
        """

        html = request.html.strip()
        if not html:
            raise ValueError("缺少必要参数 html")

        selector = request.selector.strip() or "body"
        wait_until = HTMLRenderService._normalize_wait_until(request.wait_until)
        timeout_ms = request.timeout_ms
        if timeout_ms <= 0:
            timeout_ms = int(config.render_timeout_sec * 1000)

        return HtmlRenderRequest(
            html=html,
            selector=selector,
            viewport_width=max(1, int(request.viewport_width)),
            viewport_height=max(1, int(request.viewport_height)),
            device_scale_factor=max(1.0, float(request.device_scale_factor)),
            full_page=bool(request.full_page),
            omit_background=bool(request.omit_background),
            wait_until=wait_until,
            wait_for_selector=request.wait_for_selector.strip(),
            wait_for_timeout_ms=max(0, int(request.wait_for_timeout_ms)),
            timeout_ms=max(1, int(timeout_ms)),
            allow_network=bool(request.allow_network),
        )

    @staticmethod
    def _normalize_wait_until(wait_until: str) -> str:
        """规范化页面等待阶段参数。

        Args:
            wait_until: 原始等待阶段字符串。

        Returns:
            str: Playwright 支持的等待阶段值。
        """

        normalized_wait_until = wait_until.strip().lower()
        if normalized_wait_until in {"commit", "domcontentloaded", "load", "networkidle"}:
            return normalized_wait_until
        return "load"


_html_render_service: Optional[HTMLRenderService] = None


def get_html_render_service() -> HTMLRenderService:
    """获取 HTML 浏览器渲染服务单例。

    Returns:
        HTMLRenderService: 全局唯一的浏览器渲染服务实例。
    """

    global _html_render_service
    if _html_render_service is None:
        _html_render_service = HTMLRenderService()
    return _html_render_service
