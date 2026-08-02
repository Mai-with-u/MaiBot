# 0729 剩余问题的最小修改方案

- 方案日期：2026-08-02
- 当前状态：仅供实施评审，已根据第三轮实施边界修订，尚未修改实现
- 对照范围：`src/A_memorix/docs` 中全部标记 20260729 的问题文档与当前源码
- 排除范围：问题 4 元数据 schema 迁移、问题 5 MaiBot 增量迁移
- 重新纳入：问题 6 向量恢复中的 V1→V2 正常升级误判、重复迁移记录和恢复状态语义

## 1. 方案边界

本方案只处理排除项之外仍能由当前源码确认的问题。目标不是为所有数据操作增加暂存区、双版本、全局事务或只读降级，而是缩小错误影响范围，并让历史数据继续参与正常读取。

实施时遵循以下约束：

- 校验负责发现结构矛盾，不负责猜测业务类别。用户能够修改的输入不能因为预期类别不匹配而失去修正入口。
- 兼容旧数据优先使用读取语义补齐。只有无法从既有字段可靠解释的数据，才进入显式审计或人工修复。
- 非阻塞不等于吞掉错误。迁移、图、Episode 或后台任务失败后，应保留结构化错误状态，同时让不依赖该通道的功能继续运行。
- 不为普通增量修改引入全存储 staging。只有 LPMM 这类会整体替换三类存储的离线转换，才保留独立输出目录。
- `src/A_memorix/core`、`src/A_memorix/scripts` 的实现修改应优先提交到上游 `MaiBot_branch`，再同步回 MaiBot。Dashboard、宿主服务和集成测试可直接在 MaiBot 仓库修改。

## 2. Web 导入归属与检索范围不一致

### 2.1 当前源码

导入表单默认不选择聊天流，只有非空值才写入 `chat_id`：

```ts
const [importCommonChatId, setImportCommonChatId] = useState('')

const buildCommonImportPayload = useCallback((): Record<string, unknown> => {
  const chatId = importCommonChatId.trim()
  const payload: Record<string, unknown> = {
    llm_enabled: importCommonLlmEnabled,
    strategy_override: importCommonStrategyOverride,
    dedupe_policy: importCommonDedupePolicy,
  }
  if (chatId) {
    payload.chat_id = chatId
  }
  return payload
}, [importCommonChatId])
```

导入端继续用缺少 `chat_id` 表达无归属：

```python
@staticmethod
def _chat_metadata_from_params(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    chat_id = str(params.get("chat_id") or "").strip()
    if not chat_id:
        return None
    return {"chat_id": chat_id}
```

聊天范围过滤只接受元数据聊天 ID 或 `chat_summary:<chat_id>` 来源：

```python
metadata = coerce_metadata_dict(paragraph.get("metadata"))
if cls._metadata_chat_scope_ids(metadata) & allowed_chat_ids:
    return True

source = str(paragraph.get("source", "") or metadata.get("source", "") or "").strip()
return any(source == str(cls._chat_source(allowed_chat_id) or "") for allowed_chat_id in allowed_chat_ids)
```

### 2.2 修改方案

1. Dashboard 增加明确的导入范围控件，只提供全局资料、指定聊天流两个选项。默认选择全局资料，以保持当前不绑定聊天流的实际使用习惯。
2. payload 始终发送 `scope_type`。全局资料发送 `scope_type=global`，聊天资料发送 `scope_type=chat` 和真实 `ChatSession.session_id`。
3. `_chat_metadata_from_params()` 改为返回统一范围元数据：
   - 全局资料：`{"scope_type": "global"}`
   - 聊天资料：`{"scope_type": "chat", "chat_id": "..."}`
4. 在 `MemorySearchHitProcessingService` 中建立唯一的范围解析函数。普通 search、time、hybrid、episode 和 aggregate 都使用同一判断，不再用 `chat_summary:` 代表全部聊天范围数据。
5. 范围条件作为结构化查询参数沿 `KernelSearchRequest -> SearchExecutionRequest -> DualPathRetriever` 向下传递。dense、sparse、graph 和 Episode 候选都应在各自 Top-K 截断前应用同一范围判断，不再依赖固定倍数扩大候选集后补过滤。共享组仍由查询授权层展开为 `allowed_chat_ids`，不增加第三种持久化 scope 类型。

### 2.3 历史数据

不执行强制迁移。读取时按下列顺序解释范围：

1. 存在 `scope_type` 时按新字段处理。
2. 存在 `chat_id`、`session_id` 或 `stream_id` 时解释为聊天资料。
3. `source` 以 `web_import:` 开头且没有任何聊天字段时，解释为旧版全局资料。
4. `source` 以 `chat_summary:` 开头时，从来源中解析聊天 ID。
5. 其他无范围数据保持不可跨聊天读取，并在管理端审计列表中显示为范围未知。

这套兼容只针对来源明确的旧 Web 导入，不把任意缺少 `chat_id` 的数据都提升为全局资料。

### 2.4 验收条件

- 新导入数据不再出现范围缺失。
- 旧版无 `chat_id` 的 `web_import:*` 数据在所有检索模式中按全局资料读取。
- 聊天资料只对目标聊天流和明确配置的共享组可见。
- 大量其他聊天流高分结果存在时，目标范围内结果仍可进入 Top-K。
- 无法解析范围的数据会显示原因，但不会阻止其他记忆检索。

## 3. 人物事实的证据来源、来源权威性和生命周期没有闭合

### 3.1 当前源码

涉及位置：`src/services/memory_flow_service.py`、`src/person_info/person_info.py`、`src/common/database/database_model.py`、`src/A_memorix/core/runtime/services/ingest_service.py`、`src/A_memorix/core/storage/metadata_fact.py`

MaiBot 写入的是 `evidence_source=user_supported`，没有写入 `fact_claim.trust`：

```python
result = await memory_service.ingest_text(
    external_id=external_id,
    source_type="person_fact",
    text=clean_content,
    chat_id=clean_chat_id,
    person_ids=[person_id],
    metadata={
        "person_id": person_id,
        "evidence_source": str(evidence_source or "user_supported"),
        "evidence_message_ids": evidence_message_ids or [],
    },
)
```

A_Memorix 只根据 `fact_claim.trust` 判断是否可信：

```python
_TRUSTED_FACT_ORIGINS = {"manual_confirmed", "server_verified", "trusted_import"}

raw_claim = metadata.get("fact_claim")
claim_spec = dict(raw_claim) if isinstance(raw_claim, dict) else {}
evidence_source = str(metadata.get("evidence_source", "") or "").strip()
trust = str(claim_spec.get("trust", "") or "").strip().casefold()
trusted = trust in _TRUSTED_FACT_ORIGINS
```

事实账本本身已经分别保存权威性、稳定性和生命周期状态，没有必要继续保留输入层的 `trust` 翻译：

```python
_STABILITIES = {"stable", "temporal", "uncertain"}
_AUTHORITIES = {"manual", "direct_user", "imported", "summary_derived"}
_STATUSES = {"active", "conflicted", "superseded", "retracted"}
```

未可信的分类结果只能进入近期互动或不确定记录：

```python
target = section if section in {"recent_interactions", "uncertain_notes"} else "uncertain_notes"
```

### 3.2 修改方案

1. 删除人物事实输入中的 `fact_claim.trust` 概念，直接使用事实账本已有字段：
   - `evidence_source` 只说明证据载体，例如 `user_message`、`manual_input`、`trusted_import`。
   - `authority` 说明陈述者与主体的关系，例如 `direct_user`、`user_reported`、`manual`、`imported`、`summary_derived`。
   - `stability` 说明事实预计持续多久，不表示客观真值。
   - `status` 继续由账本状态机维护。新 claim 通过校验后进入 `active`，冲突、取代和撤回分别使用现有状态；调用方不能直接提交 `status=accepted` 绕过状态机。
2. 新写入 payload 采用下列语义，不再出现 `user_supported` 同时承担来源和可信度的情况：

```json
{
  "evidence_source": "user_message",
  "evidence_refs": [
    {
      "platform": "平台",
      "message_id": "消息 ID",
      "session_id": "真实聊天流 ID"
    }
  ],
  "fact_claim": {
    "authority": "direct_user",
    "stability": "stable"
  }
}
```

3. `authority=direct_user` 只表示人物本人在有效消息中直接陈述，不能解释为客观真实。人物本人后来否认、修正或补充时，通过 refute evidence、conflicted 或 superseded 状态表达变化。
4. 用户陈述第三方信息时使用 `authority=user_reported`，默认进入 `uncertain_notes`。只有人工确认或其他受支持证据使其满足投影条件后，才能进入稳定画像。
5. `confidence` 表达抽取和主体绑定的置信度，不表达事实为真的概率，不能单独决定稳定画像投影。
6. MaiBot 在构造 payload 前，从消息数据库重新读取证据记录并完成核验。LLM 只能返回候选 message ID 和 claim 内容，不能自行声明 `authority`。
7. A_Memorix `ingest_service` 接收已经核验的 authority、stability 和证据引用，不再使用 `_TRUSTED_FACT_ORIGINS` 把来源枚举翻译成权威等级。
8. 保留人工修正接口。修正生成新的 claim，并通过 `supersedes_claim_ids` 取代旧 claim，不通过删除历史段落实现覆盖。

### 3.3 新证据的核验条件

MaiBot 侧只有同时满足以下条件，才可以把 claim 标记为 `authority=direct_user`：

- 证据消息能在 `mai_messages` 中按 platform 和 message ID 唯一找到。
- 消息的 `session_id` 等于 claim 所属聊天流，或属于显式允许的证据来源聊天流。
- 消息发送者不是 bot、系统通知或命令生成者。
- 根据消息的 platform、`user_id` 解析出的真实 `person_id` 与 claim 主体一致。
- 证据内容确实是本人的陈述，而不是引用、转述第三方或模型生成文本。
- 如果消息系统存在删除、撤回或无效标记，证据记录必须仍然有效。

当前 `Messages` 表没有独立的删除、撤回或失效字段。新流程只能确认消息在 claim 写入时存在，并记录 `evidence_checked_at`，不能声称可以追踪之后发生的撤回。历史回溯也不能据此证明证据在所有时点都有效；缺少这一信息的旧 claim 继续按旧版证据展示，不自动提升为新流程的完整权威等级。

后续若消息系统增加统一的撤回或失效状态，再由消息生命周期事件把关联 claim 标记为 `retracted` 或触发重新核验。当前方案不为 A_Memorix 单独复制一套消息有效性数据库。

### 3.4 历史数据

旧数据不再因为满足 `evidence_source=user_supported`、存在 message ID、`person_id` 一致三项条件而自动升级。处理方式如下：

