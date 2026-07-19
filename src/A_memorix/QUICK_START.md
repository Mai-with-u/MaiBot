# A_Memorix Quick Start (v2.0.0)

本文档面向当前 `2.0.0` 架构（源码内长期记忆子系统 + SDK Tool 接口）。

## 0. 版本与接口变更

- 当前版本：`2.0.0`
- 接入形态：MaiBot 内置长期记忆子系统 + Tool 调用
- 旧版 slash 命令（如 `/query`、`/memory`、`/visualize`）不再作为本分支主文档入口

## 1. 环境准备

- Python 3.10+
- 与 MaiBot 主程序相同的运行环境
- 可访问你配置的 embedding 服务

安装依赖：

```bash
pip install -r src/A_memorix/requirements.txt --upgrade
```

如果当前目录就是 `src/A_memorix`，也可以：

```bash
pip install -r requirements.txt --upgrade
```

## 2. 配置子系统

当前分支固定使用 `config/a_memorix.toml` 作为 A_Memorix 配置文件。

推荐的配置入口有两种：

- 长期记忆控制台：适合修改常用高频项，适合日常运维与调优。
- 原始 TOML：适合批量复制配置或编辑长尾高级项。

常用完整示例：

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

[filter]
enabled = true
mode = "blacklist"
chats = []

[episode]
enabled = true
generation_enabled = true
source_poll_interval_seconds = 1
source_batch_size = 20
source_max_retry = 3  # 包含首次尝试，最小值为 1
source_lease_seconds = 1800
source_max_wait_seconds = 60
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

未出现在可视化配置页中的高级项，继续通过原始 TOML 维护，详见 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)。

## 3. 运行时自检（强烈建议）

先确认 embedding 实际输出维度与向量库兼容：

```bash
python src/A_memorix/scripts/runtime_self_check.py --json
```

如果结果 `ok=false`，先修复 embedding 配置或向量库，再继续导入。

## 4. 导入数据

### 4.1 文本批量导入

`process_knowledge.py` 当前默认扫描目录为：

```text
data/plugins/a-dawn.a-memorix/raw/
```

若你当前运行目录使用 `storage.data_dir = "data/a-memorix"`，建议先把文本同步到脚本默认目录再执行，避免导入目录与运行目录不一致。

执行：

```bash
python src/A_memorix/scripts/process_knowledge.py
```

常用参数：

```bash
python src/A_memorix/scripts/process_knowledge.py --force
python src/A_memorix/scripts/process_knowledge.py --chat-log
python src/A_memorix/scripts/process_knowledge.py --chat-log --chat-reference-time "2026/02/12 10:30"
```

### 4.2 其他导入脚本

```bash
python src/A_memorix/scripts/import_lpmm_json.py <json文件或目录>
python src/A_memorix/scripts/convert_lpmm.py -i <lpmm数据目录> -o data/a-memorix
python src/A_memorix/scripts/migrate_chat_history.py --help
python src/A_memorix/scripts/migrate_maibot_memory.py --help
python src/A_memorix/scripts/migrate_person_memory_points.py --help
```

## 5. 核心 Tool 调用

### 5.1 检索

```json
{
  "tool": "search_memory",
  "arguments": {
    "query": "项目复盘",
    "mode": "aggregate",
    "limit": 5,
    "chat_id": "group:dev"
  }
}
```

`mode` 支持：`search/time/hybrid/episode/aggregate`

严格语义说明：

- `semantic` 模式已移除，传入会返回参数错误。
- `time/hybrid` 模式必须提供 `time_start` 或 `time_end`，否则返回错误（不会再当作“未命中”）。

### 5.2 写入摘要

```json
{
  "tool": "ingest_summary",
  "arguments": {
    "external_id": "chat_summary:group-dev:2026-03-18",
    "chat_id": "group:dev",
    "text": "今天完成了检索调优评审"
  }
}
```

### 5.3 写入普通记忆

```json
{
  "tool": "ingest_text",
  "arguments": {
    "external_id": "note:2026-03-18:001",
    "source_type": "note",
    "text": "模型切换后召回质量更稳定",
    "chat_id": "group:dev",
    "tags": ["worklog"]
  }
}
```

### 5.4 画像与维护

```json
{
  "tool": "get_person_profile",
  "arguments": {
    "person_id": "Alice",
    "limit": 8
  }
}
```

```json
{
  "tool": "maintain_memory",
  "arguments": {
    "action": "protect",
    "target": "模型切换后召回质量更稳定",
    "hours": 24
  }
}
```

```json
{
  "tool": "memory_stats",
  "arguments": {}
}
```

## 6. 管理 Tool（进阶）

`2.0.0` 提供完整管理工具：

- `memory_graph_admin`
- `memory_source_admin`
- `memory_episode_admin`
- `memory_profile_admin`
- `memory_runtime_admin`
- `memory_import_admin`
- `memory_tuning_admin`
- `memory_v5_admin`
- `memory_delete_admin`

可先用 `action=list` / `action=status` 等只读动作验证链路。

## 7. 常见问题

### Q1: 检索为空

1. 先看 `memory_stats` 是否有段落/关系
2. 检查 `chat_id`、`person_id` 过滤条件是否过严
3. 运行 `runtime_self_check.py --json` 确认 embedding 维度无误
4. 若返回包含 `error` 字段，优先按错误提示修正 mode/时间参数

### Q2: 启动时报向量维度不一致

- 原因：现有向量库维度与当前 embedding 输出不一致
- 处理：恢复原配置或重建向量数据后再启动
- 若你希望继续强制请求 `embedding.dimension` 指定的输出维度，可将 `embedding.dimension_request_mode` 改为 `always`

### Q3: Web 页面打不开

本分支不内置独立 `server.py`。

- 常用配置项可直接通过主程序长期记忆控制台编辑。
- `web/index.html`、`web/import.html`、`web/tuning.html` 仅作为页面结构与行为参考。
- 正式入口由宿主侧 React 页面和 `/api/webui/memory/*` 接口承接。

## 8. 下一步

- 配置细节见 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)
- 导入细节见 [IMPORT_GUIDE.md](IMPORT_GUIDE.md)
- 版本历史见 [CHANGELOG.md](CHANGELOG.md)
