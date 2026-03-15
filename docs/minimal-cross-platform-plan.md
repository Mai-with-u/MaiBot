# Minimal Cross-Platform Runtime Plan

## Goal

Make MaiBot runtime identity handling platform-aware with the smallest safe change set.

Success means:

- One canonical `is_bot_self(platform, user_id)` with no argument-order traps.
- Platform-correct outbound bot sender IDs for configured platforms.
- Platform-aware bot filtering in stored message queries.
- No false assumption that QQ is the universal bot identity for unknown platforms.
- Existing QQ configuration and WebUI behavior preserved without regressions.

This pass does **not** try to clean every QQ-oriented string, thread platform through PFC internals, or touch user-visible formats.

## Out of Scope for This Plan

The following are explicitly deferred because they are not required for runtime correctness in this pass:

- **PFC platform propagation.** `ChatObserver._message_to_dict` currently omits platform from the serialized `user_info` dict; `observation_info.dict_to_session_message` reads `user_info_dict.get("platform", "")` which is always `""`. Fixing this cascades into `chat_observer.py`, `chat_states.py`, `observation_info.py`, and possibly `conversation.py`. Deferred to a follow-up plan.
- **PFC-internal identity checks.** Sites like `reply_checker.py:61`, `action_planner.py:157`, and `chat_observer.py:337` (inside `process_chat_history`, which has **no in-repo call sites**) operate on PFC serialized dicts where platform is always `""`. They cannot be migrated to `is_bot_self(platform, user_id)` until PFC serialization is fixed. Deferred.
- **PFC prompt platformization.** Replacing `QQ私聊`/`QQ聊天` in `action_planner.py`, `reply_generator.py`, `pfc.py`, and `emoji_plugin` requires threading platform through PFC prompt construction context. Deferred.
- MCP permission ID format changes (`qq:...` -> `{platform}:...`).
- Capability API default changes (`args.get("platform", "qq")` -> `"all_platforms"`).
- Dashboard/UI/config label cleanup.
- Config field renames (`enable_qq_tools`, `qq_api_base_url`, etc.).
- A new local WebUI bot identity constant (`WEBUI_BOT_USER_ID`).
- Historical database migration.

## Verified Current State

This plan is based on the checked-in code, not on assumptions from previous drafts.

### Bot-Self Logic

| Location | Signature | Status |
|----------|-----------|--------|
| `src/common/utils/system_utils.py:2` | `is_bot_self(user_id, platform)` | Stub; only matches literal `"bot_self"` + `"test_platform"` — never true in production |
| `src/chat/utils/utils.py:69` | `is_bot_self(platform, user_id)` | Real implementation; has dangerous QQ fallback at lines 112-113 |
| `src/person_info/person_info.py:247` | `_is_bot_self(self, platform, user_id)` | Duplicate logic with same QQ fallback |

Wrong-order call sites (8 total):
- `src/bw_learner/expression_learner.py` x3 (lines 158, 241, 301)
- `src/common/utils/utils_message.py` x4 (lines 370, 440, 476, 515)
- `src/webui/routers/chat/support.py` x1 (line 65)

Correct-order callers (verify only, do not modify):
- `src/chat/replyer/private_generator.py` (lines 680, 816)
- `src/chat/replyer/group_generator.py` (line 831)
- `src/chat/planner_actions/planner.py` (line 245)
- `src/memory_system/chat_history_summarizer.py` (line 368)

### PFC Serialization Gap (Known, Not Fixed Here)

`ChatObserver._message_to_dict()` (lines 30-45) serializes messages **without** platform in `user_info`:

```python
"user_info": {
    "user_id": message.user_id,
    "user_nickname": message.user_nickname,
    # NOTE: no "platform" key
},
```

Meanwhile, `observation_info.dict_to_session_message()` (line 30) reads:
```python
platform = user_info_dict.get("platform", "")  # always "" due to above gap
```

**Consequence:** PFC-internal identity checks that operate on these serialized dicts cannot use `is_bot_self(platform, user_id)` until this gap is closed. Those sites are excluded from this plan entirely.

### WebUI Compatibility Reality