1. 原 claim 和 paragraph 继续可读，并显示为有旧版用户证据引用。
2. 未完成消息级核验时保持 `summary_derived + uncertain`，不获得与新流程相同的 `direct_user` 权威等级。
3. 可选的历史核验任务由 MaiBot 侧读取真实消息数据库，逐条验证消息存在、聊天归属、发送者和主体关系。
4. 核验成功且发送者就是 claim 主体时，生成新的 `direct_user` claim，并显式 supersede 旧 claim。
5. 发送者不是 claim 主体时，生成 `user_reported` claim 或继续保留不确定状态。
6. 消息缺失、无法唯一定位、来源不允许或有效性未知时只更新审计状态，不提升画像投影等级。

历史核验失败不阻止画像刷新。未核验旧事实仍可在不确定记录中显示，管理端提供证据状态和人工修正入口。

### 3.5 验收条件

- 只有主体本人、真实存在且归属正确的用户消息能够产生 `authority=direct_user`。
- 第三方用户陈述不会自动获得本人自述的权威等级。
- `direct_user` 在界面和日志中表达为本人陈述，不显示为已证实事实。
- 模型归纳结果不能仅凭内容形式升级为稳定事实。
- 旧版 metadata 不会在没有消息级核验时自动升级。
- 用户修正后，新 claim 生效，旧 claim 仍可审计。
- 单条坏 claim 不阻止同一人物的其他有效事实刷新。

## 4. 内部 ID 被写成普通实体

### 4.1 当前源码

运行时写入把 `person_ids`、参与者名称和普通实体合并：

```python
person_tokens = tokens(person_ids)
participant_tokens = tokens(participants)
entity_tokens = merge_tokens(entities, person_tokens, participant_tokens)

for name in entity_tokens:
    entity_hash = self.metadata_store.add_entity(name=name, source_paragraph=paragraph_hash)
    await self._ensure_entity_vector({"hash": entity_hash, "name": name})
```

底层 `add_entity()` 只检查非空，不知道名称来自显示文本还是内部 ID：

```python
name_normalized = self._canonicalize_name(name)
if not name_normalized:
    raise ValueError("Entity name cannot be empty")

hash_value = compute_hash(name_normalized)
```

### 4.2 修改方案

1. `ingest_text()` 中 `person_ids` 只保存在段落归属和人物关联字段，不再进入 `entity_tokens`。
2. 普通实体只合并 `entities` 与可显示的 `participants`。如果参与者值与任一 `person_id` 完全相同，则不把它当成显示实体。
3. 人物写回端不再用 `person_id` 作为参与者显示名的最终 fallback。没有名称时可以只保留 `person_id` 关联，不创建人物名称实体。
4. 不新增 `subject_kind`、`object_kind` 一类端点状态。当前污染来自 `person_ids` 被合并进 `entity_tokens` 的单一写入链，修复该来源即可；关系端点继续使用现有实体名称契约。
5. 移除 Web 导入链按 32、40、64 位十六进制外观拒绝实体和关系端点的规则，也不在 `MetadataStore.add_entity()` 中增加同类规则。字符串外观不能证明业务含义，合法名称必须可以正常导入和修改。

### 4.3 历史数据

提供只读审计命令，按真实引用关系识别污染，而不是按字符串形态识别：

1. 收集历史段落 metadata 中出现过的 `person_ids`。
2. 查找名称与这些 ID 完全相同的实体。
3. 有可靠人物别名时，把段落实体关联和关系端点改接到别名实体。
4. 没有别名但仍被关系使用时只标记待修复，不自动删除。
5. 仅当实体确认是内部 ID、没有有效关系和人工节点来源时，才允许显式清理。

审计失败不影响现有图和段落读取。

### 4.4 验收条件

- 新写入的 `person_id` 不再出现在实体名称和图节点中。
- 合法的十六进制实体名称仍可由图管理接口创建和修改。
- 历史污染节点不会仅凭正则表达式被批量删除。
- 人物 ID 关联和人物显示名称可以独立存在。

## 5. 启动格式迁移会重复写完成记录，V1 向量升级会误判正常旧数据

### 5.1 当前源码

涉及位置：`src/A_memorix/core/runtime/services/runtime_lifecycle_service.py`、`src/A_memorix/core/storage/format_migration.py`、`src/A_memorix/core/storage/vector_store.py`、`src/A_memorix/core/runtime/services/vector_recovery_service.py`、`pytests/A_memorix_test/test_format_migration.py`、`pytests/A_memorix_test/test_vector_store_consistency.py`

格式迁移位于所有 Store 初始化之前，并且每次新运行时启动都会调用：

```python
self.data_dir.mkdir(parents=True, exist_ok=True)
kernel_module.run_startup_format_migration(self.data_dir)
try:
    self.embedding_manager = kernel_module.create_embedding_api_adapter(...)
```

检测到完成记录后，SQLite 行转换会跳过：

```python
if _migration_record_exists(conn) and not _legacy_pickle_exists(data_dir):
    summary["sqlite"] = {"updated": 0, "reason": "already_applied"}
else:
    summary["sqlite"] = _migrate_sqlite_metadata(conn)
```

但函数随后仍无条件覆盖写入同一条完成记录：

```python
conn.execute(
    """
    INSERT OR REPLACE INTO storage_format_migrations (version, applied_at, summary_json)
    VALUES (?, ?, ?)
    """,
    (FORMAT_MIGRATION_VERSION, time.time(), _json_dumps(summary)),
)
conn.commit()
```

`finished_at` 又在完成记录写入之后才加入返回值，因此首次记录没有完整结束时间：

```python
summary["finished_at"] = time.time()
logger.info("A_Memorix 存储格式迁移检查完成: ...")
```

现有重复执行测试只验证第二次返回 `already_applied`，没有验证迁移记录、数据库哈希和 mtime 保持不变：

```python
second = run_startup_format_migration(data_dir)
assert second["sqlite"]["reason"] == "already_applied"
```

V1 向量没有提交代际。当前升级逻辑要求 V1 `known_hashes` 生成的 ID 多重集合与 `vectors_ids.bin` 完全一致：

```python
def _migrate_vector_metadata_v1_unlocked(...):
    """V1 无提交代际；只有全量 ID 与 pair 严格一致时才单向升级。"""
    ...
    actual_ids = self._disk_id_multiset_unlocked()
    if actual_ids != expected_ids:
        raise VectorStoreIntegrityError(
            "V1 向量元数据与成对文件不一致，拒绝升级: "
            f"missing={missing_count}, unexpected={unexpected_count}",
            error_code="v1_id_set_mismatch",
            ...
        )
```

旧 V1 运行时会在缓冲刷新时先追加 `vectors.bin` 和 `vectors_ids.bin`，`known_hashes` 则在 `save()` 时整体写入 metadata。升级覆盖前只要没有完整执行 shutdown，就可能留下可以由当前 MetadataStore 解释、但尚未进入 V1 metadata 的正常尾部。旧版压缩后尚未保存 metadata，也可能形成相反方向的差异。

当前运行时把 `v1_id_set_mismatch` 与真正损坏使用同一恢复路径。它会移动整个向量根目录、重置 metadata 向量投影状态并创建空 V2 双池：

```python
if error.error_code not in self._KNOWN_RECOVERABLE_CODES:
    return False
...
os.replace(source_path, quarantine_path)
...
journal["metadata_reset"] = self.metadata_store.reset_vector_projection_state()
...
self._prepare_empty_dual_generation()
```

恢复 journal 在旧向量复制仍为 `pending` 时就写入 `stage=completed`：

```python
journal["copy"] = {
    "state": "pending" if legacy_view is not None else "skipped",
    ...
}
journal["stage"] = "completed"
```

重启后如果 copy 尚未完成，运行时会重新打开旧向量视图继续复制。这是续跑，但 `completed` 会让用户误以为迁移完成后又执行了一次。

### 5.2 问题边界

当前存在三种不同现象，不能统一解释为重复迁移：

1. `run_startup_format_migration()` 每次启动都会进入，这是重复检查。
2. `already_applied` 后仍执行 `INSERT OR REPLACE`，这是真实的重复写入，并会覆盖首次迁移审计记录。
3. V1 向量转换成功写入 `schema_version=2` 后不会再次进入 V1 迁移；恢复 copy 在重启后继续推进属于未完成任务续跑，但当前阶段名称错误。

全新数据目录第一次启动时还没有 metadata.db，格式迁移无法写完成记录；MetadataStore 随后创建数据库。第二次启动才会创建 `storage_format_migrations` 并记录一次没有实际旧格式输入的迁移。这是一条没有必要的延迟完成记录。

Metadata schema 成功写入当前 `SCHEMA_VERSION` 后，重启不会再次执行 runtime auto migration。这个版本判断应保留，不为它增加新的迁移控制器。

### 5.3 启动格式迁移修改方案

启动格式迁移采用只读检测、按需执行、一次性提交，不再把缺少完成记录本身视为迁移需求：

1. 增加轻量的 `detect_startup_format_work()`，只检查：
   - 配置数据目录中是否存在活动的 legacy pickle，不包含 `.bak`。
   - paragraphs、entities、relations、deleted_relations 的 metadata 是否存在 pickle blob，可使用 `EXISTS ... LIMIT 1`，不读取整表。
   - 需要转换的当前 JSON 是否缺失或无法通过对应格式的结构检查。
2. 没有旧格式输入时直接返回 `not_required` 或 `already_applied`。该路径不创建 `storage_format_migrations` 表、不启动写事务，也不修改数据库、JSON、mtime 或 WAL。
3. 只有检测到实际转换工作后才切换到读写连接。metadata 使用 `fetchmany()` 分批处理；单行无法解析时记录 rowid 和错误，继续处理其他行，但不写完成标记。
4. `finished_at`、转换数量、输入文件和各组件结果必须先写入 summary，再提交迁移记录。
5. 完成记录使用首次插入语义，不再使用 `INSERT OR REPLACE`：

```sql
INSERT INTO storage_format_migrations (version, applied_at, summary_json)
VALUES (?, ?, ?)
ON CONFLICT(version) DO NOTHING
```

6. 完成记录代表该格式版本第一次成功转换的提交证据，不兼作每次启动的检查日志。后续只读检查不能更新 `applied_at` 和 `summary_json`。
7. 已有完成记录后重新出现 legacy 文件时，先检查当前 JSON：
   - 当前 JSON 有效时报告 `stale_legacy_artifact`，继续使用当前格式，不重复转换和覆盖完成记录。
   - 当前 JSON 缺失或损坏时报告 `legacy_artifact_reintroduced`，进入对应组件的显式修复状态，不自动把它伪装成原迁移再次成功。
