from pathlib import Path

import tomlkit

from src.platform_io.adapter_policy import AdapterPolicyManager


def test_remove_chat_overrides_clears_matching_route_only(tmp_path: Path) -> None:
    """删除聊天流时应清理匹配路由中的放行和阻止覆盖，不影响其他平台。"""

    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[[adapters]]
plugin_id = "qq-adapter"
platform = "qq"
account_id = "bot-1"

[adapters.group]
default_action = "allow"
allow_ids = ["group-1", "group-2"]
deny_ids = ["group-1", "group-3"]

[[adapters]]
plugin_id = "telegram-adapter"
platform = "telegram"

[adapters.group]
deny_ids = ["group-1"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)

    removed_count = manager.remove_chat_overrides(
        chat_type="group",
        target_id="group-1",
        platform="qq",
        account_id="bot-1",
    )

    policy = tomlkit.parse(policy_path.read_text(encoding="utf-8")).unwrap()
    assert removed_count == 2
    assert policy["adapters"][0]["group"] == {
        "default_action": "allow",
        "allow_ids": ["group-2"],
        "deny_ids": ["group-3"],
    }
    assert policy["adapters"][1]["group"]["deny_ids"] == ["group-1"]


def test_remove_chat_overrides_prunes_empty_policy(tmp_path: Path) -> None:
    """聊天级覆盖是适配器唯一配置时，应同时清理空策略节点。"""

    policy_path = tmp_path / "adapter_policy.toml"
    policy_path.write_text(
        """
[[adapters]]
adapter_id = "gateway:qq"
platform = "qq"

[adapters.group]
deny_ids = ["group-1"]
""".strip(),
        encoding="utf-8",
    )
    manager = AdapterPolicyManager(policy_path)

    removed_count = manager.remove_chat_overrides(
        chat_type="group",
        target_id="group-1",
        platform="qq",
    )

    policy = tomlkit.parse(policy_path.read_text(encoding="utf-8")).unwrap()
    assert removed_count == 1
    assert "adapters" not in policy