- Local WebUI bot messages are stored with `platform="webui"` and `user_id=str(global_config.bot.qq_account)`.
- `is_bot_self` already treats `platform == "webui"` as comparing against `qq_account`.
- `support.py` contains additional heuristic fallbacks built around this sender shape.

This plan preserves this behavior. Introducing a new WebUI bot ID would require transition logic in both Python and SQL and is deferred.

### Sender Construction Sites

Outbound bot sender IDs are hard-coded to `global_config.bot.qq_account` in:
- `src/services/send_service.py:91` (has `target_stream.platform`)
- `src/chat/replyer/group_generator.py:1125` (has `self.chat_stream.platform`)
- `src/chat/replyer/private_generator.py:965` (has `self.chat_stream.platform`)
- `src/chat/brain_chat/PFC/message_sender.py:44` (has chat stream context)

All have platform available at runtime.

## Design Rules

1. Prefer compatibility over cleanliness.
2. Fix runtime identity and sender correctness only at sites where `platform` is already available.
3. Do not thread platform through PFC serialization or prompt construction in this pass.
4. Do not introduce new user-visible formats unless required for runtime correctness.
5. Do not key implementation on `ChatObserver.process_chat_history` (no in-repo call sites).
6. Preserve `bot.qq_account` config — no schema migration.

## Identity Matrix (This Plan)

| Runtime context | Canonical bot user ID |
|-----------------|-----------------------|
| `platform == "qq"` and `qq_account` configured | `str(global_config.bot.qq_account)` |
| `platform == "telegram"` | `platforms["tg"]` or `platforms["telegram"]` |
| `platform == "webui"` | `str(global_config.bot.qq_account)` (current storage reality) |
| Other configured adapter platforms | `platforms[platform]` |
| Unknown / unconfigured platform | no account; `is_bot_self` returns `False` + warning |

`qq_account in {None, "", 0, "0"}` means QQ bot identity is **not configured**. `get_bot_account("qq")` returns `""` and `is_bot_self("qq", any_id)` returns `False` in that case.

---

## Phase 0: Unify `is_bot_self`

### Objective

Make `src/chat/utils/utils.py::is_bot_self(platform, user_id)` the only real implementation, and remove the argument-order trap.

### Allowed Files

- `src/common/utils/system_utils.py`
- `src/chat/utils/utils.py`
- `src/person_info/person_info.py`
- `src/bw_learner/expression_learner.py`
- `src/common/utils/utils_message.py`
- `src/webui/routers/chat/support.py`
- tests

### Required Changes

> **ATOMICITY CONSTRAINT:** Steps 1 and 2 MUST be committed together. Do NOT commit step 2 without all step 1 fixes. A partial commit creates a silent semantic inversion: callers passing `(platform, user_id)` to a function that still interprets them as `(user_id, platform)`. The stub always returns `False` in production so the bug would be silent — no crash, just wrong results.

1. Convert `src/common/utils/system_utils.py::is_bot_self` into a thin wrapper with signature `(platform, user_id)` that delegates to `src.chat.utils.utils.is_bot_self`. **Use a method-local import** to avoid circular dependency (`system_utils -> chat.utils.utils -> message -> mai_message_data_model -> utils_message -> system_utils`).
2. Fix all 8 wrong-order call sites — swap arguments to `(platform, user_id)`:

   | File | Line | Current Call | Required Fix |
   |------|------|--------------|--------------|
   | `expression_learner.py` | 158 | `is_bot_self(msg...user_id, msg.platform)` | swap to `(msg.platform, msg...user_id)` |
   | `expression_learner.py` | 241 | `is_bot_self(target_msg...user_id, target_msg.platform)` | swap |
   | `expression_learner.py` | 301 | `is_bot_self(current_msg...user_id, current_msg.platform)` | swap |
   | `utils_message.py` | 370 | `is_bot_self(user_id, platform)` | swap |
   | `utils_message.py` | 440 | `is_bot_self(user_id, platform)` | swap |
   | `utils_message.py` | 476 | `is_bot_self(user_id, platform)` | swap |
   | `utils_message.py` | 515 | `is_bot_self(user_id, platform)` | swap |
   | `support.py` | 65 | `is_bot_self(user_id, msg.platform)` | swap + change import source to `src.chat.utils.utils` |