8. 向量和图备份按时间倒序逐份读取。候选必须依次通过受限反序列化、组件内部结构检查和跨 Store 引用检查，不能因为单个文件可解析就立即激活。
9. 备份名称使用 `time.time_ns()`、UUID 或排他创建，避免秒级碰撞。
10. 生命周期根据组件结果决定能力状态：
    - 图格式迁移失败时禁用图通道，metadata、稀疏检索和可用向量继续启动。
    - 单个向量池迁移失败时只禁用该池。
    - metadata 中少量历史 blob 失败时继续连接数据库，相关行读取时返回明确的数据损坏错误。
    - metadata 数据库本身无法打开时，A_Memorix 显示不可用，但不能阻止 MaiBot 主程序启动。
11. 日志分别使用无需格式迁移、格式迁移完成、发现重新出现的旧格式文件，不再让每次启动都显示迁移完成。

### 5.4 V1 向量对账与一次性升级

V1 缺少 `binary_commit`，不能用 V2 的提交语义倒推所有集合差异都是损坏。修复只放宽 `v1_id_set_mismatch`，V2 commit、dimension 和 fingerprint 校验继续严格。

存储层不直接查询 MetadataStore。现有运行时恢复服务已经同时持有 metadata 和向量组件，应由它完成跨 Store 对账，再调用 VectorStore 的结构化检查和 pair 重写辅助函数：

1. V1 自动对账只在下列条件同时成立时启用：
   - metadata schema 确实为 V1。
   - dimension 和 embedding fingerprint 与当前运行时匹配。
   - `vectors.bin` 与 `vectors_ids.bin` 各自长度合法，行数一致，pair 没有截断。
   - 错误码仅为 `v1_id_set_mismatch`，不是 metadata 类型错误、tombstone 类型错误或维度错误。
2. MaiBot 运行时从 MetadataStore 读取活动 paragraph、entity、relation 的原始 hash，构造 `int64 ID -> 原始 hash` 候选映射。映射必须唯一；发生 int64 碰撞的候选不自动采信。
3. V1 metadata 和 pair 中都存在、且磁盘 ID 唯一的记录直接保留。
4. pair 中存在、V1 metadata 中缺失，但能唯一映射到当前 MetadataStore 活动记录的尾部，解释为可恢复的 V1 未提交记录并保留。
5. V1 metadata 中存在、pair 中缺失的记录不伪造向量，只写入对账报告 `missing_requires_explicit_rebuild`，不创建自动 backfill 或新的持久队列。
6. pair 中无法映射到 MetadataStore 的额外 ID 记录为 `unresolved_orphan`，不进入 V2。重复磁盘 ID、映射碰撞和非法 tombstone 分别报告，不静默选取第一条或最后一条。
7. 仅使用现有向量 pair 临时文件、journal 和原子替换协议重写这个 VectorStore。成功后删除事务临时备份，不保留长期双版本目录，也不创建跨 Store generation。
8. V2 metadata 是这次单 Store 转换的提交点，包含 `schema_version=2`、新的 `binary_commit`、最终 `known_hashes` 和 `deleted_ids`。重启后只走 V2 校验。
9. 对账成功时不调用 `reset_vector_projection_state()`，也不创建空双池。缺失向量只在用户显式执行 `rebuild_all_vectors` 时重新生成。
10. 对账无法安全完成时保留原文件，关闭对应向量通道并完整报告原因；metadata、稀疏检索、图和其他可用向量池继续运行。不能把无法解释自动转换成空向量库。

这个方案不是容忍任意不一致。它只利用 MetadataStore 作为原始 hash 的权威来源，恢复可唯一解释的 V1 尾部；无法证明归属的向量仍然拒绝进入 V2。

### 5.5 向量恢复阶段语义

恢复 journal 改为：

```text
prepared
quarantined
metadata_reset
new_generation_ready
copying
completed
```

只有 copy 状态达到 `completed` 或明确 `skipped` 后，journal 才进入最终 `completed`。重启时 `copying` 可以幂等续跑，界面显示向量恢复中。

旧 journal 按读取语义兼容，不批量重写：

```python
if stage == "completed" and copy_state not in {"completed", "skipped"}:
    effective_stage = "copying"
```

旧记录满足上述条件时继续复制，完成后再用一次原子 journal 写入收敛到真正的 `completed`。

### 5.6 generation manifest

成组转换和恢复写入 generation manifest。manifest 只用于同时恢复多个 Store 的受控操作和 LPMM 整体替换，不用于本节的 V1 单 VectorStore 对账，也不要求普通增量写入创建全 Store 世代。

```json
{
  "generation_id": "唯一世代 ID",
  "created_at": 0,
  "metadata_generation": "metadata 世代",
  "paragraph_vector_generation": "段落向量世代",
  "graph_vector_generation": "图向量世代",
  "graph_generation": "关系图世代",
  "record_counts": {},
  "checksums": {},
  "schema_versions": {},
  "embedding_model_fingerprint": "",
  "dimension": 0,
  "quantization_type": ""
}
```

manifest 的校验规则如下：

- checksum 必须对应准备激活的实际文件，不能只校验 manifest 自身。
- paragraph vector 的每个 ID 必须指向当前 metadata 中真实、未删除的 paragraph。较旧向量缺少新 paragraph 时可以进入明确的向量待回填状态，但不能包含无法解释的额外 ID。
- graph vector 的实体和关系 ID 必须能在当前 metadata 中解析。
- graph edge relation map 中的 relation hash 必须属于当前活动 relation；缺失边应从当前 metadata 重建，不能从较旧图静默补回。
- dimension、量化方式和 embedding model fingerprint 必须与当前向量读取配置兼容。
- 同一 manifest 声明的组件必须通过组合校验后才能写入完成标记。

旧备份没有 generation manifest 时，不假定它与其他 Store 属于同一世代。系统为候选生成临时审计结果，依据当前 metadata 计算引用覆盖率和缺失集合。只要存在错误关联、无法解释的 ID 或模型指纹冲突，就不得激活该候选。

### 5.7 pickle 信任边界

启动格式迁移只能读取配置数据目录中的已知历史文件，不能把 Web 上传或任意用户路径当作迁移 pickle。具体要求如下：

1. 继续使用当前 `_LegacyDataUnpickler`，禁止普通 `pickle.load()` 和任何 GLOBAL 对象加载。
2. 读取前限制文件大小，读取后要求顶层和嵌套结构只包含转换器明确支持的基础容器、字符串和数值。
3. 把本地数据目录视为完整性可能损坏、但来源由本机管理员控制的历史文件。文档明确承认这一信任边界，不声称受限 Unpickler 能抵御已获得本地文件写权限的攻击者。
4. 用户提供或来源不明的 pickle 不进入启动流程。确有离线导入需要时，使用独立低权限转换进程、显式确认和独立输出目录。

### 5.8 完整性检查

向量 metadata 至少核对 ID 类型和唯一性、pair 文件结构、dimension、dtype、量化类型、embedding model fingerprint 和当前 MetadataStore 引用覆盖率。V1 对账报告分别列出 `preserved`、`recovered_tail`、`missing`、`unresolved_orphan`、`duplicate` 和 `collision`，不能只给出总数不一致。

图 metadata 至少核对节点索引双射、邻接矩阵维度、边映射端点范围、relation hash 是否真实存在。备份恢复的有效条件是组件内部结构和跨 Store 引用同时成立。

检查失败要完整暴露，不自动套用默认值修补结构。只有 V1 中能被当前 MetadataStore 唯一解释的尾部可以进入升级结果。

### 5.9 历史数据与验收条件

现有 V2 metadata 不批量改写。旧版格式迁移完成记录保留首次 `applied_at` 和首次完整 summary；旧恢复 journal 通过读取语义解释 `completed + pending`。V1 对账只在首次成功升级时重写单个向量 pair，无法解释的数据保留在报告中，不自动提升为有效向量。

验收至少覆盖：

- 首次格式迁移完成后再次启动，迁移记录、数据库哈希、mtime、WAL 和当前 JSON 均不变化。
- 全新 V2 数据目录没有旧格式输入时，不在第二次启动补写一条虚假的格式迁移记录。
- 首次迁移 summary 包含 `finished_at`，后续检查不会覆盖它。
- 完成记录存在且旧 pickle 重新出现时，不重复转换和覆盖完成记录。
- 最新备份损坏、较旧备份有效且与当前 metadata 引用兼容时可以恢复。
- 不同向量池和图分别恢复到不同时间点时，组合校验能够发现错误引用和模型指纹冲突。
- V1 metadata 落后于 pair、额外 ID 能唯一映射到 MetadataStore 时，该尾部在升级后仍可检索。
- V1 metadata 包含 pair 中缺失的 ID 时，其他可信向量继续可用，缺失项只进入 `missing_requires_explicit_rebuild` 报告。
- 无法映射的孤儿、重复 ID 和碰撞不会进入 V2，也不会导致整个向量根目录自动清空或隔离。
- V1 对账成功后第二次启动只执行 V2 校验，不再写迁移 metadata。
- `copying` 重启后续跑，只有 copy 完成或明确跳过才显示 `completed`。
- 单条历史 metadata blob、单个图或单个向量池失败不会关闭其他记忆通道。
- 普通 `pickle.load()` 不会出现在启动迁移调用链中。
- 没有旧格式数据时，启动过程不产生迁移写入。
## 6. LPMM 转换可能破坏已有存储或切换不完整结果

### 6.1 当前源码

涉及位置：`src/A_memorix/scripts/convert_lpmm.py`、`src/A_memorix/core/utils/web_import_manager.py`、`src/A_memorix/paths.py`

CLI 会直接清空输出目录中的向量和图，但不会同步清空 metadata：

```python
self.vector_store = VectorStore(..., data_dir=self.vector_dir)
self.vector_store.clear()

self.graph_store = GraphStore(..., data_dir=self.graph_dir)
self.graph_store.clear()

self.metadata_store = MetadataStore(data_dir=self.metadata_dir)
self.metadata_store.connect()
```

Web 校验只检查目录存在，`*_nonempty` 甚至没有进入最终判断：

```python
checks = {
    "vectors_exists": vectors.exists(),
    "graph_exists": graph.exists(),
    "metadata_exists": metadata.exists(),
    "vectors_nonempty": vectors.exists() and any(vectors.iterdir()),
    "graph_nonempty": graph.exists() and any(graph.iterdir()),
    "metadata_nonempty": metadata.exists() and any(metadata.iterdir()),
}
checks["ok"] = checks["vectors_exists"] and checks["graph_exists"] and checks["metadata_exists"]
```

随后会逐个移动活动目录：

```python
for name in ("vectors", "graph", "metadata"):
    src_current = target_dir / name
    src_new = staging_dir / name
    if src_current.exists():
        shutil.move(str(src_current), str(backup_dir / name))
    shutil.move(str(src_new), str(src_current))
```

