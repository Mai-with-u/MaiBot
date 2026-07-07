"""统一适配器聊天名单策略。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import tomlkit
from tomlkit.items import AoT, Table

from src.common.logger import get_logger

logger = get_logger("platform_io.adapter_policy")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_POLICY_PATH = _PROJECT_ROOT / "config" / "adapter_policy.toml"
_SUPPORTED_CHAT_TYPES = {"group", "private"}
_SUPPORTED_LIST_TYPES = {"whitelist", "blacklist"}
_SUPPORTED_OVERRIDE_ACTIONS = {"allow", "block", "inherit"}


@dataclass(frozen=True, slots=True)
class AdapterIdentity:
    """适配器实例身份。"""

    adapter_id: str = ""
    plugin_id: str = ""
    gateway_name: str = ""
    platform: str = ""
    account_id: Optional[str] = None
    scope: Optional[str] = None


@dataclass(frozen=True, slots=True)
class AdapterPolicyResult:
    """适配器名单策略判断结果。"""

    allowed: bool
    configured: bool
    chat_type: str
    target_id: str
    list_type: str = ""
    source: str = ""
    reason: str = ""
    matched_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为 API/SDK 友好的字典。"""

        return {
            "allowed": self.allowed,
            "configured": self.configured,
            "chat_type": self.chat_type,
            "target_id": self.target_id,
            "list_type": self.list_type,
            "source": self.source,
            "reason": self.reason,
            "matched_ids": list(self.matched_ids),
        }