3. Replace `Person._is_bot_self` body with delegation to the canonical function. Use a **method-local import** inside `person_info.py` to avoid a new import cycle.
4. Do not change identity semantics yet (QQ fallback deletion is Phase 1).
5. Update existing test mocks: `pytests/utils_test/message_utils_test.py:203` defines `dummy_is_bot_self(user_id, platform)` with the **old** argument order. After swapping call sites, this mock must be updated to `dummy_is_bot_self(platform, user_id)` and the injection at line 235 must match.

### Acceptance Criteria

- Only one real implementation remains: `src/chat/utils/utils.py::is_bot_self`.
- No runtime caller passes `(user_id, platform)`.
- `system_utils.is_bot_self` is a compatibility wrapper only, using a **local import** (not top-level).
- `Person._is_bot_self` delegates instead of duplicating logic.
- Tests cover QQ, Telegram, WebUI, and unknown platform cases.
- Existing test mocks updated to match new argument order.

---

## Phase 1: Identity Resolution + Sender Construction + Trivial Prompt Cleanup

> **REVIEW NOTE (Claude + Codex joint review, 2026-03-15):** Original plan had Phase 1 (identity resolution) and Phase 2 (sender construction) as separate phases. Joint review identified a **critical regression window**: Phase 1 deletes the QQ fallback (lines 112-113), but sender construction still hard-codes `qq_account` until Phase 2. Between the two phases, `is_bot_self("telegram", qq_account)` returns `False` for existing stored bot messages, breaking `filter_bot`, stats, and identity checks on non-QQ platforms. **Resolution: merge into a single atomic phase.**

### Objective

Create one canonical place to resolve bot accounts for any runtime platform, delete the dangerous unknown-platform QQ fallback, make `filter_bot` platform-aware, fix sender construction, and clean up trivial prompt wording. Only touch sites where `platform` is already available at runtime — do **not** touch PFC internals.

> **ATOMICITY CONSTRAINT:** The QQ fallback deletion (lines 112-113) and sender construction fixes (4 sites) MUST be in the same commit. Deleting the fallback without fixing senders creates a regression for any non-QQ platform where bot messages are stored with `qq_account`.

### Allowed Files

- `src/chat/utils/utils.py`
- `src/chat/planner_actions/planner.py`
- `src/chat/utils/statistic.py`
- `src/common/message_repository.py`
- `src/webui/routers/chat/support.py`
- `src/services/send_service.py`
- `src/chat/replyer/group_generator.py`
- `src/chat/replyer/private_generator.py`
- `src/chat/brain_chat/PFC/message_sender.py`
- `src/person_info/person_info.py`
- tests

### Required Helper Additions (in `src/chat/utils/utils.py`)

```python
def get_bot_account(platform: str) -> str
def get_all_bot_accounts() -> dict[str, str]
```

- `get_bot_account` **replaces** `get_current_platform_account`. After Phase 1, `get_current_platform_account` must not exist. Update all its callers (including `is_mentioned_bot_in_message` at line 127).
- `get_all_bot_accounts()` returns only configured, non-empty runtime identities. When `qq_account` is configured and non-zero, include:
  - `"qq": str(qq_account)`
  - `"webui": str(qq_account)` (current storage reality)
  - plus any configured external platform accounts from `bot.platforms`
- `parse_platform_accounts` remains as internal helper.
- Normalize platform strings: apply `.lower().strip()` in `is_bot_self`, `get_bot_account`, **and `parse_platform_accounts`** (on parsed keys). This ensures config values like `"TG:123"` are stored as key `"tg"` and matched correctly.

No classes, registries, managers, or generalized identity frameworks.

### Required Identity Semantics Update

Update `is_bot_self` to follow the Identity Matrix. Key change:

**Delete lines 112-113** (the `return user_id_str == qq_account` fallback for unknown platforms). Replace with `return False` + `logger.warning(...)`. This is the single most important line deletion in the entire plan.

### Direct Comparison Replacements

