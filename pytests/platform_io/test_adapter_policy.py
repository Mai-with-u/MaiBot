from pathlib import Path

from src.platform_io.adapter_policy import AdapterIdentity, AdapterPolicyManager


def test_adapter_policy_missing_file_is_unconfigured(tmp_path: Path) -> None:
    manager = AdapterPolicyManager(tmp_path / "missing.toml")

    result = manager.evaluate(
        AdapterIdentity(plugin_id="maibot-team.snowluma-adapter", gateway_name="snowluma_gateway", platform="qq"),
        chat_type="group",
        target_id="123",
    )

    assert result.allowed is True
    assert result.configured is False
    assert result.reason == "no_host_policy"


def test_adapter_policy_default_whitelist(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[defaults.group]
list_type = "whitelist"
ids = ["10001"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)

    allowed = manager.evaluate(AdapterIdentity(platform="qq"), chat_type="group", target_id="10001")
    denied = manager.evaluate(AdapterIdentity(platform="qq"), chat_type="group", target_id="10002")

    assert allowed.allowed is True
    assert allowed.configured is True
    assert allowed.source == "defaults"
    assert denied.allowed is False
    assert denied.reason == "not_in_whitelist"


def test_adapter_policy_adapter_specific_blacklist_overrides_default(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[defaults.group]
list_type = "whitelist"
ids = ["*"]

[[adapters]]
plugin_id = "maibot-team.snowluma-adapter"
gateway_name = "snowluma_gateway"

[adapters.group]
list_type = "blacklist"
ids = ["10001"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)
    identity = AdapterIdentity(
        plugin_id="maibot-team.snowluma-adapter",
        gateway_name="snowluma_gateway",
        platform="qq",
    )

    denied = manager.evaluate(identity, chat_type="group", target_id="10001")
    allowed = manager.evaluate(identity, chat_type="group", target_id="10002")

    assert denied.allowed is False
    assert denied.source == "adapter"
    assert denied.reason == "matched_blacklist"
    assert allowed.allowed is True


def test_adapter_policy_chat_override_can_allow_block_and_inherit(tmp_path: Path) -> None:
    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[defaults.group]
list_type = "whitelist"
ids = ["10002"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)
    identity = AdapterIdentity(
        adapter_id="adapter.snowluma.gateway",
        plugin_id="maibot-team.snowluma-adapter",
        gateway_name="snowluma_gateway",
        platform="qq",
    )

    manager.set_chat_override(identity, chat_type="group", target_id="10001", action="allow")
    allowed = manager.evaluate(identity, chat_type="group", target_id="10001")

    manager.set_chat_override(identity, chat_type="group", target_id="10001", action="block")
    blocked = manager.evaluate(identity, chat_type="group", target_id="10001")

    manager.set_chat_override(identity, chat_type="group", target_id="10001", action="inherit")
    inherited = manager.evaluate(identity, chat_type="group", target_id="10001")

    assert allowed.allowed is True
    assert allowed.reason == "matched_allow_override"
    assert blocked.allowed is False
    assert blocked.reason == "matched_deny_override"
    assert inherited.allowed is False
    assert inherited.source == "defaults"
    assert inherited.reason == "not_in_whitelist"