### 6.2 选定的原子安装模型

LPMM 同时替换 metadata、vectors 和 graph，采用 generation 根目录加活动指针。该机制只用于这种整体替换，不推广到普通 schema、格式修复和增量写入。

```text
data/
  generations/
    gen-old/
      vectors/
      graph/
      metadata/
      manifest.json
    gen-new/
      vectors/
      graph/
      metadata/
      manifest.json
  ACTIVE
```

运行时不再直接把 `data/vectors`、`data/graph`、`data/metadata` 视为唯一位置，而是先解析 `ACTIVE`。没有 `ACTIVE` 的现有数据目录按 `legacy-root` 读取，第一次 LPMM 激活不要求先搬移旧目录，旧目录可以作为显式回退目标保留。

### 6.3 转换方案

1. CLI 默认要求目标 generation 不存在或为空。发现已有 Store 时直接返回非零，不执行任何 `clear()`。
2. 转换前先校验输入 parquet、pickle、向量维度和必要列。单批向量在写 metadata 前完成维度检查。
3. 转换器累积结构化错误。任一必要文件失败、关系图转换失败或 Store 保存失败时，进程必须返回非零，不输出所有转换成功完成。
4. 每个 generation 必须带第 5 节定义的 manifest、各 Store checksum、schema version、记录数和引用覆盖率。
5. `_verify_convert_output()` 实际打开三个 Store，核对段落数、实体数、关系数、段落向量覆盖率、关系向量覆盖率、图节点和边映射覆盖率。
6. Web 端只负责生成并校验 `gen-new`，不在活动运行时中搬移任何 Store 目录。
7. 转换 CLI 直接生成 `vectors/paragraph`、`vectors/graph` 和 `dual_ready.json`。candidate 不再输出旧单池结构，也不依赖活动运行时在激活前二次拆池。

### 6.4 激活协议

1. 确认 `gen-new`、`ACTIVE.tmp` 和 `ACTIVE` 位于同一文件系统。不能只比较路径字符串，应比较 Windows volume 或 POSIX device ID。跨文件系统输出不得进入激活步骤。
2. 在切换前完成 manifest、checksum、Store 打开测试和跨 Store 引用校验。失败时活动运行时继续使用旧 generation，不进入维护状态。
3. 进入短时维护状态后停止接收新的 A_Memorix 读写请求，并按固定顺序停止：
   - Web 导入、检索调优和其他会写 Store 的任务。
   - Episode、向量回填、图投影等后台任务。
   - 仍持有旧 Store 引用的长时间读取任务。
4. 对未结束任务执行有时限的取消，关闭 metadata SQLite、向量 mmap、图快照和缓存句柄。
5. 关闭完成后由维护协调器持有数据目录独占锁，防止另一运行时在指针切换窗口启动。
6. 将目标 generation ID 写入 `ACTIVE.tmp`，刷新文件后使用同文件系统 `os.replace()` 原子替换 `ACTIVE`。激活过程中不逐个 rename 三个 Store 目录。
7. 使用新指针重新初始化 A_Memorix，并执行最小健康检查。成功后退出维护状态。
8. 指针替换前任一步骤失败时，恢复或继续使用旧 generation。此时不能把 A_Memorix 标记为因新 generation 不可用。
9. 指针已经替换但重新初始化失败时，记录 `activation_failed`、新旧 generation ID 和失败阶段。系统保持 A_Memorix 不可用，等待明确的重试或回退操作；不得静默自动读取旧 generation。
10. 显式回退同样通过原子替换 `ACTIVE` 完成，并在回退前核对旧 generation 或 `legacy-root` 仍然完整。

### 6.5 历史数据与清理

现有活动数据不由转换命令自动覆盖。转换和校验期间继续使用旧数据。新 generation 完成激活和健康检查后，旧 generation 仍保留一个明确观察期；清理是独立维护操作，不在正常运行期按时间自动删除。

### 6.6 验收条件

- 对非空目标 generation 运行 CLI 不会修改任何已有文件。
- 任一 parquet 或向量批次失败时返回非零，Web 任务显示失败。
- 空目录或仅存在目录壳的结果不能通过校验。
- staging 和目标不在同一文件系统时不能激活。
- 激活过程中只替换 `ACTIVE`，不会依次替换三个 Store。
- 指针切换前失败时旧 generation 继续可用。
- 指针切换后初始化失败时进入显式失败状态，可由人工选择重试或原子回退。
- 转换失败不影响当前活动 A_Memorix 数据。

## 7. Web 导入和 aggregate 会错误处理协程异常

### 7.1 当前源码

涉及位置：`src/A_memorix/core/utils/web_import_manager.py`、`src/A_memorix/core/utils/aggregate_query_service.py`

Web 导入丢弃了每个文件协程的结果：

```python
jobs = [
    asyncio.create_task(self._process_file(task_id, f, file_semaphore, chunk_semaphore))
    for f in task.files
]
await asyncio.gather(*jobs, return_exceptions=True)
```

当前取消来源只表现为任务状态，没有独立上下文：

```python
elif task.status in {"preparing", "running"}:
    task.status = "cancel_requested"
    task.current_step = "cancel_requested"
    task.updated_at = _now()
```

aggregate 只把 `Exception` 识别为异常，`CancelledError` 不属于该类型：

```python
done = await asyncio.gather(
    *[task for _, task in scheduled],
    return_exceptions=True,
)
for (branch_name, _), payload in zip(scheduled, done, strict=True):
    if isinstance(payload, Exception):
        ...
```

### 7.2 取消上下文

任务记录增加不可变的取消事件，而不是只保留布尔值：

```python
@dataclass(frozen=True)
class CancellationContext:
    reason: Literal["user_request", "runtime_shutdown", "parent_cancel"]
    requested_at: float
    requested_by: str = ""
```

同一个任务可以记录多个取消事件，终态另存 `terminal_cancellation`。来源判定规则如下：

1. 用户取消继续采用协作式取消。管理器写入 `user_request` 上下文，worker 在安全点读取并自行结束，不对文件 task 调用 `Task.cancel()`。
2. 运行时关闭在取消子 task 前先写入 manager 级 `runtime_shutdown` 上下文，再调用 `Task.cancel()`。
3. 捕获 `CancelledError` 时，如果存在运行时关闭上下文，终态来源为 `runtime_shutdown`；否则来源为 `parent_cancel`。不能因为此前存在用户取消记录就吞掉该异常。
4. 用户取消和运行时关闭同时发生时，两条事件都保留，`runtime_shutdown` 决定传播语义。用户取消不丢失，但不能阻止关闭信号向上传播。
5. 文件已经完成后才收到用户取消时，文件保持 completed，任务摘要记录迟到的取消请求，不倒改已完成结果。

### 7.3 异常和终态收敛

1. Web jobs 保留 `file_id` 与 task 的对应关系，逐项处理 `gather()` 结果。
2. 普通异常必须写入对应文件记录，并把仍处于 queued、preparing、running 的文件改为 failed。
3. 用户协作式取消把尚未完成的文件和 chunk 收敛为 cancelled。
4. 真正的 `CancelledError` 在写入最少终态信息后必须重新抛出。
5. 取消状态持久化使用单独的短清理 task，并通过有上限的 `asyncio.shield()` 保护：
   - shield 只覆盖状态和报告写入，不覆盖业务处理。
   - 外层使用固定短超时，例如 1 秒。
   - 超时或再次取消时记录 best-effort 失败并立即继续关闭，不无限等待。
6. 任务最终状态只在所有文件处于 completed、failed、cancelled 之一时计算。任何中间态存在都不能报告 completed。
7. aggregate 在普通 `Exception` 判断前显式检查 `asyncio.CancelledError` 并重新抛出。其他分支异常继续作为局部失败，不影响已成功分支返回。

### 7.4 验收条件

- 文件协程在第一次状态登记前抛错时，任务结果为 `completed_with_errors`，对应文件为 failed。
- 任一文件停留在中间态时，任务不能达到 100% completed。
- 用户取消、运行时关闭、父任务取消分别得到正确的取消来源。
- 用户取消和运行时关闭并发时，关闭信号仍向上传播，事件历史保留两种来源。
- 文件完成后出现迟到用户取消时，不倒改文件成功状态。
- 状态清理超过时限时不会拖住关闭过程。
- aggregate 子分支被取消时，调用方收到取消信号。
- aggregate 普通分支失败时，其他成功分支仍能返回，并在 errors 中保留真实错误。

## 8. Episode 缺少独立资源上限和显式丢弃协议

### 8.1 当前源码

涉及位置：`src/A_memorix/core/utils/episode_segmentation_service.py`、`src/A_memorix/core/utils/episode_service.py`、`src/A_memorix/core/runtime/services/background_task_service.py`、`src/llm_models/model_client/adapter_base.py`

当前分组已经限制段落数和字符数，但没有输入 token 限制：

```python
max_paragraphs = max(1, int(self._cfg("episode.max_paragraphs_per_call", 20)))
max_chars = max(200, int(self._cfg("episode.max_chars_per_call", 6000)))
```

Episode 直接继承被选中公共任务的输出 token：

```python
result = await generate_with_resolved_model(
    resolved_model,
    request_type="A_Memorix.EpisodeSegmentation",
    prompt=prompt,
    temperature=getattr(resolved_model.task_config, "temperature", None),
    max_tokens=getattr(resolved_model.task_config, "max_tokens", None),
)
```

输出协议只有 `episodes`，要求所有输入 hash 都进入 Episode：

```python
{
  "episodes": [
    {
      "title": "string",
      "summary": "string",
      "paragraph_hashes": ["hash1", "hash2"]
    }
  ]
}
```

LLM 失败或覆盖校验失败后，当前代码会生成包含全部段落的规则 Episode，并在持久化 Episode 中写入 `segmentation_model=fallback_rule`：

```python
except Exception as e:
    logger.warning(f"Episode segmentation fallback: source={source} size={len(group_hashes)} err={e}")
    episodes = [self._build_fallback_episode(group)]
    fallback_used = True
```

底层通用 Provider 适配器已经在调用方取消时取消 child task 并等待清理，但 Episode 目前没有针对超时后的连接释放测试：

