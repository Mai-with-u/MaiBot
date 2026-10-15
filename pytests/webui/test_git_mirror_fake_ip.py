from types import SimpleNamespace

import asyncio
import ipaddress

import httpx
import pytest

from src.webui.services import git_mirror_service
from src.webui.utils import network_security


@pytest.fixture
def mirror_service(monkeypatch):
    monkeypatch.setattr(network_security, "_should_enforce_public_network", lambda _: True)
    mirrors = git_mirror_service.GitMirrorConfig.DEFAULT_MIRRORS
    config = SimpleNamespace(
        get_enabled_mirrors=lambda: mirrors,
        get_mirror_by_id=lambda mirror_id: next(m for m in mirrors if m["id"] == mirror_id),
    )
    return git_mirror_service.GitMirrorService(max_retries=1, config=config)


def resolve_to(monkeypatch, *addresses):
    monkeypatch.setattr(
        network_security,
        "_resolve_ip_addresses",
        lambda *_: {ipaddress.ip_address(address) for address in addresses},
    )


def mock_http(monkeypatch, status=200):
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(status, text='{"plugins": []}', headers={"location": "http://127.0.0.1/private"})

    def client(**kwargs):
        return client_type(**kwargs, transport=httpx.MockTransport(respond), trust_env=False)

    monkeypatch.setattr(git_mirror_service.httpx, "AsyncClient", client)
    return requests


@pytest.mark.parametrize("mirror", git_mirror_service.GitMirrorConfig.DEFAULT_MIRRORS, ids=lambda m: m["id"])
def test_builtin_raw_mirror_reaches_http_with_fake_ip(mirror_service, monkeypatch, mirror):
    resolve_to(monkeypatch, "198.18.0.209")
    requests = mock_http(monkeypatch)
    result = asyncio.run(
        mirror_service.fetch_raw_file(
            "Mai-with-u",
            "plugin-repo",
            "main",
            "plugin_details.json",
            mirror_id=mirror["id"],
            report_progress=False,
        )
    )
    assert result["success"] is True
    assert result["data"] == '{"plugins": []}'
    assert str(requests[0].url) == mirror["raw_prefix"] + "/Mai-with-u/plugin-repo/main/plugin_details.json"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"])
def test_builtin_raw_mirror_still_blocks_other_private_addresses(mirror_service, monkeypatch, address):
    resolve_to(monkeypatch, "198.18.0.209", address)
    requests = mock_http(monkeypatch)
    result = asyncio.run(
        mirror_service._fetch_raw_from_mirror(
            "Mai-with-u",
            "plugin-repo",
            "main",
            "plugin_details.json",
            git_mirror_service.GitMirrorConfig.DEFAULT_MIRRORS[0],
        )
    )
    assert result["success"] is False
    assert result["status_code"] == 400
    assert not requests


@pytest.mark.parametrize(
    "prefix",
    [
        "https://198.18.0.209",
        "http://raw.githubusercontent.com",
        "https://raw.githubusercontent.com@evil.example",
    ],
)
def test_invalid_custom_prefix_does_not_bypass_validation(mirror_service, monkeypatch, prefix):
    resolve_to(monkeypatch, "198.18.0.209")
    requests = mock_http(monkeypatch)
    mirror = {**git_mirror_service.GitMirrorConfig.DEFAULT_MIRRORS[2], "raw_prefix": prefix}
    result = asyncio.run(mirror_service._fetch_raw_from_mirror("a", "b", "main", "file", mirror))
    assert result["success"] is False
    assert not requests


def test_custom_url_and_clone_prefix_accept_fake_ip(mirror_service, monkeypatch):
    resolve_to(monkeypatch, "198.18.0.209")
    requests = mock_http(monkeypatch)
    result = asyncio.run(
        mirror_service.fetch_raw_file(
            "a",
            "b",
            "main",
            "file",
            custom_url="https://custom.example/a/b/main/file",
        )
    )
    assert result["success"] is True
    assert git_mirror_service._validate_mirror_prefix("https://git.example", "克隆前缀") == "https://git.example"
    assert len(requests) == 1


def test_builtin_raw_redirect_is_not_followed(mirror_service, monkeypatch):
    resolve_to(monkeypatch, "198.18.0.209")
    requests = mock_http(monkeypatch, status=302)
    result = asyncio.run(
        mirror_service._fetch_raw_from_mirror(
            "a",
            "b",
            "main",
            "file",
            git_mirror_service.GitMirrorConfig.DEFAULT_MIRRORS[2],
        )
    )
    assert result["success"] is False
    assert len(requests) == 1
    assert requests[0].url.host == "raw.githubusercontent.com"


def test_public_dns_still_works(mirror_service, monkeypatch):
    resolve_to(monkeypatch, "8.8.8.8")
    requests = mock_http(monkeypatch)
    result = asyncio.run(
        mirror_service.fetch_raw_file(
            "a",
            "b",
            "main",
            "file",
            custom_url="https://custom.example/file",
        )
    )
    assert result["success"] is True
    assert len(requests) == 1