| File | Line | Current Code | Action |
|------|------|-------------|--------|
| `utils.py` | 88 | inside `is_bot_self` | **KEEP** — internal to canonical function |
| `utils.py` | 124 | inside `is_mentioned_bot_in_message` | **UPDATE** — use `get_bot_account(platform)` |
| `planner.py` | 125 | `platform = message.platform or "qq"` | **UPDATE** — see below |
| `planner.py` | 138 | `if user_id == global_config.bot.qq_account:` | **REPLACE** with `is_bot_self(platform, str(user_id))` |
| `statistic.py` | 2111 | `str(global_config.bot.qq_account)` | **REPLACE** — see below |
| `message_repository.py` | 166 | `Messages.user_id != global_config.bot.qq_account` | **UPDATE** — see below |
| `support.py` | 68 | `user_id == str(global_config.bot.qq_account)` | **REPLACE** with `is_bot_self(msg.platform, user_id)` |

### Excluded from This Phase (PFC-Internal, Platform Unavailable)

| File | Line | Current Code | Why excluded |
|------|------|-------------|-------------|
| `chat_observer.py` | 337 | `user_info.user_id == global_config.bot.qq_account` | `process_chat_history` has no call sites; PFC dict lacks platform |
| `reply_checker.py` | 61 | `str(user_info.user_id) == str(global_config.bot.qq_account)` | Operates on PFC dicts without platform |
| `action_planner.py` | 157 | `bot_id = str(global_config.bot.qq_account)` | PFC-internal; platform not propagated |

### Sender Construction Sites (Now Part of This Phase)

> These were originally deferred to a separate Phase 2. After joint review, they are merged into Phase 1 to prevent a regression window. See Sender Construction Changes section below for the required fixes.

| File | Line | Current Code | Action |
|------|------|-------------|--------|
| `send_service.py` | 91 | `user_id=str(global_config.bot.qq_account)` | **REPLACE** with `get_bot_account(target_stream.platform)` |
| `group_generator.py` | 1125 | `user_id=str(global_config.bot.qq_account)` | **REPLACE** with `get_bot_account(self.chat_stream.platform)` |
| `private_generator.py` | 965 | `user_id=str(global_config.bot.qq_account)` | **REPLACE** with `get_bot_account(self.chat_stream.platform)` |
| `message_sender.py` | 44 | `user_id=global_config.bot.qq_account` | **REPLACE** with `get_bot_account(platform)` from chat stream context |

### Detailed Instructions

#### `planner.py:125` — Empty Platform Handling

```python
# BEFORE:
platform = message.platform or "qq"

# AFTER:
platform = message.platform or ""
if not platform:
    logger.warning("planner: message has no platform set, bot-self detection will be skipped")
```

Instead of falsely assuming QQ, an empty platform means `is_bot_self("", user_id)` returns `False`. This is safer than falsely matching a QQ account that may belong to a real user on another platform.

#### `statistic.py:2109-2151`

```python
# BEFORE (lines 2109-2114):
bot_qq_account = (
    str(global_config.bot.qq_account)
    if hasattr(global_config, "bot") and hasattr(global_config.bot, "qq_account")
    else ""
)

# AFTER:
from src.chat.utils.utils import is_bot_self
```

```python
# BEFORE (line 2151):
if bot_qq_account and message.user_id == bot_qq_account:
    total_replies[interval_index] += 1

# AFTER:
if is_bot_self(message.platform or "", message.user_id or ""):
    total_replies[interval_index] += 1
```

Place the import locally to avoid circular import risk. `Messages` records have a `.platform` field so this handles all platforms.

#### `support.py:62-70` — WebUI Bot Detection

```python
# CURRENT (after Phase 0 arg-order fix):
is_bot = is_bot_self(msg.platform, user_id)

if not is_bot and group_id and group_id.startswith(VIRTUAL_GROUP_ID_PREFIX):
    is_bot = user_id == str(global_config.bot.qq_account)  # QQ-only fallback
elif not is_bot:
    is_bot = not user_id.startswith(WEBUI_USER_ID_PREFIX)  # reverse heuristic
```