```python
finally:
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

### 8.2 输入和输出预算

1. 不增加 tokenizer 依赖，也不把字符数描述成精确 token。保留现有 `max_paragraphs_per_call`，增加简单的 `segmentation_max_input_chars`、`segmentation_max_output_tokens` 和 `segmentation_timeout_seconds`。
2. 分组同时受段落数和字符数限制。单条 paragraph 已超过字符上限时不调用 LLM，为该段生成规则 Episode，并记录 `fallback_reason=input_char_limit`。
3. 实际输出上限取公共任务上限与 Episode 输出上限中的较小值。
4. 使用 `asyncio.wait_for()` 限制单次 Episode 调用总时长，不修改 `utils`、`memory` 等公共任务的超时。
5. title 固定最多80个字符、summary 最多500个字符、discard reason 最多300个字符。超长响应视为协议错误，不静默截断后接受。
6. 当前 prompt 对每段正文截取800字符的行为要写入 provenance，例如 `input_excerpted=true`，避免把截断后的语义结果误认为使用了完整正文。
7. 这组限制只控制一次请求的体积、输出和时长，不新增模型选择、token 估算或 tokenizer 失效状态。

### 8.3 输出协议和完整性

输出协议增加 `discarded`：

```json
{
  "episodes": [
    {
      "title": "标题",
      "summary": "摘要",
      "paragraph_hashes": ["hash1"]
    }
  ],
  "discarded": [
    {
      "paragraph_hash": "hash2",
      "reason": "自由文本原因"
    }
  ]
}
```

校验规则如下：

1. 输入 hash 本身必须唯一。
2. Episode 和 discarded 中出现的 hash 都必须来自本次输入。未知 hash 直接使响应失败，不能先过滤再继续。
3. 同一 hash 在一个 Episode 内、多个 Episode 之间、Episode 与 discarded 之间都不得重复。
4. Episode 接受集合与 discarded 集合的并集必须恰好覆盖全部输入。
5. `reason` 只要求非空并受长度限制，不建立封闭的预期原因类别。
6. 允许全部段落进入 discarded，但必须完整列出每条原因。该来源 revision 记录为 `completed_all_discarded`，不创建空 Episode，也不触发规则 fallback。
7. discarded 只表示不进入本轮 Episode。原始 paragraph 继续被普通检索读取，不执行删除、隐藏或永久忽略。
8. JSON 错误、未知 hash、重复 hash、覆盖不完整、超时和长度违规时生成包含全部输入的规则 Episode。

### 8.4 fallback provenance 与 Provider 取消

每个规则 Episode 自身持久化 provenance，不能只在后台任务摘要中计数：

```json
{
  "provenance": {
    "mode": "fallback_rule",
    "reason": "timeout",
    "source_revision": 1,
    "input_excerpted": false
  }
}
```

现有 `segmentation_model=fallback_rule` 保留，并作为查询旧 Episode 的兼容字段。新 provenance 保存具体原因和输入处理信息。

`wait_for()` 命中后必须验证取消信号能传播到 Provider child task。当前 `await_task_with_interrupt()` 已有 child cancel 和 await 清理逻辑，实施时补充真实适配器测试；任何绕过该辅助函数的 Provider 都必须接入同样的关闭协议。清理超过短时限时记录 `provider_cleanup_timeout`，不得让远端请求无限占用连接和并发额度。

### 8.5 任务顺序保持现有模型

1. 当前源码没有实时、历史两套独立队列，Episode revision 按现有领取顺序和每轮批量限制推进。
2. 本轮不新增 `work_class`、权重、最大等待时间或第二套调度状态，避免为尚未证实的饥饿问题扩展状态机。
3. 若后续指标证明单一队列存在长期饥饿，再基于真实队列等待时间单独设计；不能把它夹带进协议和取消修复。

### 8.6 历史数据

旧 Episode 保持可读。只有来源 revision 发生变化或用户显式重建时才使用新协议，不要求全量重建。原始 paragraph 始终是权威证据，不因 Episode 生成失败或全部 discarded 而失效。

### 8.7 验收条件

- Episode prompt 同时满足段落数和输入字符上限，输出受独立 token 上限约束。
- 未知 hash、跨 Episode 重复 hash 和 accepted/discarded 交叉重复都会被拒绝。
- 全部 discarded 具有明确、可审计的完成状态，原始 paragraph 仍可检索。
- fallback Episode 自身持久化 `provenance.mode=fallback_rule` 和具体原因。
- 超时后 Provider child task 被取消并释放连接；清理不会无限等待。
- 超时和协议错误只影响当前来源批次，后台循环和其他检索继续运行。

## 9. 发布迁移的 dry-run、verify、重复执行和步骤依赖不符合语义

### 9.1 当前源码

涉及位置：`src/A_memorix/scripts/release_vnext_migrate.py`

发布迁移先写配置，再按固定顺序处理其他存储；dry-run 仍创建目录：

```python
config_changes = _migrate_config(config_doc)
if config_changes and not dry_run:
    _write_toml(config_path, config_doc)

vectors_dir = data_dir / "vectors"
vectors_dir.mkdir(parents=True, exist_ok=True)
metadata_dir = data_dir / "metadata"
metadata_dir.mkdir(parents=True, exist_ok=True)
graph_dir = data_dir / "graph"
graph_dir.mkdir(parents=True, exist_ok=True)
```

verify 会以强制 schema 模式连接：

```python
store = MetadataStore(data_dir=metadata_dir)
try:
    store.connect(enforce_schema=True)
    schema_version = store.get_schema_version()
```

`migrate` 模式没有先判断步骤是否已经完成。只要 metadata 数据库存在，就再次调用元数据迁移；只要存在 relation，就再次保存图：

```python
if metadata_db.exists():
    store = MetadataStore(data_dir=metadata_dir)
    if not dry_run:
        metadata_result = store.run_legacy_migration_for_vnext()

if relation_count > 0:
    graph_store.save()
```

该脚本不会由普通重启直接触发，但升级包装程序、人工重试或重复执行发布命令都可能再次调用它。当前实现会重复进入元数据迁移辅助逻辑并重写图文件，无法把已完成步骤识别为 `not_required`。

### 9.2 修改方案

1. `dry-run` 只读取配置和现有文件，生成计划，不创建目录，不初始化会写盘的 Store。
2. `verify` 使用 SQLite 只读连接和各 Store 的只读检查函数。不存在目标目录时直接报告路径错误，不自动创建 schema。
3. 不在文档中预设向量、metadata、图的固定先后顺序。每一种实际发布迁移先声明步骤依赖 DAG，再由拓扑排序产生执行计划。
4. 配置更新始终是最终提交步骤，依赖所有必要数据步骤的后置条件。配置写入表示新运行时可以按新格式启动，不能提前充当迁移开始标记。
5. 每个数据步骤必须声明：
   - 唯一 step ID 和 `depends_on`。
   - 可执行的前置条件。
   - 可验证的后置条件。
   - 不修改数据的 `is_applied` 幂等判断。
   - 中断后重跑从哪里继续。
   - 输出是否兼容旧运行时。
   - 失败时禁止写入哪些完成标记。
6. `is_applied` 必须先于 Store 初始化执行，而且只能读取现有数据：
   - metadata：当前 schema 版本和本次发布要求的索引、列、回填结果都满足时，返回 `true`。
   - 向量：V2 metadata、pair 结构和提交指纹一致时，返回 `true`。
   - 图：活动 relation 的边映射覆盖率和引用校验都满足时，返回 `true`，不能以 edge map 非空代替。
   - 配置：目标键值已经符合新版本时，返回 `true`。
7. schema 版本已经是当前版本、但必要后置条件缺失时，应报告完整性错误。不能把它当成旧版本再次运行通用迁移，这会掩盖部分写入或人工破坏。
8. 步骤只有在所有依赖后置条件满足后才能执行。例如向量转换依赖新 metadata schema 时，DAG 必须安排 metadata 在前；图只依赖稳定 relation 读取时，可以在 metadata 后执行；不存在依赖时才允许独立安排。
9. 不默认实现跨存储全局事务。若某一步输出不兼容旧运行时，且它与后续步骤之间存在会暴露混合状态的窗口，迁移计划必须在执行前拒绝该组合，或把这组紧耦合产物放入该组件已有的原子 generation 机制。不能先执行再依赖人工补救。
10. 每个步骤在结果报告中记录 completed、failed、not_required 和后置检查摘要。失败时命令返回非零，重跑只执行 `is_applied=false` 且依赖已满足的步骤。
11. migrate 启动前检查 RuntimeWriterLock。活动运行时存在时拒绝迁移，但不停止或修改正在运行的 A_Memorix。

建议的步骤描述最少包含下列结构，具体依赖由迁移版本定义：

```text
MigrationStep
  id
  depends_on
  precondition
  apply
  postcondition
  is_applied
  compatible_with_previous_runtime
  forbidden_markers_on_failure
```

### 9.3 历史数据与中断恢复

某个步骤已成功而后续步骤失败时，只有在该步骤明确声明兼容旧运行时的情况下，才允许保留结果并恢复旧运行时。不能用统一保留成功结果规则覆盖全部迁移。

不兼容旧运行时的紧耦合步骤必须在受控 generation 中完成，或保持维护状态等待幂等重跑。无论采用哪种方式，配置和 release 完成标记都只能在整个 DAG 的必要后置条件通过后写入。

重复调用发布命令时，先读取所有步骤的后置条件。已经完成的 metadata、向量和图步骤直接返回 `not_required`，不打开写连接、不刷新 `applied_at`、不调用 `graph_store.save()`。只有未完成步骤可以进入 `apply`。这与启动格式迁移共用的是幂等语义，不需要抽象出一套通用迁移框架。

### 9.4 验收条件

- dry-run 前后目标目录树完全一致。
- verify 前后数据库文件哈希、mtime 和 schema 完全一致。
- 测试至少覆盖 metadata 依赖向量、向量依赖 metadata、图仅依赖 relation 三种计划，证明顺序来自 DAG 而不是硬编码。
- 任一步骤失败后，配置和相关完成标记都没有提前写入。
- 兼容旧运行时的成功步骤可以幂等保留；不兼容步骤不会暴露给旧运行时。
- 活动运行时存在时迁移命令明确失败，主程序继续使用原数据。
- 对同一份已迁移数据连续执行两次 `migrate`，第二次所有已完成步骤均为 `not_required`，配置、数据库、向量和图文件的哈希、mtime、完成时间都不变化。
- schema 版本已是当前值但缺少必要结构时，命令报告完整性错误，不再次调用旧版本迁移函数。
## 10. 图快照已原子保存，但损坏识别和重建入口仍不完整

### 10.1 当前源码

图加载直接读取活动快照中的 NPZ：

```python
metadata_path, matrix_path, snapshot_active = self._resolve_snapshot_paths(data_dir)
metadata = _read_json_object(metadata_path)

if matrix_path.exists():
    self._adjacency = load_npz(str(matrix_path))
```

`has_data()` 只检查兼容根目录文件，没有跟随活动快照指针：

```python
def has_data(self) -> bool:
    if self.data_dir is None:
        return False
    return (self.data_dir / "graph_metadata.json").exists()