@pytest.mark.parametrize(
    "url,allow_fake_ip",
    [
        ("https://custom.example/file", False),
        ("https://198.18.0.209/file", True),
        ("http://raw.githubusercontent.com/file", True),
    ],
)
def test_general_url_validation_keeps_fake_ip_blocked(monkeypatch, url, allow_fake_ip):
    resolve_to(monkeypatch, "198.18.0.209")
    with pytest.raises(ValueError, match="禁止访问非公网地址"):
        network_security.validate_public_url(url, require_public_network=True, allow_fake_ip=allow_fake_ip)


def test_custom_mirror_save_and_reload_with_fake_ip(tmp_path, monkeypatch):
    monkeypatch.setattr(network_security, "_should_enforce_public_network", lambda _: True)
    monkeypatch.setattr(git_mirror_service.GitMirrorConfig, "CONFIG_FILE", tmp_path / "webui.json")
    resolve_to(monkeypatch, "198.19.0.1")
    config = git_mirror_service.GitMirrorConfig()
    config.add_mirror("custom", "自定义", "https://raw.example", "https://git.example")
    config.update_mirror("custom", raw_prefix="https://raw.example:8443/prefix")
    reopened = git_mirror_service.GitMirrorConfig()
    assert reopened.get_mirror_by_id("custom")["raw_prefix"] == "https://raw.example:8443/prefix"


@pytest.mark.parametrize("custom", [True, False])
def test_clone_reaches_git_with_fake_ip(mirror_service, monkeypatch, tmp_path, custom):
    resolve_to(monkeypatch, "198.18.0.209")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_mirror_service.subprocess, "run", run)
    result = asyncio.run(
        mirror_service.clone_repository(
            "owner",
            "repo",
            tmp_path / "plugin",
            custom_url="https://git.example/owner/repo.git" if custom else None,
        )
    )
    assert result["success"] is True
    assert len(commands) == 1
    assert commands[0][:2] == ["git", "clone"]
    assert commands[0][-2].startswith("https://")


@pytest.mark.parametrize("remote_url", [None, "https://git.example/owner/repo.git"])
def test_update_reaches_git_with_fake_ip(mirror_service, monkeypatch, tmp_path, remote_url):
    resolve_to(monkeypatch, "198.18.0.209")
    (tmp_path / ".git").mkdir()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_mirror_service.subprocess, "run", run)
    result = asyncio.run(mirror_service.pull_repository(tmp_path, remote_url=remote_url))
    assert result["success"] is True
    network_commands = [c for c in commands if "fetch" in c or "pull" in c]
    assert len(network_commands) == 2
    assert [c[:2] for c in network_commands] == [["git", "fetch"], ["git", "pull"]]
    if remote_url:
        assert commands[0] == ["git", "remote", "set-url", "origin", remote_url]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254"])
def test_custom_git_target_rejected_before_execution(mirror_service, monkeypatch, tmp_path, address):
    resolve_to(monkeypatch, address)
    (tmp_path / ".git").mkdir()

    def fail_run(*args, **kwargs):
        pytest.fail("被拒绝的目标不应启动 Git")

    monkeypatch.setattr(git_mirror_service.subprocess, "run", fail_run)
    result = asyncio.run(
        mirror_service.clone_repository("a", "b", tmp_path / "new", custom_url="https://git.example/a.git")
    )
    assert result["status_code"] == 400
    result = asyncio.run(mirror_service.pull_repository(tmp_path, remote_url="https://git.example/a.git"))
    assert result["status_code"] == 400


@pytest.mark.parametrize("host", ["198.18.1", "3323068417", "0xc6120001", "0306.022.0.1"])
def test_noncanonical_fake_ip_literals_are_blocked(monkeypatch, host):
    resolve_to(monkeypatch, "198.18.0.1")
    with pytest.raises(ValueError, match="禁止访问非公网地址"):
        network_security.validate_public_url(f"https://{host}/file", require_public_network=True, allow_fake_ip=True)


@pytest.mark.parametrize("operation", ["raw", "clone"])
def test_all_mirrors_failed_preserves_validation_reason(mirror_service, monkeypatch, tmp_path, operation):
    resolve_to(monkeypatch, "10.0.0.1")
    if operation == "raw":
        result = asyncio.run(mirror_service.fetch_raw_file("a", "b", "main", "file", report_progress=False))
    else:
        result = asyncio.run(mirror_service.clone_repository("a", "b", tmp_path / "plugin"))
    assert result["success"] is False
    assert "禁止访问非公网地址: 10.0.0.1" in result["error"]
    assert "github" in result["error"]


def test_git_clone_preserves_normal_initial_redirect(mirror_service, monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    import os

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path.startswith("/old/"):
                self.send_response(301)
                self.send_header("Location", self.path.replace("/old/", "/new/", 1))
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-git-upload-pack-advertisement")
                self.end_headers()
                line = b"# service=git-upload-pack\n"
                self.wfile.write(f"{len(line) + 4:04x}".encode() + line + b"00000000")

    # 真实 Git 访问隔离本机服务，不读取用户 Git 配置或经过用户代理。
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.delenv("GIT_CONFIG_PARAMETERS", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(
            mirror_service._clone_with_url(
                f"http://127.0.0.1:{server.server_port}/old/",
                tmp_path / "clone",
                None,
                None,
                "fixture",
            )
        )
        assert result["success"] is True, result
        assert (tmp_path / "clone" / ".git").is_dir()
        assert any(path.startswith("/new/info/refs") for path in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
