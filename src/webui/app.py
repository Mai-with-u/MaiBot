"""FastAPI 应用工厂 - 创建和配置 WebUI 应用实例"""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired, run
import mimetypes
import shutil

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.common.i18n import t
from src.common.logger import get_logger

logger = get_logger("webui.app")

_DASHBOARD_BUILD_TIMEOUT_SECONDS = 300
_DASHBOARD_INSTALL_TIMEOUT_SECONDS = 600
_MANUAL_BUILD_COMMAND = "cd dashboard && npm install && npm run build"

_DASHBOARD_BUILD_COMMANDS = {
    "bun": ["bun", "run", "build"],
    "npm": ["npm", "run", "build"],
    "pnpm": ["pnpm", "build"],
    "yarn": ["yarn", "build"],
}
_DASHBOARD_INSTALL_COMMANDS = {
    "bun": ["bun", "install"],
    "npm": ["npm", "install", "--no-package-lock"],
    "pnpm": ["pnpm", "install"],
    "yarn": ["yarn", "install"],
}


@dataclass(frozen=True)
class DashboardAutoRecoveryResult:
    succeeded: bool
    manual_recovery_command: str | None = None


def _resolve_safe_static_file_path(static_path: Path, full_path: str) -> Path | None:
    static_root = static_path.resolve()

    try:
        candidate_path = (static_root / full_path).resolve()
        candidate_path.relative_to(static_root)
    except (OSError, RuntimeError, ValueError):
        logger.warning(t("startup.webui_path_traversal_detected", full_path=full_path))
        return None

    return candidate_path


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _get_dashboard_root() -> Path:
    return _get_project_root() / "dashboard"


def _format_dashboard_shell_commands(*commands: list[str]) -> str:
    formatted_commands = " && ".join(" ".join(command) for command in commands)
    return f"cd dashboard && {formatted_commands}"


def _validate_static_path(static_path: Path | None) -> tuple[str, dict[str, object]] | None:
    if static_path is None:
        return "startup.webui_static_dir_missing", {}

    if not static_path.exists():
        return "startup.webui_static_dir_missing_with_path", {"static_path": static_path}

    index_path = static_path / "index.html"
    if not index_path.exists():
        return "startup.webui_index_missing", {"index_path": index_path}

    return None


def _summarize_command_output(command_result: CompletedProcess[str] | TimeoutExpired) -> str:
    output_chunks: list[str] = []
    stdout = command_result.stdout
    stderr = command_result.stderr

    if isinstance(stdout, str) and stdout.strip():
        output_chunks.append(stdout.strip())
    if isinstance(stderr, str) and stderr.strip():
        output_chunks.append(stderr.strip())

    if not output_chunks:
        return ""

    combined_output = "\n".join(output_chunks)
    max_output_length = 2000
    if len(combined_output) <= max_output_length:
        return combined_output

    return combined_output[-max_output_length:]


