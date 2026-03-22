# A_Memorix 配置参考 (v2.0.0)

本文档对应当前仓库代码（`__version__ = 2.0.0`、`SCHEMA_VERSION = 8`）。

说明：

- 本文只覆盖 **当前运行时实际读取** 的配置键。
- 旧版 `/query`、`/memory`、`/visualize` 命令体系相关配置，不再作为主路径说明。
- 未配置的键会回退到代码默认值。

## 最小可用配置

```toml
[plugin]
enabled = true

[storage]
data_dir = "./data"

[embedding]
model_name = "auto"
dimension = 1024
batch_size = 32
max_concurrent = 5
enable_cache = false
quantization_type = "int8"

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

[episode]
enabled = true
generation_enabled = true
pending_batch_size = 20
pending_max_retry = 3

[person_profile]
enabled = true

[memory]
enabled = true
half_life_hours = 24.0
prune_threshold = 0.1

[advanced]
enable_auto_save = true
auto_save_interval_minutes = 5

[web.import]
enabled = true

[web.tuning]
enabled = true
```

## 1. 存储与嵌入

### `storage`

- `storage.data_dir` (默认 `./data`)
: 数据目录。相对路径按插件目录解析。

### `embedding`

- `embedding.model_name` (默认 `auto`)
: embedding 模型选择。
- `embedding.dimension` (默认 `1024`)
: 期望维度（运行时会做真实探测并校验）。
- `embedding.batch_size` (默认 `32`)
- `embedding.max_concurrent` (默认 `5`)
- `embedding.enable_cache` (默认 `false`)
- `embedding.retry` (默认 `{}`)
: embedding 调用重试策略。
- `embedding.quantization_type`
: 当前主路径仅建议 `int8`。

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

### `retrieval.sparse` (`SparseBM25Config`)

常用键（默认值）：

- `enabled = true`
- `backend = "fts5"`
- `lazy_load = true`
- `mode = "auto"` (`auto`/`fallback_only`/`hybrid`)
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
```

规则：

- `blacklist`：命中列表即拒绝
- `whitelist`：仅列表内允许
- 列表为空时：
  - `blacklist` => 全允许
  - `whitelist` => 全拒绝

## 5. Episode

### `episode`

- `episode.enabled` (默认 `true`)
- `episode.generation_enabled` (默认 `true`)
- `episode.pending_batch_size` (默认 `20`，部分路径默认 `12`)
- `episode.pending_max_retry` (默认 `3`)
- `episode.max_paragraphs_per_call` (默认 `20`)
- `episode.max_chars_per_call` (默认 `6000`)
- `episode.source_time_window_hours` (默认 `24`)
- `episode.segmentation_model` (默认 `auto`)

## 6. 人物画像

### `person_profile`

- `person_profile.enabled` (默认 `true`)
- `person_profile.refresh_interval_minutes` (默认 `30`)
- `person_profile.active_window_hours` (默认 `72`)
- `person_profile.max_refresh_per_cycle` (默认 `50`)
- `person_profile.top_k_evidence` (默认 `12`)

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
- `web.import.max_file_concurrency` (默认 `6`)
- `web.import.max_chunk_concurrency` (默认 `12`)
- `web.import.poll_interval_ms` (默认 `1000`)

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
- `web.tuning.default_intensity` (默认 `standard`)
- `web.tuning.default_objective` (默认 `precision_priority`)
- `web.tuning.default_top_k_eval` (默认 `20`)
- `web.tuning.default_sample_size` (默认 `24`)
- `web.tuning.llm_retry.max_attempts` (默认 `3`)
- `web.tuning.llm_retry.min_wait_seconds` (默认 `2`)
- `web.tuning.llm_retry.max_wait_seconds` (默认 `20`)
- `web.tuning.llm_retry.backoff_multiplier` (默认 `2`)

## 11. 兼容性提示

- 若你从 `1.x` 升级，请优先运行：

```bash
python plugins/A_memorix/scripts/release_vnext_migrate.py preflight --strict
python plugins/A_memorix/scripts/release_vnext_migrate.py migrate --verify-after
python plugins/A_memorix/scripts/release_vnext_migrate.py verify --strict
```

- 启动前再执行：

```bash
python plugins/A_memorix/scripts/runtime_self_check.py --json
```

以避免 embedding 维度与向量库不匹配导致运行时异常。
