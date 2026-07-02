# A_Memorix 配置参考 (v2.0.0)

本文档对应当前仓库代码（`__version__ = 2.0.0`、`SCHEMA_VERSION = 10`）。

说明：

- 本文只覆盖 **当前运行时实际读取** 的配置键。
- 默认配置文件路径为 `config/a_memorix.toml`。
- 旧版 `/query`、`/memory`、`/visualize` 命令体系相关配置，不再作为主路径说明。
- 未配置的键会回退到代码默认值。
- 长期记忆控制台已可视化高频常用字段；未展示的长尾高级项仍然有效，请通过“源码模式 / 原始 TOML”编辑。

## 常用完整配置

```toml
[plugin]
enabled = true

[storage]
data_dir = "data/a-memorix"

[embedding]
model_name = "auto"
dimension = 1024
dimension_request_mode = "explicit"
batch_size = 32
max_concurrent = 5
enable_cache = false
quantization_type = "int8"

[embedding.fallback]
enabled = true
probe_interval_seconds = 180
allow_metadata_only_write = true

[embedding.paragraph_vector_backfill]
enabled = true
interval_seconds = 60
batch_size = 64
max_retry = 5

[retrieval]
top_k_paragraphs = 20
top_k_relations = 10
top_k_final = 10
alpha = 0.5
enable_ppr = true
ppr_alpha = 0.85
ppr_timeout_seconds = 1.5
ppr_concurrency_limit = 4
enable_parallel = true

[retrieval.sparse]
enabled = true
backend = "fts5"
mode = "auto"
tokenizer_mode = "jieba"
candidate_k = 80
relation_candidate_k = 60

[threshold]
min_threshold = 0.3
max_threshold = 0.95
percentile = 75.0
min_results = 3
enable_auto_adjust = true

[filter]
enabled = true
mode = "blacklist"
chats = []

[episode]
enabled = true
generation_enabled = true
pending_batch_size = 50
pending_max_retry = 3
max_paragraphs_per_call = 20
max_chars_per_call = 6000
source_time_window_hours = 24
segmentation_model = "auto"

[person_profile]
enabled = true
refresh_interval_minutes = 30
active_window_hours = 72
max_refresh_per_cycle = 50
top_k_evidence = 12
evidence_classification_max_tokens = 1200
evidence_classification_temperature = 0.1

[memory]
enabled = true
half_life_hours = 24.0
prune_threshold = 0.1
freeze_duration_hours = 24.0

[advanced]
enable_auto_save = true
auto_save_interval_minutes = 5
debug = false

[web.import]
enabled = true
max_queue_size = 20
max_files_per_task = 200
max_file_size_mb = 20
max_paste_chars = 200000
default_file_concurrency = 2
default_chunk_concurrency = 4
default_narrative_window_size = 1600
default_narrative_overlap = 400
default_factual_target_size = 1200
max_chunk_chars = 3200

[web.import.timeout]
llm_call_seconds = 240
process_poll_seconds = 1
process_terminate_seconds = 5
process_kill_seconds = 3
convert_preflight_seconds = 20

[web.tuning]
enabled = true
max_queue_size = 8
poll_interval_ms = 1200
default_intensity = "standard"
default_objective = "precision_priority"
default_top_k_eval = 20
default_sample_size = 24
```

### 可视化与原始 TOML 的分工

- 长期记忆控制台：适合修改高频项，例如 embedding、检索、Episode、人物画像、导入与调优的常用开关。
- 原始 TOML：适合复制整份配置、批量调整参数，或修改未在可视化表单中展示的高级项。
- raw-only 高级项仍包括：`retrieval.search.relation_intent.*`、`retrieval.search.graph_recall.*`、`retrieval.search.posterior_graph.*`、`retrieval.aggregate.*`、`memory.orphan.*`、`advanced.extraction_model`、`web.import.llm_retry.*`、`web.import.timeout.*`、`web.import.path_aliases`、`web.import.convert.*`、`web.tuning.llm_retry.*`、`web.tuning.eval_query_timeout_seconds`。

