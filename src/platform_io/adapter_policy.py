"""统一适配器聊天名单策略。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import tomlkit
from tomlkit.items import AoT, Table

from src.common.paths import get_instance_root
from src.common.logger import get_logger
from src.core.local_operator import LOCAL_PLATFORM_BOT_IDS

logger = get_logger("platform_io.adapter_policy")

_PROJECT_ROOT = get_instance_root()
_DEFAULT_POLICY_PATH = _PROJECT_ROOT / "config" / "adapter_policy.toml"
_SUPPORTED_CHAT_TYPES = {"group", "private"}
_SUPPORTED_LIST_TYPES = {"whitelist", "blacklist"}
_SUPPORTED_OVERRIDE_ACTIONS = {"allow", "block", "inherit"}
_SUPPORTED_DEFAULT_ACTIONS = {"allow", "block"}
_IMPLICIT_DEFAULT_ACTION = "allow"


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

        # 内置聊天室与本地控制台不属于外部适配器，不能被适配器名单禁用。
        if identity.platform == "webui" or identity.platform in LOCAL_PLATFORM_BOT_IDS:
            return AdapterPolicyResult(
                allowed=True,
                configured=False,
                chat_type=normalized_chat_type,
                target_id=normalized_target_id,
                source="internal_platform",
                reason="internal_platform_exempt",
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
            allowed=_IMPLICIT_DEFAULT_ACTION == "allow",
            configured=False,
            chat_type=normalized_chat_type,
            target_id=normalized_target_id,
            source="implicit_default",
            reason=f"default_{_IMPLICIT_DEFAULT_ACTION}",
        )

    def get_default_actions(self) -> Dict[str, str]:
        """返回群聊和私聊当前生效的全局默认动作。"""

        policy_data = self._load_policy_data()
        defaults = policy_data.get("defaults")
        actions: Dict[str, str] = {}
        for chat_type in sorted(_SUPPORTED_CHAT_TYPES):
            action = _IMPLICIT_DEFAULT_ACTION
            if isinstance(defaults, Mapping):
                typed_policy = self._resolve_typed_policy(defaults, chat_type)
                if typed_policy is not None:
                    configured_action = str(typed_policy.get("default_action") or "").strip().lower()
                    if configured_action in _SUPPORTED_DEFAULT_ACTIONS:
                        action = configured_action
            actions[chat_type] = action
        return actions

    def get_adapter_policy(self, identity: AdapterIdentity) -> Dict[str, Any]:
        """返回一个适配器身份可由 WebUI 编辑的主程序规则。

        与 evaluate 使用同一匹配标准：在条目指定的身份字段与 identity 一致的
        现有条目中取 specificity 最高者，保证面板读到运行时真正生效的那条。
        """

        policy_match = self._identity_to_policy_match(identity)
        if not policy_match:
            raise ValueError("适配器身份不能为空")

        policy_data = self._load_policy_data()
        best_policy = self._find_display_adapter_policy(policy_data.get("adapters"), identity)

        result: Dict[str, Any] = {}
        for chat_type in sorted(_SUPPORTED_CHAT_TYPES):
            typed_policy = self._resolve_typed_policy(best_policy, chat_type) if best_policy is not None else None
            default_action = "inherit"
            allow_ids: List[str] = []
            deny_ids: List[str] = []
            if typed_policy is not None:
                configured_action = str(typed_policy.get("default_action") or "").strip().lower()
                if configured_action in _SUPPORTED_DEFAULT_ACTIONS:
                    default_action = configured_action
                allow_ids = self._normalize_id_list(typed_policy.get("allow_ids"))
                deny_ids = self._normalize_id_list(typed_policy.get("deny_ids"))
                # 旧版 list_type/ids 在首次创建分组时转换成等价的默认动作与覆盖名单。
                # 显式覆盖优先于旧名单，因此合并时必须保留原来的放行/拒绝顺序。
                if "list_type" in typed_policy or "ids" in typed_policy:
                    legacy_ids = self._normalize_id_list(typed_policy.get("ids"))
                    list_type = str(typed_policy.get("list_type") or "whitelist").strip().lower()
                    if list_type == "blacklist":
                        default_action = "block" if "*" in legacy_ids else "allow"
                        deny_ids.extend(item for item in legacy_ids if item != "*" and item not in allow_ids)
                    else:
                        default_action = "allow" if "*" in legacy_ids else "block"
                        allow_ids.extend(item for item in legacy_ids if item != "*" and item not in deny_ids)
                    deny_ids = list(dict.fromkeys(deny_ids))
                    allow_ids = list(dict.fromkeys(item for item in allow_ids if item not in deny_ids))
                if typed_policy.get("disabled", False):
                    default_action, allow_ids, deny_ids = "block", [], ["*"]
            result[chat_type] = {
                "default_action": default_action,
                "allow_ids": allow_ids,
                "deny_ids": deny_ids,
            }
        if best_policy is not None and "policy_groups" in best_policy:
            groups = best_policy["policy_groups"]
            active_group = best_policy.get("active_group")
            if not isinstance(groups, list) or not groups:
                raise ValueError("策略分组不能为空")
            result["active_group"] = active_group
            result["policy_groups"] = []
            for group in groups:
                group_policy = {"id": group["id"], "name": group["name"], **self._normalize_editor_policies(group)}
                # 聊天流详情页也能修改当前规则；读取时以实际生效内容更新当前组快照。
                if group_policy["id"] == active_group:
                    group_policy.update({chat_type: result[chat_type] for chat_type in _SUPPORTED_CHAT_TYPES})
                result["policy_groups"].append(group_policy)
            if not any(group["id"] == active_group for group in groups):
                raise ValueError("当前策略分组不存在")
        return result

    def has_adapter_policy_entry(self, identity: AdapterIdentity) -> bool:
        """判断当前身份是否命中已有规则条目（供面板区分「专属规则」与「默认继承」）。"""

        policy_data = self._load_policy_data()
        return self._find_display_adapter_policy(policy_data.get("adapters"), identity) is not None

    def list_plugin_entries(self, plugin_id: str) -> List[Dict[str, Any]]:
        """列出某插件名下全部规则条目的身份摘要。

        适配器换账号登录后旧账号条目不再生效但保留在配置中；
        面板据此展示各账号的规则归属，避免把旧条目误读为当前生效规则。
        """

        normalized_plugin_id = str(plugin_id or "").strip()
        if not normalized_plugin_id:
            return []
        policy_data = self._load_policy_data()
        adapters = policy_data.get("adapters")
        if not isinstance(adapters, list):
            return []
        entries: List[Dict[str, Any]] = []
        for item in adapters:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("plugin_id") or "").strip() != normalized_plugin_id:
                continue
            chat_summary: Dict[str, Any] = {}
            for chat_type in sorted(_SUPPORTED_CHAT_TYPES):
                typed_policy = self._resolve_typed_policy(item, chat_type)
                if typed_policy is None:
                    continue
                chat_summary[chat_type] = {
                    "default_action": str(typed_policy.get("default_action") or "inherit"),
                    "allow_ids": self._normalize_id_list(typed_policy.get("allow_ids")),
                    "deny_ids": self._normalize_id_list(typed_policy.get("deny_ids")),
                }
            entries.append(
                {
                    "adapter_id": str(item.get("adapter_id") or ""),
                    "account_id": str(item.get("account_id") or ""),
                    "platform": str(item.get("platform") or ""),
                    "gateway_name": str(item.get("gateway_name") or ""),
                    "scope": str(item.get("scope") or ""),
                    "rules": chat_summary,
                }
            )
        return entries

    def set_adapter_policy(
        self,
        identity: AdapterIdentity,
        policies: Mapping[str, Mapping[str, Any]],
        *,
        policy_groups: Optional[List[Dict[str, Any]]] = None,
        active_group: Optional[str] = None,
    ) -> None:
        """原子更新一个精确适配器身份的群聊与私聊规则。"""

        if not self._identity_to_policy_match(identity):
            raise ValueError("适配器身份不能为空")

        normalized_policies = self._normalize_editor_policies(policies)
        normalized_groups: Optional[List[Dict[str, Any]]] = None
        if policy_groups is not None:
            if not policy_groups:
                raise ValueError("至少保留一个策略分组")
            normalized_groups = []
            group_ids = set()
            group_names = set()
            for group in policy_groups:
                group_id = str(group.get("id", "")).strip()
                name = str(group.get("name", "")).strip()
                if not group_id or not name:
                    raise ValueError("分组 ID 和名称不能为空")
                if group_id in group_ids or name in group_names:
                    raise ValueError("分组 ID 和名称不能重复")
                group_ids.add(group_id)
                group_names.add(name)
                normalized_groups.append({"id": group_id, "name": name, **self._normalize_editor_policies(group)})
            if active_group not in group_ids:
                raise ValueError("当前策略分组不存在")
            selected = next(group for group in normalized_groups if group["id"] == active_group)
            if any(selected[chat_type] != normalized_policies[chat_type] for chat_type in _SUPPORTED_CHAT_TYPES):
                raise ValueError("当前分组内容与生效规则不一致")
        elif active_group is not None:
            raise ValueError("切换分组时必须提供策略分组")

        policy_doc = self._load_policy_doc()
        adapters = self._ensure_adapter_tables(policy_doc)
        adapter_policy = self._find_or_create_adapter_policy(adapters, identity)
        if normalized_groups is not None:
            adapter_policy["policy_groups"] = normalized_groups
            adapter_policy["active_group"] = active_group
        for chat_type, normalized_policy in normalized_policies.items():
            chat_policy = self._ensure_chat_policy_table(adapter_policy, chat_type)
            if normalized_groups is not None:
                # 分组保存使用完整的新规则，清除旧格式字段，避免旧名单继续影响新组。
                for key in ("list_type", "ids", "disabled"):
                    if key in chat_policy:
                        del chat_policy[key]
            default_action = normalized_policy["default_action"]
            if default_action == "inherit":
                if "default_action" in chat_policy:
                    del chat_policy["default_action"]
            else:
                chat_policy["default_action"] = default_action
            self._set_or_remove_id_list(chat_policy, "allow_ids", normalized_policy["allow_ids"])
            self._set_or_remove_id_list(chat_policy, "deny_ids", normalized_policy["deny_ids"])
            self._prune_empty_ui_policy(adapter_policy, chat_type)

        self._prune_empty_adapter_policy(adapters, adapter_policy)
        self._sync_active_group(adapter_policy)
        self._write_policy_doc(policy_doc)

    def _normalize_editor_policies(self, policies: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
        """校验并规范化一组群聊、私聊编辑规则。"""

        normalized_policies: Dict[str, Dict[str, Any]] = {}
        for chat_type in sorted(_SUPPORTED_CHAT_TYPES):
            raw_policy = policies.get(chat_type)
            if not isinstance(raw_policy, Mapping):
                raise ValueError(f"缺少 {chat_type} 放行规则")
            default_action = str(raw_policy.get("default_action") or "inherit").strip().lower()
            if default_action not in _SUPPORTED_OVERRIDE_ACTIONS:
                raise ValueError("default_action 必须是 allow、block 或 inherit")
            allow_ids = self._normalize_id_list(raw_policy.get("allow_ids"))
            deny_ids = self._normalize_id_list(raw_policy.get("deny_ids"))
            conflicting_ids = sorted(set(allow_ids).intersection(deny_ids))
            if conflicting_ids:
                raise ValueError(f"同一 ID 不能同时放行和拒绝: {', '.join(conflicting_ids)}")
            normalized_policies[chat_type] = {
                "default_action": default_action,
                "allow_ids": allow_ids,
                "deny_ids": deny_ids,
            }

        return normalized_policies

    def set_default_action(self, chat_type: str, action: str) -> None:
        """设置某类聊天在没有命中具体规则时的全局默认动作。"""

        normalized_chat_type = self._normalize_chat_type(chat_type)
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in _SUPPORTED_DEFAULT_ACTIONS:
            raise ValueError("action 必须是 allow 或 block")

        policy_doc = self._load_policy_doc()
        defaults = policy_doc.get("defaults")
        if not isinstance(defaults, Table):
            defaults = tomlkit.table()
            policy_doc["defaults"] = defaults
        chat_policy = self._ensure_chat_policy_table(defaults, normalized_chat_type)
        chat_policy["default_action"] = normalized_action
        self._write_policy_doc(policy_doc)

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
        adapter_policy = self._find_or_create_adapter_policy(adapters, identity)
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
        self._sync_active_group(adapter_policy)
        self._policy_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_policy_doc(policy_doc)

    def remove_chat_overrides(
        self,
        *,
        chat_type: str,
        target_id: str,
        platform: str,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> int:
        """删除指定聊天目标在匹配适配器策略中的显式覆盖规则。"""

        normalized_chat_type = self._normalize_chat_type(chat_type)
        normalized_target_id = str(target_id or "").strip()
        normalized_platform = str(platform or "").strip()
        normalized_account_id = str(account_id or "").strip()
        normalized_scope = str(scope or "").strip()
        if not normalized_target_id:
            raise ValueError("target_id 不能为空")
        if not normalized_platform:
            raise ValueError("platform 不能为空")

        policy_doc = self._load_policy_doc()
        adapters = policy_doc.get("adapters")
        if not isinstance(adapters, AoT):
            return 0

        removed_count = 0
        for adapter_policy in list(adapters):
            if not isinstance(adapter_policy, Table):
                continue
            if not self._policy_matches_chat_scope(
                adapter_policy,
                platform=normalized_platform,
                account_id=normalized_account_id,
                scope=normalized_scope,
            ):
                continue

            # 同时清理未启用分组，避免删除聊天流后切换策略又恢复其旧覆盖规则。
            policy_sources = [adapter_policy]
            groups = adapter_policy.get("policy_groups", [])
            policy_sources.extend(group for group in groups if group["id"] != adapter_policy.get("active_group"))
            for policy_source in policy_sources:
                chat_policy = policy_source.get(normalized_chat_type)
                if not isinstance(chat_policy, Table):
                    continue
                for key in ("allow_ids", "deny_ids"):
                    ids = self._normalize_id_list(chat_policy.get(key))
                    next_ids = [item for item in ids if item != normalized_target_id]
                    if len(next_ids) == len(ids):
                        continue
                    removed_count += 1
                    if policy_source is adapter_policy:
                        self._set_or_remove_id_list(chat_policy, key, next_ids)
                    else:
                        chat_policy[key] = next_ids

            self._prune_empty_ui_policy(adapter_policy, normalized_chat_type)
            self._prune_empty_adapter_policy(adapters, adapter_policy)
            self._sync_active_group(adapter_policy)

        if removed_count:
            self._write_policy_doc(policy_doc)
        return removed_count

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
            default_action = str(policy.get("default_action") or "").strip().lower()
            if default_action not in _SUPPORTED_DEFAULT_ACTIONS:
                return None
            return AdapterPolicyResult(
                allowed=default_action == "allow",
                configured=True,
                chat_type=chat_type,
                target_id=target_id,
                list_type="default",
                source=source,
                reason=f"default_{default_action}",
            )

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

        with self._policy_path.open("r", encoding="utf-8") as policy_file:
            return tomlkit.load(policy_file).unwrap()

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
        if normalized_chat_type not in _SUPPORTED_CHAT_TYPES:
            raise ValueError(f"不支持的聊天类型: {chat_type}")
        return normalized_chat_type

    def _write_policy_doc(self, policy_doc: Any) -> None:
        """原子写入策略文档，避免进程中断留下半份配置。"""

        self._policy_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._policy_path.with_suffix(f"{self._policy_path.suffix}.tmp")
        with temporary_path.open("w", encoding="utf-8") as policy_file:
            policy_file.write(tomlkit.dumps(policy_doc))
        temporary_path.replace(self._policy_path)

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

    @staticmethod
    def _policy_matches_chat_scope(
        policy: Mapping[str, Any],
        *,
        platform: str,
        account_id: str,
        scope: str,
    ) -> bool:
        """判断适配器策略是否可能作用于指定聊天路由。"""

        policy_platform = str(policy.get("platform") or "").strip()
        policy_account_id = str(policy.get("account_id") or "").strip()
        policy_scope = str(policy.get("scope") or "").strip()
        if policy_platform and policy_platform != platform:
            return False
        if policy_account_id and account_id and policy_account_id != account_id:
            return False
        if policy_scope and scope and policy_scope != scope:
            return False
        return True

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

    def _find_display_adapter_policy(self, adapters: Any, identity: AdapterIdentity) -> Optional[Mapping[str, Any]]:
        """为 WebUI 读取挑选应展示的规则条目。

        先按与 evaluate 相同的匹配标准（条目指定的身份字段与 identity 一致）
        取 specificity 最高的条目；身份仅含 plugin_id（适配器未运行、无法解析
        完整身份）时，退回该插件名下 specificity 最高的条目，避免面板读到与
        运行时脱靶的空模板。
        """

        if not isinstance(adapters, list):
            return None
        matched = [
            item
            for item in adapters
            if isinstance(item, Mapping) and self._adapter_policy_matches(item, identity)
        ]
        if not matched and set(self._identity_to_policy_match(identity)) == {"plugin_id"}:
            plugin_id = str(identity.plugin_id or "").strip()
            matched = [
                item
                for item in adapters
                if isinstance(item, Mapping) and str(item.get("plugin_id") or "").strip() == plugin_id
            ]
        if not matched:
            return None
        return max(matched, key=self._adapter_policy_specificity)

    def _find_or_create_adapter_policy(self, adapters: AoT, identity: AdapterIdentity) -> Table:
        """按运行时求值同一标准定位规则条目，找不到时按身份新建。

        写入必须落在求值会命中的同一条目上，否则会写出一条被更高
        specificity 条目永久遮蔽、保存成功却不生效的死规则。
        """

        matched = [
            item
            for item in adapters
            if isinstance(item, Table) and self._adapter_policy_matches(item, identity)
        ]
        if matched:
            return max(matched, key=self._adapter_policy_specificity)

        adapter_policy = tomlkit.table()
        for key, value in self._identity_to_policy_match(identity).items():
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
    def _sync_active_group(adapter_policy: Table) -> None:
        """保持当前分组快照与聊天流覆盖接口写入的实际规则一致。"""

        groups = adapter_policy.get("policy_groups")
        if groups is None:
            return
        active_group = adapter_policy.get("active_group")
        selected = next((group for group in groups if group["id"] == active_group), None)
        if selected is None:
            raise ValueError("当前策略分组不存在")
        for chat_type in _SUPPORTED_CHAT_TYPES:
            policy = adapter_policy.get(chat_type, {})
            selected[chat_type] = {
                "default_action": policy.get("default_action", "inherit"),
                "allow_ids": list(policy.get("allow_ids", [])),
                "deny_ids": list(policy.get("deny_ids", [])),
            }

    @staticmethod
    def _prune_empty_ui_policy(adapter_policy: Table, chat_type: str) -> None:
        chat_policy = adapter_policy.get(chat_type)
        if not isinstance(chat_policy, Table):
            return
        if any(
            key in chat_policy for key in ("allow_ids", "deny_ids", "list_type", "ids", "disabled", "default_action")
        ):
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
