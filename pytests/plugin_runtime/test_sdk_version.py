from importlib.metadata import PackageNotFoundError
from unittest.mock import Mock

import pytest

from src.plugin_runtime.runner import rpc_client


@pytest.mark.parametrize("local_version", ["2.12.0.dev1", None])
def test_sdk_version_uses_local_source_or_installed_metadata(
    monkeypatch: pytest.MonkeyPatch, local_version: str | None
) -> None:
    monkeypatch.setattr(rpc_client, "read_local_sdk_version", lambda: local_version)
    installed_version = Mock(return_value="2.12.0")
    monkeypatch.setattr(rpc_client, "version", installed_version)

    assert rpc_client._get_sdk_version() == (local_version or "2.12.0")
    if local_version is None:
        installed_version.assert_called_once_with("maibot-plugin-sdk")
    else:
        installed_version.assert_not_called()


@pytest.mark.parametrize("error", [PackageNotFoundError("maibot-plugin-sdk"), ValueError("invalid metadata")])
def test_sdk_version_preserves_metadata_errors(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setattr(rpc_client, "read_local_sdk_version", lambda: None)
    monkeypatch.setattr(rpc_client, "version", Mock(side_effect=error))

    with pytest.raises(type(error)) as caught:
        rpc_client._get_sdk_version()
    assert caught.value is error