## 1. 存储与嵌入

### `storage`

- `storage.data_dir` (当前配置模板默认 `data/a-memorix`)
: 数据目录。相对路径按 MaiBot 仓库根目录解析。

补充说明：

- 部分离线脚本若未显式覆盖路径，会回退到 `A_memorix.paths.default_data_dir()`（当前为 `data/plugins/a-dawn.a-memorix`）。
- 建议在运维侧统一目录策略，避免“控制台写入目录”和“脚本处理目录”不一致。

### `embedding`

- `embedding.model_name` (默认 `auto`)
: embedding 模型选择。
- `embedding.dimension` (默认 `1024`)
: 期望的向量维度，用于初始化新向量库、运行时自检和显式维度请求。
- `embedding.dimension_request_mode` (默认 `explicit`)
: 是否在 embedding 请求中携带维度参数。`explicit` 仅在调用方显式指定 `dimensions` 时携带；`always` 保持旧行为，默认 encode 也会向 OpenAI 传 `dimensions`、向 Gemini 传 `output_dimensionality`；`never` 始终不传，让模型返回自然维度。
- `embedding.batch_size` (默认 `32`)
- `embedding.max_concurrent` (默认 `5`)
- `embedding.enable_cache` (默认 `false`)
- `embedding.retry` (默认 `{}`)
: embedding 调用重试策略。
- `embedding.quantization_type`
: 当前主路径仅建议 `int8`。
- `embedding.fallback.enabled` (默认 `true`)
- `embedding.fallback.probe_interval_seconds` (默认 `180`)
- `embedding.fallback.allow_metadata_only_write` (默认 `true`)
- `embedding.paragraph_vector_backfill.enabled` (默认 `true`)
- `embedding.paragraph_vector_backfill.interval_seconds` (默认 `60`)
- `embedding.paragraph_vector_backfill.batch_size` (默认 `64`)
- `embedding.paragraph_vector_backfill.max_retry` (默认 `5`)

## 2. 检索

### `retrieval` 主键

- `retrieval.top_k_paragraphs` (默认 `20`)
- `retrieval.top_k_relations` (默认 `10`)
- `retrieval.top_k_final` (默认 `10`)
- `retrieval.alpha` (默认 `0.5`)
- `retrieval.enable_ppr` (默认 `true`)
- `retrieval.ppr_alpha` (默认 `0.85`)
- `retrieval.ppr_timeout_seconds` (默认 `1.5`)
- `retrieval.ppr_concurrency_limit` (默认 `4`)
- `retrieval.enable_parallel` (默认 `true`)
- `retrieval.relation_vectorization.enabled` (默认 `false`)
- `retrieval.relation_vectorization.backfill_enabled` (默认 `false`)
- `retrieval.relation_vectorization.write_on_import` (默认 `true`)
- `retrieval.vector_pools.mode` (默认 `"dual"`)
- `retrieval.vector_pools.graph_top_k` (默认 `40`)
- `retrieval.vector_pools.graph_weight` (默认 `0.15`)

### `retrieval.relation_vectorization` (`RelationVectorizationConfig`)

关系向量化默认关闭。开启 `enabled` 后，运行时会允许关系写入向量库；开启 `backfill_enabled` 后，后台维护流程会为已有关系补写向量；`write_on_import` 控制摘要导入、网页导入和迁移脚本写入关系时是否同步写入关系向量。

### `retrieval.vector_pools` (`VectorPoolsConfig`)

双向量池检索默认使用 `dual`：段落向量池只召回段落，图谱向量池召回 `entity:<hash>` 和 `relation:<hash>`，再映射回支撑段落并作为 evidence 参与排序。切换为 `single` 可保持单向量池行为。

常用键（默认值）：

