from unittest.mock import patch

from src.webui import app as webui_app


def test_ensure_static_path_ready_uses_existing_static_path(tmp_path) -> None:
    static_path = tmp_path / "dist"
    static_path.mkdir()
    (static_path / "index.html").write_text("<html></html>", encoding="utf-8")

    with (
        patch.object(webui_app, "_resolve_static_path", return_value=static_path),
        patch.object(webui_app, "_try_build_dashboard") as build_mock,
    ):
        result = webui_app._ensure_static_path_ready()

    assert result == static_path
    build_mock.assert_not_called()


def test_ensure_static_path_ready_retries_after_auto_build(tmp_path) -> None:
    static_path = tmp_path / "dist"
    static_path.mkdir()
    (static_path / "index.html").write_text("<html></html>", encoding="utf-8")

    with (
        patch.object(webui_app, "_resolve_static_path", side_effect=[None, static_path]),
        patch.object(
            webui_app,
            "_try_build_dashboard",
            return_value=webui_app.DashboardAutoRecoveryResult(succeeded=True),
        ) as build_mock,
    ):
        result = webui_app._ensure_static_path_ready()

    assert result == static_path
    build_mock.assert_called_once_with()


def test_ensure_static_path_ready_logs_manual_hint_when_auto_build_fails() -> None:
    with (
        patch.object(webui_app, "_resolve_static_path", return_value=None),
        patch.object(
            webui_app,
            "_try_build_dashboard",
            return_value=webui_app.DashboardAutoRecoveryResult(
                succeeded=False,
                manual_recovery_command=webui_app._MANUAL_BUILD_COMMAND,
            ),
        ),
        patch.object(webui_app.logger, "warning") as warning_mock,
    ):
        result = webui_app._ensure_static_path_ready()

    assert result is None
    warning_mock.assert_any_call(webui_app.t("startup.webui_auto_recovery_failed"))
    warning_mock.assert_any_call(
        webui_app.t("startup.webui_manual_build_hint", command=webui_app._MANUAL_BUILD_COMMAND)
    )


def test_setup_static_files_does_not_duplicate_warning_when_static_path_is_unavailable() -> None:
    app = webui_app.FastAPI()

    with (
        patch.object(webui_app, "_ensure_static_path_ready", return_value=None),
        patch.object(webui_app.logger, "warning") as warning_mock,
    ):
        webui_app._setup_static_files(app)

    warning_mock.assert_not_called()


def test_get_dashboard_build_command_defaults_to_npm(tmp_path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    with patch.object(
        webui_app.shutil,
        "which",
        side_effect=lambda tool_name: "/usr/bin/npm" if tool_name == "npm" else None,
    ):
        command = webui_app._get_dashboard_build_command(tmp_path)

    assert command == ["npm", "run", "build"]


def test_try_build_dashboard_installs_missing_dependencies_before_build(monkeypatch, tmp_path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    run_results = [
        webui_app.CompletedProcess(args=["npm", "install", "--no-package-lock"], returncode=0, stdout="", stderr=""),
        webui_app.CompletedProcess(args=["npm", "run", "build"], returncode=0, stdout="", stderr=""),
    ]

    monkeypatch.setattr(webui_app, "_get_dashboard_root", lambda: tmp_path)
    monkeypatch.setattr(webui_app, "_should_auto_install_dashboard_dependencies", lambda dashboard_root: True)

    with (
        patch.object(webui_app, "_get_dashboard_build_command", return_value=["npm", "run", "build"]),
        patch.object(webui_app, "run", side_effect=run_results) as run_mock,
    ):
        result = webui_app._try_build_dashboard()

    assert result.succeeded is True
    assert run_mock.call_count == 2
    install_call = run_mock.call_args_list[0]
    build_call = run_mock.call_args_list[1]
    assert install_call.args[0] == ["npm", "install", "--no-package-lock"]
    assert install_call.kwargs["cwd"] == tmp_path
    assert build_call.args[0] == ["npm", "run", "build"]
    assert build_call.kwargs["cwd"] == tmp_path


def test_resolve_static_path_uses_dashboard_dist(monkeypatch, tmp_path) -> None:
    dashboard_dist = tmp_path / "dashboard" / "dist"
    dashboard_dist.mkdir(parents=True)

    monkeypatch.setattr(webui_app, "_get_project_root", lambda: tmp_path)

    with patch.object(webui_app, "import_module", side_effect=ImportError):
        resolved_path = webui_app._resolve_static_path()

    assert resolved_path == dashboard_dist
