"""SIWC registration and failure recovery using generated identities, never live accounts."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit

import base64
import hashlib
import json
import threading
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.common import chatgpt_auth as auth


def token_response(**changes):
    return {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": auth.SCOPES,
        **changes,
    }


def saved_account(store, **changes):
    with store.locked("default") as path:
        record = {
            "client_id": "mai-test-client",
            "subject": "test-user",
            "access_token": "test-old-access",
            "refresh_token": "test-old-refresh",
            "scopes": auth.SCOPES.split(),
            "expires_at": 0,
            **changes,
        }
        auth._write(path, record)
    return path


@pytest.fixture
def identity(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public["kid"] = "test-kid"
    monkeypatch.setattr(auth, "_discovery", lambda: {"jwks_uri": auth.ISSUER + "/keys"})
    monkeypatch.setattr(auth, "_json_request", lambda *args, **kwargs: {"keys": [public]})

    def sign(**changes):
        claims = {
            "iss": auth.ISSUER,
            "aud": "mai-test-client",
            "sub": "test-user",
            "iat": time.time(),
            "exp": time.time() + 300,
            "nonce": "test-nonce",
            "email": "test@example.invalid",
            **changes,
        }
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-kid"})

    return sign


def test_identity_signature_and_binding(identity):
    assert auth._verify_identity(identity(), "mai-test-client", "test-nonce")["sub"] == "test-user"
    for changes in (
        {"aud": "other-app"},
        {"iss": "https://other.invalid"},
        {"exp": 0},
        {"nonce": "other"},
        {"azp": "other-app"},
        {"aud": ["mai-test-client", "other"]},
    ):
        with pytest.raises(auth.ChatGPTAuthError):
            auth._verify_identity(identity(**changes), "mai-test-client", "test-nonce")
    with pytest.raises(auth.ChatGPTAuthError):
        auth._credentials(
            token_response(id_token=identity(sub="other-account")),
            {"client_id": "mai-test-client", "subject": "test-user"},
            "test-nonce",
        )


@pytest.mark.parametrize(
    "query",
    [
        "state=bad&code=c&client_id=registered",
        "state=s&state=s&code=c&client_id=registered",
        "state=s&code=c&code=d&client_id=registered",
        "state=s&code=c",
        "state=s&code=c&client_id=dynamic_agent_client",
        "state=s&error=access_denied",
        "state=s&code=c&client_id=a&client_id=b",
    ],
)
def test_callback_rejects_unbound_or_ambiguous_result(query):
    with pytest.raises(auth.ChatGPTAuthError):
        auth._callback(query, "s", None)


def test_registration_reuse_and_conflict():
    assert auth._callback("state=s&code=c&client_id=registered", "s", None) == ("c", "registered")
    assert auth._callback("state=s&code=c", "s", "registered") == ("c", "registered")
    with pytest.raises(auth.ChatGPTAuthError):
        auth._callback("state=s&code=c&client_id=other", "s", "registered")


def test_private_store_atomicity_and_rotation_lock(tmp_path, monkeypatch):
    store = auth.ChatGPTAccountStore(tmp_path / "private")
    path = saved_account(store)
    calls = []

    def exchange(method, url, **kwargs):
        calls.append(kwargs["data"])
        time.sleep(0.02)
        return token_response()

    monkeypatch.setattr(auth, "_json_request", exchange)
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda _: store.access_token("default"), range(4))) == ["test-access"] * 4
    assert len(calls) == 1
    assert calls[0]["client_id"] == "mai-test-client"
    assert calls[0]["refresh_token"] == "test-old-refresh"
    assert "scope" not in calls[0]
    assert path.stat().st_mode & 0o777 == 0o600
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert auth._read(path)["refresh_token"] == "test-refresh"
    assert "test-access" not in json.dumps(store.accounts())
    path.chmod(0o644)
    with pytest.raises(auth.ChatGPTAuthError):
        store.access_token("default")


def test_refresh_checkpoint_survives_jwks_failure(tmp_path, monkeypatch):
    store = auth.ChatGPTAccountStore(tmp_path / "private")
    path = saved_account(store)
    exchange = []

    def rotated(*args, **kwargs):
        exchange.append(True)
        return token_response(id_token="test-id-token")

    monkeypatch.setattr(auth, "_json_request", rotated)

    def outage(*args):
        raise auth.ChatGPTAuthError("test network outage")

    monkeypatch.setattr(auth, "_verify_identity", outage)
    with pytest.raises(auth.ChatGPTAuthError):
        store.access_token("default")
    assert auth._read(path)["pending_grant"]["response"]["refresh_token"] == "test-refresh"
    monkeypatch.setattr(auth, "_verify_identity", lambda *args: {"sub": "test-user"})
    assert store.access_token("default") == "test-access"
    assert len(exchange) == 1
    assert "pending_grant" not in auth._read(path)


def test_grant_without_plan_permission_is_not_usable(tmp_path, monkeypatch):
    store = auth.ChatGPTAccountStore(tmp_path / "private")
    path = saved_account(store)
    monkeypatch.setattr(auth, "_json_request", lambda *args, **kwargs: token_response(scope="openid email"))
    with pytest.raises(auth.ChatGPTAuthError, match="套餐"):
        store.access_token("default")
    assert auth._read(path)["access_token"] == "test-old-access"


def test_models_use_selected_account_and_public_catalog(tmp_path, monkeypatch):
    store = auth.ChatGPTAccountStore(tmp_path / "private")
    saved_account(store, expires_at=time.time() + 600)

    def catalog(method, url, **kwargs):
        assert url == auth.RESOURCE + "/models"
        assert kwargs["headers"]["Authorization"] == "Bearer test-old-access"
        return {
            "models": [
                {"slug": "test-model", "display_name": "Test model", "visibility": "list"},
                {"slug": "hidden", "visibility": "hide"},
            ]
        }

    monkeypatch.setattr(auth, "_json_request", catalog)
    assert store.models("default") == [{"id": "test-model", "name": "Test model"}]


def test_logout_retries_revocation_and_keeps_registration(tmp_path, monkeypatch):
    store = auth.ChatGPTAccountStore(tmp_path / "private")
    path = saved_account(store, id_token="test-id")
    monkeypatch.setattr(auth, "_discovery", lambda: {"revocation_endpoint": auth.ISSUER + "/revoke"})
    attempts = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, data):
            attempts.append(data)
            return httpx.Response(503)

    monkeypatch.setattr(auth.httpx, "Client", Client)
    assert store.logout("default") is False
    assert len(attempts) == 3
    assert all(item["token"] == "test-old-refresh" for item in attempts)
    assert auth._read(path) == {"client_id": "mai-test-client", "subject": "test-user"}


def test_explicit_login_loopback_pkce_and_returning_registration(tmp_path, monkeypatch):
    store = auth.ChatGPTAccountStore(tmp_path / "private")
    opened, exchanges, workers = [], [], []

    def browser(url):
        query = parse_qs(urlsplit(url).query)
        opened.append(query)
        callback = query["redirect_uri"][0]

        def deliver():
            with httpx.Client(trust_env=False) as client:
                assert client.get(callback + "?state=wrong").status_code == 400
                reply = client.get(
                    callback
                    + "?"
                    + urlencode({"state": query["state"][0], "code": "test-code", "client_id": "mai-test-client"})
                )
                assert reply.status_code == 200
                assert reply.headers["cache-control"] == "no-store"

        worker = threading.Thread(target=deliver)
        workers.append(worker)
        worker.start()
        return True

    def exchange(method, url, **kwargs):
        data = kwargs["data"]
        exchanges.append(data)
        assert data["redirect_uri"] == opened[-1]["redirect_uri"][0]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).decode().rstrip("=")
        )
        assert challenge == opened[-1]["code_challenge"][0]
        assert data["client_id"] == "mai-test-client"
        return token_response(id_token="test-id-token")

    monkeypatch.setattr(auth.webbrowser, "open", browser)
    monkeypatch.setattr(auth, "_json_request", exchange)
    monkeypatch.setattr(
        auth,
        "_verify_identity",
        lambda token, client, nonce: {"sub": "test-user"} if nonce == opened[-1]["nonce"][0] else {},
    )
    store.login("default", timeout=5)
    store.login("default", timeout=5)
    for worker in workers:
        worker.join(timeout=5)
    assert len(exchanges) == 2
    assert opened[0]["client_id"] == ["dynamic_agent_client"]
    assert opened[0]["agent_name_hint"] == ["MaiBot"]
    assert opened[1]["client_id"] == ["mai-test-client"]
    assert opened[0]["ext_agent_host_id"] == opened[1]["ext_agent_host_id"]
    assert opened[0]["state"] != opened[1]["state"]
    assert opened[0]["nonce"] != opened[1]["nonce"]
    assert opened[1]["id_token_hint"] == ["test-id-token"]


def test_declining_login_and_listing_never_creates_credentials(tmp_path, monkeypatch, capsys):
    from src.cli.chatgpt import run_chatgpt_command

    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert run_chatgpt_command("login", tmp_path, "default") == 1
    assert run_chatgpt_command("accounts", tmp_path, "default") == 0
    assert not (tmp_path / "data").exists()
    assert "没有启动登录" in capsys.readouterr().out