def _get_preferred_dashboard_package_manager(dashboard_root: Path) -> str:
    if (dashboard_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (dashboard_root / "yarn.lock").exists():
        return "yarn"
    if (dashboard_root / "bun.lock").exists() or (dashboard_root / "bun.lockb").exists():
        return "bun"
    return "npm"


def _get_dashboard_build_command(dashboard_root: Path) -> list[str] | None:
    if not (dashboard_root / "package.json").exists():
        return None

    preferred_package_manager = _get_preferred_dashboard_package_manager(dashboard_root)
    package_managers = [
        preferred_package_manager,
        *[package_manager for package_manager in _DASHBOARD_BUILD_COMMANDS if package_manager != preferred_package_manager],
    ]

    for package_manager in package_managers:
        if shutil.which(package_manager):
            return _DASHBOARD_BUILD_COMMANDS[package_manager]

    return None


def _get_dashboard_manual_recovery_command(dashboard_root: Path, build_command: list[str] | None = None) -> str:
    package_manager = build_command[0] if build_command is not None else _get_preferred_dashboard_package_manager(dashboard_root)
    install_command = _DASHBOARD_INSTALL_COMMANDS.get(package_manager)
    selected_build_command = _DASHBOARD_BUILD_COMMANDS.get(package_manager)

    if install_command is None or selected_build_command is None:
        return _MANUAL_BUILD_COMMAND

    return _format_dashboard_shell_commands(install_command, selected_build_command)


def _should_auto_install_dashboard_dependencies(dashboard_root: Path) -> bool:
    return not (dashboard_root / "node_modules").exists()


def _try_build_dashboard() -> DashboardAutoRecoveryResult:
    dashboard_root = _get_dashboard_root()
    if not dashboard_root.exists():
        logger.warning(t("startup.webui_dashboard_source_missing", dashboard_root=dashboard_root))
        return DashboardAutoRecoveryResult(succeeded=False)

    build_command = _get_dashboard_build_command(dashboard_root)
    if build_command is None:
        logger.warning(t("startup.webui_auto_build_tool_missing"))
        manual_recovery_command = _get_dashboard_manual_recovery_command(dashboard_root)
        return DashboardAutoRecoveryResult(succeeded=False, manual_recovery_command=manual_recovery_command)

    manual_recovery_command = _get_dashboard_manual_recovery_command(dashboard_root, build_command)

    if _should_auto_install_dashboard_dependencies(dashboard_root):
        install_command = _DASHBOARD_INSTALL_COMMANDS[build_command[0]]
        logger.info(t("startup.webui_auto_install_started", command=" ".join(install_command)))

        try:
            install_result = run(
                install_command,
                capture_output=True,
                check=False,
                cwd=dashboard_root,
                text=True,
                timeout=_DASHBOARD_INSTALL_TIMEOUT_SECONDS,
            )
        except TimeoutExpired as exc:
            logger.warning(
                t("startup.webui_auto_install_timeout", timeout_seconds=_DASHBOARD_INSTALL_TIMEOUT_SECONDS),
            )
            install_output = _summarize_command_output(exc)
            if install_output:
                logger.warning(t("startup.webui_auto_install_failed_output", output=install_output))
            return DashboardAutoRecoveryResult(succeeded=False, manual_recovery_command=manual_recovery_command)
        except OSError as exc:
            logger.warning(t("startup.webui_auto_install_exec_failed", error=exc))
            return DashboardAutoRecoveryResult(succeeded=False, manual_recovery_command=manual_recovery_command)

        if install_result.returncode != 0:
            logger.warning(t("startup.webui_auto_install_failed", return_code=install_result.returncode))
            install_output = _summarize_command_output(install_result)
            if install_output:
                logger.warning(t("startup.webui_auto_install_failed_output", output=install_output))
            return DashboardAutoRecoveryResult(succeeded=False, manual_recovery_command=manual_recovery_command)

        logger.info(t("startup.webui_auto_install_succeeded"))

    logger.info(t("startup.webui_auto_build_started", command=" ".join(build_command)))

    try:
        build_result = run(
            build_command,
            capture_output=True,
            check=False,
            cwd=dashboard_root,
            text=True,
            timeout=_DASHBOARD_BUILD_TIMEOUT_SECONDS,
        )
    except TimeoutExpired as exc:
        logger.warning(
            t("startup.webui_auto_build_timeout", timeout_seconds=_DASHBOARD_BUILD_TIMEOUT_SECONDS),
        )
        build_output = _summarize_command_output(exc)
        if build_output:
            logger.warning(t("startup.webui_auto_build_failed_output", output=build_output))
        return DashboardAutoRecoveryResult(succeeded=False, manual_recovery_command=manual_recovery_command)
    except OSError as exc:
        logger.warning(t("startup.webui_auto_build_exec_failed", error=exc))
        return DashboardAutoRecoveryResult(succeeded=False, manual_recovery_command=manual_recovery_command)

    if build_result.returncode != 0:
        logger.warning(t("startup.webui_auto_build_failed", return_code=build_result.returncode))
        build_output = _summarize_command_output(build_result)
        if build_output:
            logger.warning(t("startup.webui_auto_build_failed_output", output=build_output))
        return DashboardAutoRecoveryResult(succeeded=False, manual_recovery_command=manual_recovery_command)

    logger.info(t("startup.webui_auto_build_succeeded"))
    return DashboardAutoRecoveryResult(succeeded=True, manual_recovery_command=manual_recovery_command)


def _ensure_static_path_ready() -> Path | None:
    static_path = _resolve_static_path()
    validation_error = _validate_static_path(static_path)
    if validation_error is None:
        return static_path

    logger.info(t("startup.webui_static_assets_try_auto_build"))

    auto_recovery_result = _try_build_dashboard()
    if auto_recovery_result.succeeded:
        static_path = _resolve_static_path()
        validation_error = _validate_static_path(static_path)
        if validation_error is None:
            return static_path
        logger.warning(t("startup.webui_auto_build_artifacts_invalid"))
        error_key, error_kwargs = validation_error
        logger.warning(t(error_key, **error_kwargs))
        logger.warning(
            t(
                "startup.webui_manual_build_hint",
                command=auto_recovery_result.manual_recovery_command or _MANUAL_BUILD_COMMAND,
            )
        )
        return None

    if auto_recovery_result.manual_recovery_command is not None:
        logger.warning(t("startup.webui_auto_recovery_failed"))
        logger.warning(t("startup.webui_manual_build_hint", command=auto_recovery_result.manual_recovery_command))
    return None


def create_app(
    host: str = "0.0.0.0",
    port: int = 8001,
    enable_static: bool = True,
) -> FastAPI:
    """
    创建 WebUI FastAPI 应用实例

    Args:
        host: 服务器主机地址
        port: 服务器端口
        enable_static: 是否启用静态文件服务
    """
    app = FastAPI(title="MaiBot WebUI")

    _setup_anti_crawler(app)
    _setup_cors(app, port)
    _register_api_routes(app)
    _setup_robots_txt(app)

    if enable_static:
        _setup_static_files(app)

    return app


def _setup_cors(app: FastAPI, port: int):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:7999",
            "http://127.0.0.1:7999",
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Accept",
            "Origin",
            "X-Requested-With",
        ],
        expose_headers=["Content-Length", "Content-Type"],
    )
    logger.debug(t("startup.webui_cors_configured"))