After Phase 1, `is_bot_self` correctly handles all current platform cases:
- WebUI local chat: `is_bot_self("webui", str(qq_account))` returns `True`.
- Virtual-group mode: `msg.platform` carries the simulated platform (e.g., `"qq"`), so `is_bot_self` uses the correct platform account.

The entire if/elif block becomes dead code.

**Target code:**
```python
is_bot = is_bot_self(msg.platform, user_id)
```

Delete the if/elif fallback block. After Phase 1, `is_bot_self` handles all current platform cases correctly, making this block dead code. If a smoke test disproves this assumption, stop and report instead of keeping the fallback.

#### `filter_bot` in `message_repository.py:166`

Current code has a type mismatch: `Messages.user_id` is string, `global_config.bot.qq_account` is `int`.

**NOTE:** `message_repository.py` does not currently import `or_`, `and_`, or `not_` from SQLAlchemy (only `func` at line 7). These imports must be added.

```python
# AFTER:
if filter_bot:
    from src.chat.utils.utils import get_all_bot_accounts
    bot_accounts = get_all_bot_accounts()
    if bot_accounts:
        from sqlalchemy import or_, and_, not_
        bot_identity_predicate = or_(
            *[
                and_(Messages.platform == plat, Messages.user_id == acct)
                for plat, acct in bot_accounts.items()
            ]
        )
        conditions.append(not_(bot_identity_predicate))
    # If no bot accounts configured, skip bot filtering (no-op).
```

> **EDGE CASE:** `or_()` with zero arguments raises `TypeError` in SQLAlchemy. The `if bot_accounts` guard is mandatory.

**WebUI compatibility:** `get_all_bot_accounts()` includes `{"webui": str(qq_account), ...}`. Since WebUI bot messages are stored with `platform="webui"` and `user_id=str(qq_account)`, the pair correctly matches them. No regression.

### Acceptance Criteria

- `is_bot_self` no longer falls back to QQ for unknown platforms (lines 112-113 deleted).
- `get_bot_account(platform)` exists and resolves configured platform accounts.
- `get_current_platform_account` is deleted — no callers remain.
- `get_bot_account("qq") == ""` when `qq_account` is unconfigured.
- `is_bot_self("webui", str(qq_account))` returns `True` (preserves current behavior).
- `filter_bot=True` excludes configured bot identities by `(platform, user_id)` pairs.
- `statistic.py` uses `is_bot_self` for reply counting.
- `support.py` uses `is_bot_self` without reverse heuristic fallbacks.
- PFC-internal sites are **not changed**.

### Sender Construction Changes (formerly Phase 2)

Replace hard-coded `global_config.bot.qq_account` sender IDs with `get_bot_account(platform)`:

| File | Line | Platform Source |
|------|------|----------------|
| `send_service.py` | 91 | `target_stream.platform` |
| `group_generator.py` | 1125 | `self.chat_stream.platform` |
| `private_generator.py` | 965 | `self.chat_stream.platform` |
| `message_sender.py` | 44 | available from chat stream context |

For this pass:
- Local WebUI resolves to `str(qq_account)` via `get_bot_account("webui")` — same value as today, no regression.
- WebUI virtual-group sessions carry the simulated platform, so `get_bot_account(self.chat_stream.platform)` produces the correct platform-specific account.
- Do **not** introduce `WEBUI_BOT_USER_ID`.

### Trivial Prompt Cleanup

These sites use QQ-specific wording but need only text replacement, no runtime platform info:

| File | Line(s) | Current Text | Change |
|------|---------|-------------|--------|
| `person_info.py` | 734 | `用户的qq昵称是` | `用户的昵称是` |
| `person_info.py` | 735 | `用户的qq群昵称名是` | `用户的群昵称名是` |
| `person_info.py` | 737 | `用户的qq头像是` | `用户的头像是` |
| `person_info.py` | 742 | `qq昵称或群昵称` (multiple) | Remove `qq` prefix, keep `昵称` and `群昵称` |

### Prompt Sites Deferred (Require Platform Threading)