class AdapterPolicyManager:
    """读取并执行主程序统一适配器黑白名单策略。"""

    def __init__(self, policy_path: Path = _DEFAULT_POLICY_PATH) -> None:
        self._policy_path = policy_path

    @property
    def policy_path(self) -> Path:
        """返回策略文件路径。"""

        return self._policy_path

    def evaluate(
        self,
        identity: AdapterIdentity,
        *,
        chat_type: str,
        target_id: str,
    ) -> AdapterPolicyResult:
        """判断某个聊天目标是否允许通过指定适配器。"""

        normalized_chat_type = self._normalize_chat_type(chat_type)
        normalized_target_id = str(target_id or "").strip()
        if not normalized_target_id:
            return AdapterPolicyResult(
                allowed=False,
                configured=True,
                chat_type=normalized_chat_type,
                target_id=normalized_target_id,
                reason="missing_target_id",
            )

        policy_data = self._load_policy_data()
        for policy, source in self._iter_resolved_policies(policy_data, identity, normalized_chat_type):
            policy_result = self._evaluate_policy(
                policy,
                source=source,
                chat_type=normalized_chat_type,
                target_id=normalized_target_id,
            )
            if policy_result is not None:
                return policy_result

        return AdapterPolicyResult(
            allowed=True,
            configured=False,
            chat_type=normalized_chat_type,
            target_id=normalized_target_id,
            reason="no_host_policy",
        )

    def set_chat_override(
        self,
        identity: AdapterIdentity,
        *,
        chat_type: str,
        target_id: str,
        action: str,
    ) -> None:
        """为某个适配器和聊天流写入显式放行/阻止覆盖规则。"""

        normalized_chat_type = self._normalize_chat_type(chat_type)
        normalized_target_id = str(target_id or "").strip()
        normalized_action = str(action or "").strip().lower()
        if not normalized_target_id:
            raise ValueError("target_id 不能为空")
        if normalized_action not in _SUPPORTED_OVERRIDE_ACTIONS:
            raise ValueError("action 必须是 allow、block 或 inherit")

        policy_doc = self._load_policy_doc()
        adapters = self._ensure_adapter_tables(policy_doc)
        adapter_policy = self._find_or_create_exact_adapter_policy(adapters, identity)
        chat_policy = self._ensure_chat_policy_table(adapter_policy, normalized_chat_type)

        allow_ids = self._normalize_id_list(chat_policy.get("allow_ids"))
        deny_ids = self._normalize_id_list(chat_policy.get("deny_ids"))
        if normalized_action == "allow":
            allow_ids = self._append_unique_id(allow_ids, normalized_target_id)
            deny_ids = [item for item in deny_ids if item != normalized_target_id]
        elif normalized_action == "block":
            deny_ids = self._append_unique_id(deny_ids, normalized_target_id)
            allow_ids = [item for item in allow_ids if item != normalized_target_id]
        else:
            allow_ids = [item for item in allow_ids if item != normalized_target_id]
            deny_ids = [item for item in deny_ids if item != normalized_target_id]

        self._set_or_remove_id_list(chat_policy, "allow_ids", allow_ids)
        self._set_or_remove_id_list(chat_policy, "deny_ids", deny_ids)
        self._prune_empty_ui_policy(adapter_policy, normalized_chat_type)
        self._prune_empty_adapter_policy(adapters, adapter_policy)
        self._policy_path.parent.mkdir(parents=True, exist_ok=True)
        with self._policy_path.open("w", encoding="utf-8") as policy_file:
            policy_file.write(tomlkit.dumps(policy_doc))

    def _evaluate_policy(
        self,
        policy: Mapping[str, Any],
        *,
        source: str,
        chat_type: str,
        target_id: str,
    ) -> Optional[AdapterPolicyResult]:
        if bool(policy.get("disabled", False)):
            return AdapterPolicyResult(
                allowed=False,
                configured=True,
                chat_type=chat_type,
                target_id=target_id,
                source=source,
                reason="adapter_disabled",
            )

        deny_ids = self._normalize_id_list(policy.get("deny_ids"))
        allow_ids = self._normalize_id_list(policy.get("allow_ids"))
        if target_id in deny_ids or "*" in deny_ids:
            return AdapterPolicyResult(
                allowed=False,
                configured=True,
                chat_type=chat_type,
                target_id=target_id,
                list_type="override",
                source=source,
                reason="matched_deny_override",
                matched_ids=["*"] if "*" in deny_ids else [target_id],
            )
        if target_id in allow_ids or "*" in allow_ids:
            return AdapterPolicyResult(
                allowed=True,
                configured=True,
                chat_type=chat_type,
                target_id=target_id,
                list_type="override",
                source=source,
                reason="matched_allow_override",
                matched_ids=["*"] if "*" in allow_ids else [target_id],
            )

        if "list_type" not in policy and "ids" not in policy:
            return None

        list_type = str(policy.get("list_type") or "whitelist").strip().lower()
        if list_type not in _SUPPORTED_LIST_TYPES:
            list_type = "whitelist"

        ids = self._normalize_id_list(policy.get("ids"))
        wildcard_matched = "*" in ids
        target_matched = target_id in ids or wildcard_matched
        if list_type == "whitelist":
            allowed = target_matched
            reason = "matched_whitelist" if allowed else "not_in_whitelist"
        else:
            allowed = not target_matched
            reason = "matched_blacklist" if not allowed else "not_in_blacklist"

        return AdapterPolicyResult(
            allowed=allowed,
            configured=True,
            chat_type=chat_type,
            target_id=target_id,
            list_type=list_type,
            source=source,
            reason=reason,
            matched_ids=["*"] if wildcard_matched else ([target_id] if target_matched else []),
        )

    def _load_policy_data(self) -> Dict[str, Any]:
        """读取策略 TOML；不存在时返回空策略。"""

        if not self._policy_path.exists():
            return {}

        try:
            with self._policy_path.open("r", encoding="utf-8") as policy_file:
                loaded = tomlkit.load(policy_file).unwrap()
        except Exception as exc:
            logger.warning(f"读取适配器策略失败，统一策略本次不生效: {exc}")
            return {}

        return loaded if isinstance(loaded, dict) else {}

    def _load_policy_doc(self) -> Any:
        """读取可写 TOML 文档；不存在时创建空文档。"""

        if not self._policy_path.exists():
            return tomlkit.document()

        with self._policy_path.open("r", encoding="utf-8") as policy_file:
            return tomlkit.load(policy_file)

    def _iter_resolved_policies(
        self,
        policy_data: Mapping[str, Any],
        identity: AdapterIdentity,
        chat_type: str,
    ) -> List[tuple[Dict[str, Any], str]]:
        """按适配器实例到默认模板的顺序解析策略。"""

        resolved_policies = [
            (policy, "adapter") for policy in self._resolve_adapter_policies(policy_data, identity, chat_type)
        ]

        defaults = policy_data.get("defaults")
        if isinstance(defaults, Mapping):
            default_policy = self._resolve_typed_policy(defaults, chat_type)
            if default_policy is not None:
                resolved_policies.append((default_policy, "defaults"))

        return resolved_policies

    def _resolve_adapter_policies(
        self,
        policy_data: Mapping[str, Any],
        identity: AdapterIdentity,
        chat_type: str,
    ) -> List[Dict[str, Any]]:
        adapters = policy_data.get("adapters")
        if not isinstance(adapters, list):
            return []

        matched_policies: List[tuple[int, Dict[str, Any]]] = []
        for item in adapters:
            if not isinstance(item, Mapping):
                continue
            if not self._adapter_policy_matches(item, identity):
                continue

            if bool(item.get("disabled", False)):
                matched_policies.append((self._adapter_policy_specificity(item), {"disabled": True}))
                continue
            typed_policy = self._resolve_typed_policy(item, chat_type)
            if typed_policy is not None:
                matched_policies.append((self._adapter_policy_specificity(item), typed_policy))
        return [policy for _, policy in sorted(matched_policies, key=lambda pair: pair[0], reverse=True)]

    def _adapter_policy_matches(self, policy: Mapping[str, Any], identity: AdapterIdentity) -> bool:
        adapter_id = str(policy.get("adapter_id") or "").strip()
        plugin_id = str(policy.get("plugin_id") or "").strip()
        gateway_name = str(policy.get("gateway_name") or "").strip()
        platform = str(policy.get("platform") or "").strip()
        account_id = str(policy.get("account_id") or "").strip()
        scope = str(policy.get("scope") or "").strip()

        if adapter_id and adapter_id != identity.adapter_id:
            return False
        if plugin_id and plugin_id != identity.plugin_id:
            return False
        if gateway_name and gateway_name != identity.gateway_name:
            return False
        if platform and platform != identity.platform:
            return False
        if account_id and account_id != (identity.account_id or ""):
            return False
        if scope and scope != (identity.scope or ""):
            return False
        return bool(adapter_id or plugin_id or gateway_name or platform or account_id or scope)

    @staticmethod
    def _adapter_policy_specificity(policy: Mapping[str, Any]) -> int:
        score = 0
        for key in ("adapter_id", "plugin_id", "gateway_name", "platform", "account_id", "scope"):
            if str(policy.get(key) or "").strip():
                score += 1
        return score

    @staticmethod
    def _resolve_typed_policy(raw_policy: Mapping[str, Any], chat_type: str) -> Optional[Dict[str, Any]]:
        chat_policy = raw_policy.get(chat_type)
        if not isinstance(chat_policy, Mapping):
            return None
        return dict(chat_policy)

    @staticmethod
    def _normalize_chat_type(chat_type: str) -> str:
        normalized_chat_type = str(chat_type or "").strip().lower()
        return normalized_chat_type if normalized_chat_type in _SUPPORTED_CHAT_TYPES else "private"

    @staticmethod
    def _normalize_id_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        normalized_ids: List[str] = []
        for item in value:
            normalized_item = str(item or "").strip()
            if normalized_item and normalized_item not in normalized_ids:
                normalized_ids.append(normalized_item)
        return normalized_ids

    @staticmethod
    def _append_unique_id(ids: List[str], target_id: str) -> List[str]:
        if target_id not in ids:
            ids.append(target_id)
        return ids

    @staticmethod
    def _identity_to_policy_match(identity: AdapterIdentity) -> Dict[str, str]:
        policy_match = {
            "adapter_id": identity.adapter_id,
            "plugin_id": identity.plugin_id,
            "gateway_name": identity.gateway_name,
            "platform": identity.platform,
            "account_id": identity.account_id or "",
            "scope": identity.scope or "",
        }
        return {key: str(value).strip() for key, value in policy_match.items() if str(value).strip()}

    def _ensure_adapter_tables(self, policy_doc: Any) -> AoT:
        adapters = policy_doc.get("adapters")
        if isinstance(adapters, AoT):
            return adapters

        next_adapters = tomlkit.aot()
        if isinstance(adapters, list):
            for item in adapters:
                if isinstance(item, dict):
                    table = tomlkit.table()
                    for key, value in item.items():
                        table[key] = value
                    next_adapters.append(table)
        policy_doc["adapters"] = next_adapters
        return next_adapters

    def _find_or_create_exact_adapter_policy(self, adapters: AoT, identity: AdapterIdentity) -> Table:
        policy_match = self._identity_to_policy_match(identity)
        for item in adapters:
            if not isinstance(item, Table):
                continue
            item_match = self._identity_to_policy_match(
                AdapterIdentity(
                    adapter_id=str(item.get("adapter_id") or ""),
                    plugin_id=str(item.get("plugin_id") or ""),
                    gateway_name=str(item.get("gateway_name") or ""),
                    platform=str(item.get("platform") or ""),
                    account_id=str(item.get("account_id") or "") or None,
                    scope=str(item.get("scope") or "") or None,
                )
            )
            if item_match == policy_match:
                return item

        adapter_policy = tomlkit.table()
        for key, value in policy_match.items():
            adapter_policy[key] = value
        adapters.append(adapter_policy)
        return adapter_policy

    @staticmethod
    def _ensure_chat_policy_table(adapter_policy: Table, chat_type: str) -> Table:
        chat_policy = adapter_policy.get(chat_type)
        if isinstance(chat_policy, Table):
            return chat_policy

        next_policy = tomlkit.table()
        adapter_policy[chat_type] = next_policy
        return next_policy

    @staticmethod
    def _set_or_remove_id_list(policy: Table, key: str, ids: List[str]) -> None:
        if ids:
            policy[key] = ids
        elif key in policy:
            del policy[key]

    @staticmethod
    def _prune_empty_ui_policy(adapter_policy: Table, chat_type: str) -> None:
        chat_policy = adapter_policy.get(chat_type)
        if not isinstance(chat_policy, Table):
            return
        if any(key in chat_policy for key in ("allow_ids", "deny_ids", "list_type", "ids", "disabled")):
            return
        del adapter_policy[chat_type]

    @staticmethod
    def _prune_empty_adapter_policy(adapters: AoT, adapter_policy: Table) -> None:
        identity_keys = {"adapter_id", "plugin_id", "gateway_name", "platform", "account_id", "scope"}
        if any(key not in identity_keys for key in adapter_policy):
            return
        try:
            adapters.remove(adapter_policy)
        except ValueError:
            return


_adapter_policy_manager: Optional[AdapterPolicyManager] = None


def get_adapter_policy_manager() -> AdapterPolicyManager:
    """返回全局适配器策略管理器。"""

    global _adapter_policy_manager
    if _adapter_policy_manager is None:
        _adapter_policy_manager = AdapterPolicyManager()
    return _adapter_policy_manager