def _setup_anti_crawler(app: FastAPI):
    try:
        from src.webui.middleware import AntiCrawlerMiddleware
        from src.config.config import global_config

        anti_crawler_mode = global_config.webui.anti_crawler_mode
        app.add_middleware(AntiCrawlerMiddleware, mode=anti_crawler_mode)

        mode_descriptions = {
            "false": t("startup.webui_anti_crawler_mode_disabled"),
            "strict": t("startup.webui_anti_crawler_mode_strict"),
            "loose": t("startup.webui_anti_crawler_mode_loose"),
            "basic": t("startup.webui_anti_crawler_mode_basic"),
        }
        mode_desc = mode_descriptions.get(anti_crawler_mode, t("startup.webui_anti_crawler_mode_basic"))
        logger.info(t("startup.webui_anti_crawler_configured", mode_desc=mode_desc))
    except Exception as e:
        logger.error(t("startup.webui_anti_crawler_config_failed", error=e), exc_info=True)


def _setup_robots_txt(app: FastAPI):
    try:
        from src.webui.middleware import create_robots_txt_response

        @app.get("/robots.txt", include_in_schema=False)
        async def robots_txt():
            return create_robots_txt_response()

        logger.debug(t("startup.webui_robots_route_registered"))
    except Exception as e:
        logger.error(t("startup.webui_robots_route_register_failed", error=e), exc_info=True)


def _register_api_routes(app: FastAPI):
    try:
        from src.webui.routers import get_all_routers

        for router in get_all_routers():
            app.include_router(router)

        logger.info(t("startup.webui_api_routes_registered"))
    except Exception as e:
        logger.error(t("startup.webui_api_routes_register_failed", error=e), exc_info=True)


def _setup_static_files(app: FastAPI):
    mimetypes.init()
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("application/json", ".json")

    static_path = _ensure_static_path_ready()
    if static_path is None:
        return

    if not static_path.exists():
        logger.warning(t("startup.webui_static_dir_missing_with_path", static_path=static_path))
        logger.warning(t("startup.webui_manual_build_hint", command=_MANUAL_BUILD_COMMAND))
        return

    if not (static_path / "index.html").exists():
        logger.warning(t("startup.webui_index_missing", index_path=static_path / "index.html"))
        logger.warning(t("startup.webui_manual_build_hint", command=_MANUAL_BUILD_COMMAND))
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if not full_path or full_path == "/":
            response = FileResponse(static_path / "index.html", media_type="text/html")
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        file_path = _resolve_safe_static_file_path(static_path, full_path)
        if file_path is None:
            raise HTTPException(status_code=404, detail=t("core.not_found"))

        if file_path.exists() and file_path.is_file():
            media_type = mimetypes.guess_type(str(file_path))[0]
            response = FileResponse(file_path, media_type=media_type)
            if str(file_path).endswith(".html"):
                response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            return response

        response = FileResponse(static_path / "index.html", media_type="text/html")
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    logger.info(t("startup.webui_static_files_configured", static_path=static_path))


def _resolve_static_path() -> Path | None:
    try:
        module = import_module("maibot_dashboard")
        get_dist_path = getattr(module, "get_dist_path", None)
        if callable(get_dist_path):
            package_path = get_dist_path()
            if isinstance(package_path, Path) and package_path.exists():
                return package_path
    except Exception:
        pass

    base_dir = _get_project_root()
    static_path = base_dir / "dashboard" / "dist"
    if static_path.exists():
        return static_path
    return None


def show_access_token():
    """显示 WebUI Access Token（供启动时调用）"""
    try:
        from src.webui.core import get_token_manager

        token_manager = get_token_manager()
        current_token = token_manager.get_token()
        logger.info(t("startup.webui_access_token", token=current_token))
        logger.info(t("startup.webui_access_token_login_hint"))
    except Exception as e:
        logger.error(t("startup.webui_access_token_failed", error=e))