| File | Line(s) | Text | Why deferred |
|------|---------|------|-------------|
| `action_planner.py` | 22, 55, 89 | `QQ私聊` / `QQ 私聊` | Needs platform in PFC prompt context |
| `reply_generator.py` | 18, 43, 68 | `QQ私聊` | Same |
| `pfc.py` | 125, 254 | `QQ聊天` | Same |
| `emoji_plugin/plugin.py` | 66 | `你正在进行QQ聊天` | Needs plugin message context |

### Known Behavior Changes

| Site | Current Behavior | New Behavior | Risk |
|------|-----------------|--------------|------|
| `planner.py:138` | `user_id == global_config.bot.qq_account` compares string to int — always `False` (existing bug) | `is_bot_self(platform, str(user_id))` — correctly identifies bot | **Fixes existing bug**, but changes behavior. May cause bot name to render as `{nickname}(你)` where it previously showed the raw user reference. |

### Acceptance Criteria

- `is_bot_self` no longer falls back to QQ for unknown platforms (lines 112-113 deleted).
- `get_bot_account(platform)` exists and resolves configured platform accounts.
- `get_current_platform_account` is deleted — no callers remain.
- `get_bot_account("qq") == ""` when `qq_account` is unconfigured.
- `is_bot_self("webui", str(qq_account))` returns `True` (preserves current behavior).
- `filter_bot=True` excludes configured bot identities by `(platform, user_id)` pairs.
- `statistic.py` uses `is_bot_self` for reply counting.
- `support.py` uses `is_bot_self` without reverse heuristic fallbacks.
- PFC-internal sites are **not changed**.
- Outbound QQ sender IDs still use `qq_account`.
- Outbound Telegram and other configured platform sender IDs use that platform's configured account.
- Outbound local WebUI sender IDs remain `str(qq_account)` — compatible with current stored history.
- No sender construction path hard-codes `global_config.bot.qq_account`.
- `person_info.py` nickname/avatar prompts use neutral wording (`昵称`, not `qq昵称`).
- No PFC-internal prompt files are modified.

---

## Implementation Order

1. **Phase 0** — unify `is_bot_self` (signature + argument order only, no semantic changes)
2. **Phase 1** — identity resolution, QQ fallback deletion, `filter_bot`, direct comparison cleanup, sender construction, trivial prompt cleanup

Do not start Phase 1 until Phase 0 is committed and verified.

---

## Hard Execution Contract

This section is written for code-generation agents. If any rule here conflicts with a generic agent preference, **this section wins**.

### Required Workflow

1. Read every file that will be modified in full before editing. Read at least 30 lines above and below any target line.
2. Check `git status --short` before the first edit of a phase. If a file **in the current phase allowlist** has unrelated user changes, stop and report. Unrelated changes in files outside the allowlist (e.g., `docs/`) do not block the phase.
3. One phase per commit or one clearly isolated change batch.
4. Run the search checklist after each phase.
5. Produce a short phase report before moving on (files changed, searches before/after, tests run, residual hits).

### Allowed to Do

- Read any file needed to confirm current behavior.
- Edit only files in the **current phase allowlist**, plus narrowly scoped tests.
- Add local imports or compatibility wrappers to avoid circular imports.
- Fix additional call sites of the **same semantic bug pattern** if within the phase allowlist.
- Stop after completing the current phase.

### Forbidden to Do

- Do not edit files outside the current phase allowlist.
- Do not bundle Phase 0 and Phase 1 into one commit.
- Do not touch PFC serialization files (`chat_observer.py._message_to_dict`, `chat_states.py`, `observation_info.py`, `conversation.py`).
- Do not introduce `WEBUI_BOT_USER_ID` or any new WebUI bot identity constant.
- Do not change capability query API defaults (`args.get("platform", "qq")`).
- Do not change MCP permission context ID format.
- Do not change data models, adapter protocol, or config schema.
- Do not perform repo-wide formatting, lint churn, comment churn, or opportunistic refactors.
- Do not use destructive git commands (`git checkout -- .`, `git reset --hard`, etc.).
- Do not guess platform values — if `platform` is empty, treat as unknown, never substitute `"qq"`.
- Do not treat `qq_account == 0` as a real QQ bot identity.
- Do not create `get_bot_account` alongside `get_current_platform_account` — the old function must be deleted.
- Do not implement `filter_bot` as `Messages.user_id NOT IN (...)` — use `(platform, user_id)` pair matching.