```

生命周期已经把图异常隔离为图通道不可用：

```python
except Exception as exc:
    self.graph_store = None
    self._set_runtime_capability("graph", False)
    logger.exception(f"[sdk] 图谱通道初始化失败，元数据与其他检索通道继续运行: {exc}")
```

### 10.2 修改方案

1. `has_data()` 跟随活动指针判断目标 metadata 是否存在。指针存在但目标损坏时仍返回存在，让 `load()` 暴露真实错误，而不是静默创建空图。
2. `load()` 保持严格读取，捕获 NPZ EOF、bad zip、缺失文件和矩阵维度异常后抛出包含 generation、metadata path、matrix path 的图完整性异常。
3. 生命周期继续使用现有图通道隔离，不把图损坏扩大为完整初始化失败。
4. 增加显式 `rebuild_graph_from_metadata` 维护入口，从活动 entities、relations 和 paragraph-relation 映射重建新快照。重建成功并完成自检后才激活。
5. 不自动激活旧 generation。旧图可能缺少较新的关系，自动回退会产生看似可用但时序错误的检索结果。

### 10.3 历史数据

metadata 中的实体和关系是重建来源。损坏图文件只影响图通道，段落、人物事实、稀疏检索和可用向量继续工作。无法从 metadata 重建的孤立图节点列入报告，不凭旧快照自动恢复。

### 10.4 验收条件

- 活动指针存在但根目录兼容文件缺失时，仍会加载活动快照。
- 零字节、截断和非法 NPZ 都会产生包含实际路径的错误状态。
- 图损坏后其他检索通道可用。
- 显式重建成功后图通道恢复，不需要重启 MaiBot 主程序。

## 11. WebUI 导入分类、factual 分类和 smart path 存在确定性问题

### 11.1 当前源码

WebUI 目前把公共导入策略默认设为 `auto`，并在高级参数中使用自由文本输入框：

文件：`dashboard/src/routes/resource/knowledge-base/hooks/useImportForm.ts`

```typescript
const [importCommonStrategyOverride, setImportCommonStrategyOverride] = useState('auto')
```

文件：`dashboard/src/routes/resource/knowledge-base/tabs/ImportTab.tsx`

```tsx
<div className="space-y-1">
  <Label>指定抽取策略</Label>
  <Input
    value={importCommonStrategyOverride}
    onChange={(event) => setImportCommonStrategyOverride(event.target.value)}
  />
</div>
```

这种交互不能帮助用户理解类别，也允许提交拼写错误或后端不支持的值。导入策略枚举目前只有 `auto`、`narrative`、`factual`、`quote`：

文件：`src/A_memorix/core/storage/knowledge_types.py`

```python
class ImportStrategy(str, Enum):
    AUTO = "auto"
    NARRATIVE = "narrative"
    FACTUAL = "factual"
    QUOTE = "quote"
```

自动分类还有一个独立问题。短文本只要包含是、有、在、为中的任一字符，就会被判为 factual：

文件：`src/A_memorix/core/storage/type_detection.py`

```python
_FACTUAL_MARKERS = [r"是", r"有", r"在", r"为", r"属于", r"位于", r"包含", r"拥有"]

factual_score = sum(1 for marker in _FACTUAL_MARKERS if re.search(r"\s*" + marker + r"\s*", text))
if factual_score <= 0:
    return False
if len(text) <= 240:
    return True
```

smart path 依赖空格分词，并把无向节点对和相对方向一起缓存：

文件：`src/A_memorix/core/utils/path_fallback_service.py`

```python
tokens = text.replace("?", " ").replace("!", " ").replace(".", " ").split()

cache_key = tuple(sorted((u, v)))
if cache_key in edge_cache:
    pred, direction = edge_cache[cache_key]
```

### 11.2 WebUI 导入类别必须由用户明确选择

WebUI 的导入主表单增加必选类别控件，不放在高级参数中，也不继续使用可输入任意字符串的 `Input`。可以使用 `Select` 或紧凑的分段控件，显示本地化名称，提交稳定值：

| 页面名称 | 页面值 | 后端载荷 |
| --- | --- | --- |
| 叙事资料 | `narrative` | `strategy_override=narrative`、`chat_log=false` |
| 事实资料 | `factual` | `strategy_override=factual`、`chat_log=false` |
| 语录与短句 | `quote` | `strategy_override=quote`、`chat_log=false` |
| 聊天记录 | `chat_log` | `strategy_override=narrative`、`chat_log=true` |

`chat_log` 只作为 WebUI 交互值使用，不扩充现有 `ImportStrategy`。现有聊天记录开关应并入类别选择，防止类别与开关形成冲突组合。

表单初始状态为空，未选择时禁用提交并显示简短的中文校验信息。只在 Dashboard 的 payload 构造层拒绝空值、`auto` 和未知值；共享后端入口继续接受现有 `auto` 和缺省语义，使旧版客户端、脚本及内部调用保持可用。服务端不新增调用来源字段，也不复制一套导入接口。

任务参数、任务详情和导入报告要保存稳定枚举值，并通过同一份本地化映射显示中文名称，不能直接向用户展示 `factual` 等内部值。历史任务中的 `auto` 仍可读取，界面显示为旧版自动分类，不要求改写历史记录。

### 11.3 自动分类的适用边界

WebUI 新导入不再用内容分类覆盖用户选择，也不增加预期类别一致性校验。内部调用、脚本和旧版客户端暂时保留 `auto`，以维持兼容性；这些入口的自动分类失败必须暴露为任务错误或可审计结果，不能悄悄替换用户已指定的策略。

自动分类规则作以下收敛：

1. 去掉单字符命中即 factual 的规则。短文本至少需要结构化三元组模式或两个较强事实词，无法可靠判断时返回 mixed。
2. `detect_knowledge_type()` 先识别具有明确对话、章节或叙事标记的 narrative，再执行 factual 判断，防止事实单字覆盖叙事证据。
3. 显式导入策略始终优先。自动分类结果只服务于 `auto` 入口，不能成为修改、重试和恢复历史任务的门槛。

### 11.4 smart path 修改方案

1. smart path 复用图检索已有的节点词典匹配能力，按查询位置做最长匹配，不再按空格切分中文。
2. 候选实体按文本位置保序，限制候选数和实体对数量。多于两个实体时在预算内尝试实体对，不直接返回空。
3. 路径关系缓存改用有向 key `(u, v)`，或缓存标准的 subject、predicate、object 后在每次展示时计算箭头。不要缓存相对于首次遍历方向的 `direction`。

### 11.5 历史数据

不批量重写已经落库的 `knowledge_type`。管理端允许用户修改历史段落类型；旧版 `auto` 任务继续按原记录展示。smart path 修复只改变查询结果，不修改图数据。

### 11.6 验收条件

- WebUI 未选择类别时不能提交，页面只显示中文类别名称。
- 四个页面类别分别映射到约定的 `strategy_override` 和 `chat_log`，任务详情使用相同的中文名称。
- Dashboard 中的空值、`auto` 和未知值不能创建任务，用户改选合法类别后可以正常提交。
- 内部调用和历史客户端继续可以使用 `auto`，不会因 WebUI 规则失去兼容性。
- 同时包含叙事标记和是、有、在、为的中文短文不会仅凭单字被判为 factual。
- 爱丽丝和鲍勃是什么关系能够提取两个中文节点。
- 三个及以上实体查询能在预算内产生路径候选。
- 同一有向边正向和反向出现在不同路径时，箭头方向分别正确。
## 12. 时间回填、简化迁移脚本和重复入口仍需收敛

### 12.1 当前源码

Web 时间回填重新打开一个 MetadataStore，并直接对活动目录写入：

```python
store = MetadataStore(data_dir=metadata_dir)
try:
    store.connect()
    summary = store.backfill_temporal_metadata_from_created_at(
        limit=limit,
        dry_run=dry_run,
        no_created_fallback=no_created_fallback,
    )
finally:
    store.close()
```

两个简化迁移脚本仍使用全表 `fetchall()`：

```python
rows = conn.execute(sql).fetchall()
```

同时还保留 `migrate_chat_history.py`、`migrate_person_memory_points.py` 和 `migrate_maibot_memory.py` 三个相近入口。

### 12.2 修改方案

1. Web 时间回填直接复用活动内核的 `metadata_store`，在该 Store 的受管事务中执行，不重新创建活动目录连接。
2. 独立时间回填脚本执行前获取 RuntimeWriterLock。锁被活动运行时持有时直接退出，不尝试绕过。
3. 两个简化脚本改为薄入口：解析旧参数后调用权威迁移实现，或明确标记废弃并输出替代命令。不要继续维护独立的数据选择和写入逻辑。
4. 必须暂时保留的读取使用 `fetchmany()`，批次大小可配置，单批提交并输出坏行报告。
5. 这一收敛不改变问题 5 中 MaiBot 增量迁移的 checkpoint、来源指纹和跨存储提交语义，只删除重复入口。

### 12.3 历史数据

旧命令名可以保留一段兼容期，但只能转发到权威实现。已有 checkpoint 和报告格式由权威迁移器读取，不创建第二套状态文件。

### 12.4 验收条件

- Web 时间回填不会打开第二个活动 MetadataStore。
- 活动运行时存在时，独立脚本拒绝写入且不影响主程序。
- 大表迁移内存占用随批次大小变化，不随全表行数线性增长。
- 三个入口不会再产生不同的数据选择和统计语义。

## 13. 不可达自动迁移和重复生命周期属于后续清理

### 13.1 当前源码

双向量自动迁移入口被正式禁用，但其状态和任务实现仍保留：

```python
def _should_start_dual_vector_auto_migration(self) -> bool:
    # 历史数据不得因启动自动迁移而重新调用 embedding。
    return False