- `mode = "dual"` (`single`/`dual`)
- `paragraph_top_k = 20`
- `graph_top_k = 40`
- `graph_expand_paragraph_k = 80`
- `relation_expand_per_hit = 5`
- `entity_expand_per_hit = 8`
- `relation_evidence_weight = 1.0`
- `entity_evidence_weight = 0.55`
- `semantic_weight = 0.65`
- `sparse_weight = 0.20`
- `graph_weight = 0.15`
- `relation_intent.graph_top_k = 80`
- `relation_intent.semantic_weight = 0.45`
- `relation_intent.sparse_weight = 0.15`
- `relation_intent.graph_weight = 0.40`
- `relation_intent.return_relation_items = false`

`relation_vectorization.enabled` 控制关系向量是否写入图谱池，`vector_pools.mode` 控制检索时是否使用双池。两者语义不同。

### `retrieval.sparse` (`SparseBM25Config`)

常用键（默认值）：

- `enabled = true`
- `backend = "fts5"`
- `lazy_load = true`
- `mode = "auto"` (`auto`/`fallback_only`/`hybrid`)
- 运行时若 embedding 进入 degraded，会强制按 `fallback_only` 执行读路径（不改用户配置文件）
- `tokenizer_mode = "jieba"` (`jieba`/`mixed`/`char_2gram`)
- `char_ngram_n = 2`
- `candidate_k = 80`
- `relation_candidate_k = 60`
- `enable_ngram_fallback_index = true`
- `enable_relation_sparse_fallback = true`

### `retrieval.fusion` (`FusionConfig`)

- `method` (默认 `weighted_rrf`)
- `rrf_k` (默认 `60`)
- `vector_weight` (默认 `0.7`)
- `bm25_weight` (默认 `0.3`)
- `normalize_score` (默认 `true`)
- `normalize_method` (默认 `minmax`)

### `retrieval.search.relation_intent` (`RelationIntentConfig`)

- `enabled` (默认 `true`)
- `alpha_override` (默认 `0.35`)
- `relation_candidate_multiplier` (默认 `4`)
- `preserve_top_relations` (默认 `3`)
- `force_relation_sparse` (默认 `true`)
- `pair_predicate_rerank_enabled` (默认 `true`)
- `pair_predicate_limit` (默认 `3`)

### `retrieval.search.graph_recall` (`GraphRelationRecallConfig`)

- `enabled` (默认 `true`)
- `candidate_k` (默认 `24`)
- `max_hop` (默认 `1`)
- `allow_two_hop_pair` (默认 `true`)
- `max_paths` (默认 `4`)

### `retrieval.search.posterior_graph` (`PosteriorGraphConfig`)

- `enabled` (默认 `true`)
- `drop_ratio` (默认 `0.15`)
- `min_core_results` (默认 `2`)
- `max_graph_slots` (默认 `2`)
- `gate_scan_top_k` (默认 `5`)
- `grounded_confidence_threshold` (默认 `0.48`)
- `incidental_confidence_threshold` (默认 `0.22`)
- `min_query_token_coverage` (默认 `0.78`)
- `incidental_query_relevance_threshold` (默认 `0.68`)
- `incidental_core_overlap_threshold` (默认 `0.34`)
- `incidental_specificity_threshold` (默认 `0.42`)

说明：

- 这组配置控制“后验图补位”，即先跑正常双路检索，再判断是否需要从图结构补一小批 relation 候选进入尾部竞争。
- 设计目标以 `recall` 为主，而不是强行把 relation 顶到第一名。
- 如果你的最终回答仍会经过 LLM 汇总，这组能力更适合用于“保证证据进入前排候选”，而不是做激进排序改写。

### `retrieval.aggregate`

- `retrieval.aggregate.rrf_k`
- `retrieval.aggregate.weights`

用于聚合检索阶段混合策略；未配置时走代码默认行为。

## 3. 阈值过滤

### `threshold` (`ThresholdConfig`)

- `threshold.min_threshold` (默认 `0.3`)
- `threshold.max_threshold` (默认 `0.95`)
- `threshold.percentile` (默认 `75.0`)
- `threshold.std_multiplier` (默认 `1.5`)
- `threshold.min_results` (默认 `3`)
- `threshold.enable_auto_adjust` (默认 `true`)