### Must Stop and Report

Stop immediately and report instead of improvising if:

- a required fix needs a file outside the current phase allowlist
- a file **in the current phase allowlist** already contains unrelated user changes
- the checked-in code no longer matches the plan's assumed signatures or control flow
- a circular import cannot be avoided with a local import or wrapper
- a search reveals a new runtime platform-coupling category not covered by the current phase
- tests fail twice and the root cause is still unclear

When stopping, name: the exact file(s), the blocking mismatch, why it is outside scope, and the smallest safe next step.

### Per-Phase File Allowlist

| Phase | Allowed files |
|-------|---------------|
| Phase 0 | `src/common/utils/system_utils.py`, `src/chat/utils/utils.py`, `src/person_info/person_info.py`, `src/bw_learner/expression_learner.py`, `src/common/utils/utils_message.py`, `src/webui/routers/chat/support.py`, tests (including `pytests/utils_test/message_utils_test.py`) |
| Phase 1 | `src/chat/utils/utils.py`, `src/chat/planner_actions/planner.py`, `src/chat/utils/statistic.py`, `src/common/message_repository.py`, `src/webui/routers/chat/support.py`, `src/services/send_service.py`, `src/chat/replyer/group_generator.py`, `src/chat/replyer/private_generator.py`, `src/chat/brain_chat/PFC/message_sender.py`, `src/person_info/person_info.py`, tests |

### INVALID OUTPUT EXAMPLES

Any of the following means the implementation has drifted and must be rejected:

- Editing PFC serialization files (`chat_observer.py._message_to_dict`, `observation_info.py`, `chat_states.py`)
- Introducing `WEBUI_BOT_USER_ID` constant
- Changing WebUI sender storage to anything other than `str(qq_account)`
- `get_current_platform_account` still existing after Phase 1
- `is_bot_self` returning `True` for unknown platforms (lines 112-113 still present)
- Implementing `filter_bot` as `Messages.user_id.notin_(...)`
- Changing `args.get("platform", "qq")` in capability query APIs
- Modifying PFC prompt strings (`QQ私聊`, `QQ聊天`) in this plan
- Editing Phase 1 files while Phase 0 is incomplete
- Introducing `PlatformRegistry`, `BotIdentityManager`, or similar abstractions
- Deleting the QQ fallback (lines 112-113) without simultaneously fixing all 4 sender construction sites

---

## Search Checklist

Run before and after each phase:

```bash
rg -n "def is_bot_self|_is_bot_self|is_bot_self\(" src
rg -n "global_config\.bot\.qq_account" src
rg -n "get_current_platform_account|get_bot_account|get_all_bot_accounts" src
rg -n 'filter_bot|filter_mai' src
rg -n 'qq昵称|qq群昵称|qq头像' src
```

### Expected Residual Hits After All Phases

| Pattern | File | Why it remains |
|---------|------|---------------|
| `global_config.bot.qq_account` | `src/chat/utils/utils.py` | Internal to `is_bot_self` and `get_bot_account` |
| `global_config.bot.qq_account` | `src/chat/brain_chat/PFC/chat_observer.py` | PFC-internal, `process_chat_history` has no call sites — deferred |
| `global_config.bot.qq_account` | `src/chat/brain_chat/PFC/reply_checker.py` | PFC-internal, platform not available — deferred |
| `global_config.bot.qq_account` | `src/chat/brain_chat/PFC/action_planner.py` | PFC-internal, platform not available — deferred |
| `QQ私聊` / `QQ聊天` | PFC prompt files | Requires platform threading into PFC context — deferred |
| `进行QQ聊天` | `emoji_plugin/plugin.py` | Requires plugin context investigation — deferred |
| `platform or "qq"` | capability query APIs | Default semantics change — deferred |

---

## Known Issues Identified During Review

> Added during Claude + Codex joint review (2026-03-15). These are pre-existing issues or edge cases discovered during plan review that do not block this plan but must be tracked.

### 1. WebUI Virtual-Group Session ID Mismatch (Pre-existing Bug)