```

生产主路径使用 `runtime_lifecycle_service.py`，旧 `lifecycle_orchestrator.py` 目前只被 `scripts/runtime_self_check.py` 直接引用。

### 13.2 修改方案

1. 接受现有策略：禁止启动时自动重嵌入，只保留显式 `rebuild_all_vectors` 和无重嵌入故障恢复。
2. 删除或隐藏不会启动的自动迁移进度状态、后台循环和 Web 展示，避免用户等待一个永远不会运行的任务。
3. 将 `runtime_self_check.py` 改为调用 SDK 主生命周期或抽取只读检查函数。完成后把旧 orchestrator 收缩成薄适配器，再评估删除。
4. 该项不引入新的迁移控制器、状态机或兼容层，优先级低于前述正确性问题。

### 13.3 验收条件

- 产品界面不再展示不可达的自动迁移状态。
- runtime self-check 与生产初始化使用同一组存储检查规则。
- 生命周期初始化、能力降级和关闭顺序只有一处权威实现。

## 14. 建议实施顺序

建议按依赖和风险分为五批，不要求任何一批成为 A_Memorix 启动前置条件。LPMM 的 generation 激活是整体替换的专用协议，不作为其他批次的默认实现。

### 第一批：导入语义、可见性和事实权威性

1. WebUI 必选本地化导入类别，并完成页面值到现有后端枚举的映射
2. Web 导入范围模型与检索前置过滤
3. 人物事实的证据来源、来源权威性和生命周期分离
4. 内部 ID 与普通实体分离

### 第二批：错误传播与任务资源边界

1. Web 文件任务终态、取消上下文和 aggregate 取消传播
2. Episode 输入输出预算、完整性协议和 fallback provenance
3. 图损坏状态和显式重建入口

### 第三批：启动转换与向量恢复

1. 启动格式迁移的只读探测、无任务零写入、完成记录只写一次和组件级失败隔离
2. V1 向量 pair 与 metadata 的一次性对账、可证明记录恢复和恢复阶段收敛
3. 真正跨 Store 转换的 generation 清单、引用覆盖校验和受限 pickle 读取边界
4. LPMM 空输出 generation、完整 manifest、原子活动指针和显式回退

### 第四批：发布迁移和离线工具安全性

1. 发布迁移 dry-run、verify、步骤级 `is_applied`、依赖 DAG 和配置最终提交
2. 时间回填复用活动 Store
3. 简化脚本转发权威入口

### 第五批：检索质量与结构清理

1. 非 Web `auto` 入口的 factual 自动分类修正
2. 中文、多实体 smart path 与方向缓存修正
3. 不可达自动迁移和重复生命周期清理

## 15. 明确不采用的设计

- 不给所有迁移统一增加完整数据目录 staging、generation manifest 或活动指针。
- 不为普通 schema、格式修复和增量写入维持长期双版本 Store。LPMM 同时替换三类 Store，使用 generation 活动指针属于这一受控整体替换的例外。
- 不把 V1 单个 VectorStore 的 pair 对账扩展为跨 Store generation，也不因可解释的 metadata 滞后隔离整个向量根目录、重置全部投影状态。
- 不在每次启动时刷新迁移完成记录，也不为启动迁移和发布迁移建立新的通用编排框架；两处分别实现最小的只读 `is_applied` 判断。
- 不在图损坏时自动加载旧 generation。
- 不通过十六进制外观拒绝实体名称和关系端点。
- 不对 WebUI 导入内容执行预期类别一致性校验，也不把校验错误转换为默认类别后继续写入。
- 不把旧版 `user_supported` metadata 仅凭引用字段自动升级为 `direct_user` 稳定事实。
- 不让 verify、dry-run 或后台审计修改目标数据。
- 不把用户协作式取消和运行时 `CancelledError` 合并为一个布尔状态。
- 不因 Episode、图、向量池或单个迁移任务失败而隐去错误；应保留错误状态并隔离影响通道。

## 16. 文档与测试更新范围

实施时只为实际行为变化补充对应测试，不建立单独的通用迁移测试框架：

- Dashboard 与宿主接入：导入类别必选、四类中文名称与 payload 映射、非法或旧版 `auto` 请求、导入范围 payload、真实聊天流选择、历史全局导入可见性。
- 人物事实：证据消息存在性、聊天归属、发送者身份和 claim 主体；硬删除后不存在的消息不能作为证据，宿主将来提供撤回或失效状态时验证其联动；第三方陈述不能获得 `direct_user`；旧版 `user_supported` 不会未经完整核验自动升级。
- 启动格式迁移：无旧数据的全新目录不生成延迟完成记录；已有完成记录的第二次启动不更新数据库、WAL、mtime、`applied_at` 或报告摘要；重新出现旧文件时报告异常状态，不覆盖首次审计记录；多备份倒序尝试、manifest checksum、不同 generation 混合拒绝、当前 metadata 引用覆盖率、受限 pickle 类型拒绝、单组件失败不阻止其他通道初始化。
- V1 向量升级：正常关闭、pair 比 metadata 更新、metadata 比 pair 更新三种旧数据；只恢复能映射到当前活动记录的唯一 ID；孤儿、重复和碰撞不导入；缺失记录进入现有回填；可解释的不一致不隔离整个向量根目录；第二次启动只走 V2 校验；旧版 `completed + copy pending` journal 按 `copying` 继续且不会重做已完成批次。
- LPMM：非空目标拒绝、跨文件系统拒绝、切换前失败继续使用旧 generation、`ACTIVE` 原子替换、切换后初始化失败记录和显式回退。
- 发布迁移：dry-run 无副作用、verify 只读、配置最后提交、metadata 依赖向量、向量依赖 metadata、图仅依赖 relation 三类 DAG、中断后幂等重跑；对已完成数据第二次执行时所有步骤为 `not_required`，配置、数据库、向量、图文件及完成时间均不变化。
- 协程取消：用户请求、运行时关闭、父任务取消、并发取消、迟到取消、aggregate 传播和有时限的 shield 状态清理。
- Episode：输入字符和段落数上限、输出上限、未知或重复 hash、全部 discarded、字段长度、fallback 持久 provenance 和 Provider child 清理。
- 其他 `pytests/A_memorix_test`：内部 ID 不生成实体、图 NPZ 损坏和重建、中文 smart path 多实体与方向缓存。

配置项若实际增加，只修改配置模板、`config_schema.json` 和配置参考文档，并按项目规则提升配置模板版本；不直接修改实际 `bot_config.toml` 或 `model_config.toml`，也不新增 ConfigUpgradeHook。

## 17. 状态机关联审查后的实施前修订

本节依据 2026-08-03 对当前源码的再次检查形成。前文保留了问题发现和方案演进过程；如果第 5、6、7、9、10、13、14、15、16 节与本节存在冲突，实施时以本节为准。本节不扩大修复范围，也不要求把所有迁移统一成 generation、双版本或长期只读降级。

### 17.1 状态收敛边界

状态重复不能只按字段名称判断。下列状态保护的提交边界不同，必须继续独立存在：

- VectorStore 的 append journal、compaction journal 和 V2 `binary_commit`，负责单个向量池的 pair 与 metadata 崩溃一致性。
- GraphStore 的 `graph_snapshot.json`，负责单个图快照的磁盘激活。
- MetadataStore 的 schema version 和各 durable job 状态，负责数据库结构与后台投影任务。
- LPMM 的 ACTIVE，负责 metadata、vectors、graph 三类 Store 的整体选择。
- 宿主 runtime state，负责请求门控；内核 capability 和 `vector_health` 负责表达各通道当前是否可用。
- `dual_ready.json` 是磁盘提交证据，`_dual_vector_pools_ready` 表示本次运行时是否已经成功加载对应 Store 对象，二者不能简单删除其一。

可以收敛的是同一工作流内重复表达相同阶段的字段。恢复 journal 不应同时由 `stage` 和 `copy.state` 决定生命周期；Web Task、File、Chunk 则属于不同粒度，不能合并成一个状态对象。

### 17.2 LPMM 激活必须脱离导入 worker

当前 `_process_lpmm_convert()` 在 ImportTaskManager worker 内执行，而内核 `shutdown()` 会停止并等待这个 manager。如果转换 worker 直接调用宿主关闭或激活，会取消或等待自己。转换与激活必须分成两个执行边界：

1. 导入 worker 只生成 candidate generation、完成 Store 打开测试、checksum 和跨 Store 引用校验，并原子写入终态报告。
2. worker 从 `_run_task()` 返回，`_active_task_id` 清除并完成临时文件处理后，才通知宿主维护协调器。不能在当前 `_notify_write_changed()` 的 await 链内直接激活。
3. 宿主协调器负责请求门控、任务退出、Store 关闭、ACTIVE 切换、重新初始化和健康检查。
4. ImportTaskManager 重建后，`list_tasks()` 和 `get_task()` 应能只读返回宿主级目录中的终态报告。报告不是执行状态权威，不能用于续跑操作；报告写入必须使用临时文件、刷新和 `os.replace()`，不能继续直接 `write_text()`。

### 17.3 ACTIVE 使用最小两阶段状态并自动回退

只原子替换一个 generation ID 无法识别指针已切换但健康检查尚未提交的崩溃窗口。ACTIVE 本身承担唯一的激活事务状态，不再另建 activation journal：

```json
{
  "current_generation": "gen-new",
  "previous_generation": "legacy-root",
  "activation_state": "provisional",
  "operation_id": "唯一操作 ID"
}
```

激活顺序修订为：

1. candidate 的完整校验全部在维护状态之前完成。
2. 宿主关闭新请求入口，写请求进入宿主级启动队列，已有请求按计数器排空。等待超时时在指针切换前放弃激活并恢复旧运行态，不强行关闭仍被读取的 Store。
3. 宿主持有基准数据目录的 RuntimeWriterLock 租约，锁在旧内核关闭、ACTIVE 切换和新内核初始化之间不得释放。SDK 独立运行时仍可自行持锁，宿主模式通过显式 lease 注入，不能让锁路径随 generation 变化。
4. 原子写入 `activation_state=provisional` 的 ACTIVE，初始化新 generation 并执行最小健康检查。在 provisional 阶段不得开放直接写入。
5. 健康检查成功后原子写入 `activation_state=committed`，排空维护期间已接收的写入队列，再开放直接请求。
6. 健康检查失败时，在仍未开放写入的条件下自动恢复 previous 指针并初始化旧 generation。旧 generation 恢复成功后直接回到 ready，不要求用户做没有信息增量的人工确认。
7. 进程启动发现 provisional 时执行同一套自动判定。新 generation 健康则提交；否则自动恢复 previous。新旧两边都失败时只把 A_Memorix 标记为不可用，MaiBot 主程序继续运行并完整显示错误。
8. ACTIVE 已 committed 且服务已经开放写入后不得自动回退，因为回退会丢失激活后的新增数据。此时的问题不再属于本次激活事务。

generation manifest 的 checksum 只是激活时完整性基线。活动 generation 后续会正常增量写入，启动时不能拿旧 checksum 当成当前实时文件证明；实时一致性继续由各 Store 自身的 commit、journal 和结构校验负责。

### 17.4 路径解析分为宿主级和数据集级

不能把 `SDKMemoryKernel.data_dir` 整体替换为 generation 路径，否则写者锁、任务报告和启动队列也会随 generation 漂移。路径解析器至少返回 `base_data_dir` 和 `active_store_root`：

- 宿主级：ACTIVE、generations、RuntimeWriterLock、启动写入队列、Web 任务报告、LPMM staging 和 candidate 管理。
- 数据集级：metadata、vectors、graph、`dual_ready.json`、格式迁移记录、向量恢复 journal、向量隔离目录和 `import_manifest.json`。

`import_manifest.json` 属于当前数据集。若把它固定在宿主根目录，新 generation 会继承旧数据集的导入去重结论，可能跳过本应重新导入的内容。向量恢复 journal 也必须带 generation 归属，不能让旧 generation 的未完成恢复作用于新数据集。

运行时应在取得基准目录写者锁后解析并固定 `active_store_root`，本次内核生命周期内不得动态跟随 ACTIVE 变化。`runtime_lifecycle_service.py`、`dual_vector_state_service.py`、`vector_recovery_service.py`、`format_migration.py`、Web temporal backfill 以及所有离线脚本必须使用同一解析器。需要覆盖的脚本至少包括：

- `release_vnext_migrate.py`
- `migrate_maibot_memory.py`
- `backfill_relation_vectors.py`
- `audit_vector_consistency.py`
- `rebuild_episodes.py`
- `process_knowledge.py`
- `runtime_self_check.py`

GraphStore 当前通过 `graph_dir.parent / metadata / metadata.db` 获取边映射数据库；只要 graph 和 metadata 保持为同一 active_store_root 下的兄弟目录，该约束仍然成立。

### 17.5 宿主门控、在途请求和启动写入队列

宿主目前只检查 `_runtime_state`，没有记录已经取得 kernel 引用的请求。新增维护状态时不需要再建持久状态机，只增加短时请求门、在途计数器和 condition：

- 新请求在 ready 状态登记 lease，结束时释放。
- 进入 activating 前先关闭登记入口，再等待旧 lease 清零。
- search、profile 和 stats 在维护期间返回现有受控不可用结果；ingest_summary 和 ingest_text 写入宿主级队列。
- `starting`、`migrating`、`activating` 和 `activation_recovering` 都属于可排队状态，不能被 `_unavailable_response()` 误判为初始化失败。

当前启动流程在 `_replay_startup_write_queue()` 前已经发布 ready，会让后到的新写入先于旧队列执行。修复后必须保持门关闭，循环回放已经接收的记录，并在队列锁和运行时状态交接完成后再发布 ready。队列回放失败的记录继续保留明确错误并等待显式或下次重试，不能因单条失败阻止其他记录和主程序。

### 17.6 向量恢复只保留一个权威 phase

恢复 journal 修订为：

```text
prepared
quarantined
metadata_reset
new_generation_ready
copying
completed
failed
```

`copy` 只保存 `cursor`、`processed`、`copied`、`total`、`mode` 和时间戳，不再保存另一套 `state`。`mode=skipped` 表示没有可信旧向量可复制，工作流可以完成，但健康状态仍按可信覆盖率显示 degraded。复制异常写入 `phase=failed`、`failed_phase` 和 `last_error`，其他记忆通道继续运行。

旧 journal 的 `completed + copy pending/running` 通过一个 `effective_recovery_phase()` 解释为 copying。恢复续跑、新故障处理、隔离清理和状态输出都必须调用该函数。特别是 `_recover_known_vector_failure()` 不能再直接根据原始 `stage=completed` 创建新 operation，否则会把未完成续跑误当成新故障再次隔离。

`vector_health`、`recovery_stage` 和 `copy_progress` 保留为 Web API 兼容投影，但不独立推进状态。journal 写入后统一更新内存投影。普通 paragraph backfill 与可信旧向量复制不应同时作为两个后台批任务争用同一 paragraph pool；恢复 copying 期间暂停普通自动 backfill，但正常在线写入仍可按 VectorStore 现有锁和幂等 ID 规则执行。

### 17.7 V1 对账不丢弃原始证据，也不自动重嵌入

第 5.4 节修订为以下规则：

1. 所有进入 V2 的记录重新核对当前 MetadataStore。已删除 paragraph、失效 relation、tombstone、错误资源类型和当前已不存在的对象不能因 V1 metadata 与 pair 同时存在就直接保留。
2. 按目标向量池和资源类型分别映射。paragraph pool 只接受 paragraph；graph pool 分别处理 entity 和 relation，并写入现有类型化目标 ID。旧单池中同一无类型原始 ID 同时对应多个资源类型时无法证明归属，仍应隔离。
3. `unresolved_orphan`、重复 ID、碰撞、无效 tombstone 和其他被排除行的原始 ID、pair 行号、向量内容、dimension、dtype、来源 checksum 与排除原因完整写入只读证据目录。该目录与常规向量恢复隔离目录分开，不参与七天清理；升级成功后也不得随事务临时备份删除。
4. V1 metadata 中存在但 pair 缺失的记录标记为 `missing_requires_explicit_rebuild`。不能写入现有自动 backfill 队列；只有显式 `rebuild_all_vectors` 才重新调用 embedding。
5. pair 重写复用 VectorStore 现有 compaction journal、只读备份和 V2 metadata 提交点。先持久化隔离证据，再准备两个 pair 临时文件和 journal；pair 替换、刷新完成后才写 V2 metadata。重启根据 journal 与 transaction ID 回滚或完成，不新增 V1 专用事务状态机。
6. 无法安全完成对账时保留原文件并关闭对应向量通道，metadata、稀疏检索、图和其他向量池继续运行。

### 17.8 Web 导入状态不做全面枚举重构

Task、File、Chunk 是不同粒度。多个文件可能同时处于 extracting、writing 和 completed，任务级不存在唯一业务 phase。问题 7 的修复不应顺带重构全部公开枚举：

- 保留当前 `status`、`current_step` 和 `step` JSON 字段，避免 Dashboard、重试判断、写入阻塞和历史报告同时失效。
- 集中修复 `gather()` 异常归属、所有子项终态覆盖、迟到取消和 `CancelledError` 传播。
- 现有 `ChunkState.failed_at` 继续保存失败前的真实 chunk 阶段，不能因终态写成 failed 而丢失。本轮不为没有该字段的 Task、File 复制同名状态。
- 任务级 `current_step` 可以继续显示 running 或由子项只读聚合，不能持久化一个虚假的唯一文件阶段。

多条不可变取消事件和独立 `terminal_cancellation` 偏重，取消上下文改为最小正交字段：

```text
cancel_requested_at
cancel_origin = user_request | runtime_shutdown | parent_cancel
```

用户取消仍通过 `status=cancel_requested` 协作检查。manager 的 `_stopping` 在捕获 `CancelledError` 时优先判定 runtime_shutdown；否则属于 parent_cancel，必须在短时有界 shield 中原子写入最少终态报告后重新抛出。已经完成后到达的用户取消不修改结果，也不写入执行状态历史。

### 17.9 发布迁移不建立持久化总状态机

当前配置不存在可作为 release 提交证据的独立版本字段。配置最后写表示新运行时依赖的配置输出最后落盘，不表示配置本身是迁移完成状态。

- 使用每个发布版本内的静态步骤描述和拓扑校验即可，不建设通用迁移编排框架。
- `completed`、`failed`、`not_required` 只存在于本次命令报告。
- 是否重跑由各组件语义后置条件决定。schema version 只能作为条件之一，不能掩盖缺列、缺索引、未完成回填和引用错误。
- 整体完成是所有必要后置条件与目标配置值的合取，不另写 release completion marker。
- 配置文件最后写入也必须使用同目录临时文件、刷新和 `os.replace()`，不能继续直接 `write_text()`。
- DAG 在任何写入前检查未知依赖和环。文档中的 metadata 依赖向量、向量依赖 metadata 是不同迁移规格的测试案例，不能组合成同一个循环计划。
- 命令在取得基准数据目录 RuntimeWriterLock 后解析 active_store_root。活动运行时存在时明确退出，不修改正在运行的 A_Memorix。

### 17.10 图重建、双池状态和生命周期清理

图重建不新增另一套 activation 状态。磁盘提交继续复用 GraphStore 的快照 generation；运行时完成条件则必须覆盖所有旧引用持有者：`graph_store`、retriever、relation write service、person profile service、summary importer、runtime bundle、缓存和 graph capability。

新图应在独立 GraphStore 中构建和校验。最终切换复用现有 relation graph projection lock 和宿主短时请求门，先替换运行时引用并刷新依赖，再更新 capability，旧 Store 在没有在途读者后释放。构建期间 metadata 若继续变化，应复用现有 projection job 和 revision 校验进行追平，不能用第二套图迁移任务状态。这样可以不重启 MaiBot，也不会让查询看到 `clear()` 到 `add_edges()` 之间的半成品。

不可达的 dual vector auto migration 状态、后台循环和 Dashboard 展示可以删除，但必须同时处理 SDK 字段、包装方法、显式 rebuild 中当前无效的进度回调、API 类型、组件测试和 Dashboard 测试。`_dual_vector_pools_ready`、`dual_ready.json` 及 search runtime 对它们的读取继续保留。

旧 `lifecycle_orchestrator.py` 不能在所有场景直接替换为生产初始化：

- runtime self-check 使用隔离临时目录时，可以实例化真实 SDK，走生产初始化和关闭流程。
- `--use-config-data-dir` 面向现有数据时只能执行只读 Store 探测和 embedding adapter 自检，不能借自检触发格式迁移、schema 写入、索引预热、后台任务或持久化。
- 完成两种模式拆分后，再把旧 orchestrator 收缩成薄适配器或删除。宿主 runtime state 与内核 `_initialized`、capability 分属请求路由和组件可用性，不应合并。

### 17.11 修订后的验收补充

- LPMM conversion worker 退出后才进入宿主激活；不存在 manager 等待自身或任务查询在重建后消失。
- 进程在 provisional ACTIVE 的每个持久化点退出，重启都能自动提交健康 candidate 或自动恢复 previous，且未开放写入。
- 激活前在途请求超时会取消本次切换并继续使用旧 generation，不关闭仍被读取的 Store。
- 写者锁在旧内核关闭到新内核 ready 之间始终由同一基准目录 lease 持有。
- 维护期排队写入全部回放后才发布 ready，不出现新直接写入越过旧队列。
- 在线运行时和全部离线脚本解析到相同 active_store_root；宿主级报告、队列和锁不随 generation 漂移。
- 旧向量恢复只有一个 phase 权威字段；`completed + pending` 不创建第二个 operation；复制失败有明确 failed 状态但不关闭其他通道。
- V1 被排除向量可以从只读隔离文件完整复核，缺失向量不会由启动后台任务自动重新嵌入。
- Web 公开字段保持兼容，用户取消、运行时关闭和父任务取消得到正确来源，真实取消不会被 worker 吞掉。
- 发布迁移第二次运行只根据后置条件返回 not_required，不刷新配置、数据库、向量、图和完成时间。
- 图重建期间查询不会看到半成品，激活后所有运行时依赖引用同一新 GraphStore。