## 4. 聊天过滤

### `filter`

用于 `respect_filter=true` 场景（检索和写入都支持）。

```toml
[filter]
enabled = true
mode = "blacklist" # blacklist / whitelist
chats = ["group:123", "user:456", "stream:abc"]

[filter.retrieval.chat_stream]
enabled = false
mode = "blacklist"
chats = []

[filter.retrieval.chat_summary]
enabled = false
mode = "blacklist"
chats = []

[filter.retrieval.episode]
enabled = false
mode = "blacklist"
chats = []
```

规则：

- `blacklist`：命中列表即拒绝
- `whitelist`：仅列表内允许
- 列表为空时：
  - `blacklist` => 全允许
  - `whitelist` => 全拒绝
- `chats` 支持 `group:<group_id>`、`user:<user_id>`、`private:<user_id>`、
  `stream:<session_id>`；裸字符串会匹配 stream/group/user 任一 token。
- `filter.retrieval.*` 只在检索结果后置过滤阶段生效，不影响写入、聊天摘要生成、
  Episode 生成、人物画像刷新或画像快照。
- `chat_stream` 裁剪普通 paragraph/relation 命中；`chat_summary` 裁剪
  `source_type=chat_summary` 或 `source=chat_summary:<session_id>` 命中；
  `episode` 裁剪 Episode 命中。
- 人物画像当前保持全局聚合与缓存，不按群组隔离。

### `global_memory_sharing_enabled`

- 默认 `false`
- 关闭时，普通记忆查询只检索当前聊天流以及 `shared_memory_groups`
  配置出的同组聊天流。
- 开启时，普通记忆查询会在所有聊天流范围内检索；
  `shared_memory_groups` 会保留配置，但不再限制普通查询范围。

### `shared_memory_groups`

用于配置多个聊天流共享同一长期记忆检索范围。写入仍保留原始
`chat_id`，只在检索时把当前聊天流扩展为同组允许范围。

```toml
[[shared_memory_groups]]

[[shared_memory_groups.targets]]
platform = "qq"
item_id = "123"
rule_type = "group"

[[shared_memory_groups.targets]]
platform = "qq"
item_id = "456"
rule_type = "group"
```

注意：

- `filter.whitelist` 只控制哪些聊天流允许读写记忆。
- `filter.retrieval.*` 只裁剪已经召回的检索结果，不会扩大检索范围。
- `shared_memory_groups` 才控制哪些聊天流互相共享检索范围。
- 成员会解析为系统已知的真实聊天流 ID；解析不到的目标不会生效。

## 5. Episode

### `episode`

- `episode.enabled` (默认 `true`)
- `episode.generation_enabled` (默认 `true`)
- `episode.pending_batch_size` (默认 `50`)
- `episode.pending_max_retry` (默认 `3`)
- `episode.max_paragraphs_per_call` (默认 `20`)
- `episode.max_chars_per_call` (默认 `6000`)
- `episode.source_time_window_hours` (默认 `24`)
- `episode.segmentation_model` (默认 `auto`)
: 支持 `auto`，也支持填写 `utils/replyer/planner/tool_use` 或具体模型名。

## 6. 人物画像

### `person_profile`

- `person_profile.enabled` (默认 `true`)
- `person_profile.refresh_interval_minutes` (默认 `30`)
- `person_profile.active_window_hours` (默认 `72`)
- `person_profile.max_refresh_per_cycle` (默认 `50`)
- `person_profile.top_k_evidence` (默认 `12`)
- `person_profile.evidence_classification_max_tokens` (默认 `1200`)
- `person_profile.evidence_classification_temperature` (默认 `0.1`)

## 7. 记忆演化与回收

### `memory`

- `memory.enabled` (默认 `true`)
- `memory.half_life_hours` (默认 `24.0`)
- `memory.base_decay_interval_hours` (默认 `1.0`)
- `memory.prune_threshold` (默认 `0.1`)
- `memory.freeze_duration_hours` (默认 `24.0`)