`ChatHistoryManager._resolve_session_id()` at `support.py:84` always hashes with `WEBUI_CHAT_PLATFORM` ("webui"), but virtual-identity messages are created with the simulated platform (e.g., "telegram") at `support.py:341` and stored under `SessionUtils.calculate_session_id(message.platform, ...)` at `bot.py:316-317`. These produce different session IDs. This means history retrieval for virtual-group sessions may not find the stored messages. **This is a pre-existing bug, not introduced by this plan.** The plan's `support.py` heuristic deletion should include a smoke test of virtual-group mode to confirm behavior.

### 2. `filter_bot` Legacy Data Contingency

The new `(platform, user_id)` pair matching in `filter_bot` will not catch historical rows where `platform` is empty or inconsistent. Current write paths reject empty platform at `chat_manager.py:128-131` before storage, so this is unlikely for recent data. However, if the database contains legacy rows from earlier versions with empty `platform`, those bot messages will no longer be filtered. **Recommendation:** Before deploying, run `SELECT DISTINCT platform FROM mai_messages WHERE user_id = '{qq_account}'` to verify data distribution. If empty-platform rows exist, consider a one-time data migration or add `("", str(qq_account))` to `get_all_bot_accounts()`.

### 3. `parse_platform_accounts` Key Normalization (Fixed by This Plan)

`parse_platform_accounts()` at `utils.py:31` historically called `.strip()` on keys but not `.lower()`. This plan adds `.lower().strip()` normalization in `parse_platform_accounts`, `is_bot_self`, and `get_bot_account` (see Phase 1 Required Helper Additions), resolving this gap.

---

## Phase Gates

Every phase must pass before the next starts:

1. Read every file that will be modified in full.
2. Record the search checklist output before editing.
3. Make only the changes required for the current phase.
4. Record the search checklist output after editing.
5. Run tests or smoke checks.
6. Verify no unrelated file was modified.
7. **Phase 1 only:** Before committing, run `SELECT DISTINCT platform FROM mai_messages WHERE user_id = '{qq_account}'` (or equivalent) to verify stored platform distribution. If empty-platform bot rows exist, add `("", str(qq_account))` to `get_all_bot_accounts()` as a legacy compatibility entry and document this in the phase report.
8. Produce a short phase report.

---

## Definition of Done

This plan is complete when:

- `is_bot_self(platform, user_id)` has one real implementation with no argument-order traps
- The unknown-platform QQ fallback (lines 112-113) is deleted
- `get_bot_account(platform)` exists; `get_current_platform_account` is deleted
- Sender construction uses `get_bot_account(platform)` at all 4 sites
- `filter_bot=True` uses platform-aware `(platform, user_id)` pair matching
- `person_info.py` prompts use neutral wording
- No regression in WebUI bot message storage or filtering
- User-visible format changes and PFC platform propagation remain deferred

---

## Follow-Up Plan Topics

These are explicitly deferred and should be addressed in subsequent plans:

1. **PFC platform propagation** — Add `platform` to `chat_observer._message_to_dict()` serialization, update consumers in `chat_states.py`, `observation_info.py`, `conversation.py`. Prerequisite for migrating PFC-internal identity checks.
2. **PFC-internal identity migration** — After propagation is fixed, migrate `reply_checker.py:61`, `action_planner.py:157`, and other PFC dict-based bot checks to `is_bot_self`.
3. **PFC prompt platformization** — Replace `QQ私聊`/`QQ聊天` with platform-aware wording after platform is available in PFC prompt context.
4. **WebUI identity separation** — Introduce `WEBUI_BOT_USER_ID`, update sender construction, add dual-acceptance transition period.
5. **Capability query API defaults** — Change `args.get("platform", "qq")` to `args.get("platform", "all_platforms")`.
6. **MCP permission format** — Change context IDs from `qq:{id}:...` to `{platform}:{id}:...`.
7. **UI/config cleanup** — Dashboard labels, setup flow, adapter naming, config field renames.
8. **WebUI virtual-group session ID fix** — `ChatHistoryManager._resolve_session_id()` always uses `"webui"` platform, but virtual messages are stored with simulated platform. Session IDs mismatch — needs investigation and fix.