### `memory.orphan`

- `enable_soft_delete` (默认 `true`)
- `entity_retention_days` (默认 `7.0`)
- `paragraph_retention_days` (默认 `7.0`)
- `sweep_grace_hours` (默认 `24.0`)

## 8. 高级运行时

### `advanced`

- `advanced.enable_auto_save` (默认 `true`)
- `advanced.auto_save_interval_minutes` (默认 `5`)
- `advanced.debug` (默认 `false`)
- `advanced.extraction_model` (默认 `auto`)

## 9. 导入中心 (`web.import`)

### 开关与限流

- `web.import.enabled` (默认 `true`)
- `web.import.max_queue_size` (默认 `20`)
- `web.import.max_files_per_task` (默认 `200`)
- `web.import.max_file_size_mb` (默认 `20`)
- `web.import.max_paste_chars` (默认 `200000`)
- `web.import.default_file_concurrency` (默认 `2`)
- `web.import.default_chunk_concurrency` (默认 `4`)
- `web.import.default_narrative_window_size` (默认 `1600`)
- `web.import.default_narrative_overlap` (默认 `400`)
- `web.import.default_factual_target_size` (默认 `1200`)
- `web.import.max_chunk_chars` (默认 `3200`)
- `web.import.max_file_concurrency` (默认 `6`)
- `web.import.max_chunk_concurrency` (默认 `12`)
- `web.import.poll_interval_ms` (默认 `1000`)

### 超时

- `web.import.timeout.llm_call_seconds` (默认 `240`，`0` 表示不额外限制)
- `web.import.timeout.process_poll_seconds` (默认 `1`)
- `web.import.timeout.process_terminate_seconds` (默认 `5`)
- `web.import.timeout.process_kill_seconds` (默认 `3`)
- `web.import.timeout.convert_preflight_seconds` (默认 `20`)

### 重试与路径

- `web.import.llm_retry.max_attempts` (默认 `4`)
- `web.import.llm_retry.min_wait_seconds` (默认 `3`)
- `web.import.llm_retry.max_wait_seconds` (默认 `40`)
- `web.import.llm_retry.backoff_multiplier` (默认 `3`)
- `web.import.path_aliases` (默认内置 `raw/lpmm/plugin_data`)

### 转换阶段

- `web.import.convert.enable_staging_switch` (默认 `true`)
- `web.import.convert.keep_backup_count` (默认 `3`)

## 10. 调优中心 (`web.tuning`)

- `web.tuning.enabled` (默认 `true`)
- `web.tuning.max_queue_size` (默认 `8`)
- `web.tuning.poll_interval_ms` (默认 `1200`)
- `web.tuning.eval_query_timeout_seconds` (默认 `10.0`)
- `web.tuning.default_intensity` (默认 `standard`，可选 `quick/standard/deep`)
- `web.tuning.default_objective` (默认 `precision_priority`，可选 `precision_priority/balanced/recall_priority`)
- `web.tuning.default_top_k_eval` (默认 `20`)
- `web.tuning.default_sample_size` (默认 `24`)
- `web.tuning.llm_retry.max_attempts` (默认 `3`)
- `web.tuning.llm_retry.min_wait_seconds` (默认 `2`)
- `web.tuning.llm_retry.max_wait_seconds` (默认 `20`)
- `web.tuning.llm_retry.backoff_multiplier` (默认 `2`)

## 11. 兼容性提示

- 若你从 `1.x` 升级，请优先运行：

```bash
python src/A_memorix/scripts/release_vnext_migrate.py preflight --strict
python src/A_memorix/scripts/release_vnext_migrate.py migrate --verify-after
python src/A_memorix/scripts/release_vnext_migrate.py verify --strict
```

- 启动前再执行：

```bash
python src/A_memorix/scripts/runtime_self_check.py --json
```

以避免 embedding 维度与向量库不匹配导致运行时异常。
