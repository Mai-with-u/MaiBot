# A_Memorix

**长期记忆与认知增强插件** (v2.0.0)

> 消えていかない感覚 , まだまだ足りてないみたい !

A_Memorix 是面向 MaiBot SDK 的 `memory_provider` 插件。  
它把文本、关系、Episode、人物画像和检索调优统一在一套运行时里，适合长期运行的 Agent 记忆场景。

## 快速导航

- [快速入门](QUICK_START.md)
- [配置参数详解](CONFIG_REFERENCE.md)
- [导入指南与最佳实践](IMPORT_GUIDE.md)
- [更新日志](CHANGELOG.md)

## 2.0.0 版本定位

`v2.0.0` 是一次架构收敛版本，当前分支以 **SDK Tool 接口** 为主：

- 旧 `components/commands/*`、`components/tools/*` 与 `server.py` 已移除。
- 统一入口为 [`plugin.py`](plugin.py) + [`core/runtime/sdk_memory_kernel.py`](core/runtime/sdk_memory_kernel.py)。
- 元数据 schema 为 `v8`，新增外部引用与运维操作记录（如 `external_memory_refs`、`memory_v5_operations`、`delete_operations`）。

如果你还在使用旧版 slash 命令（如 `/query`、`/memory`、`/visualize`），需要按本文的 Tool 接口迁移。

## 核心能力

- 双路检索：向量 + 图谱关系联合召回，支持 `search/time/hybrid/episode/aggregate`。
- 写入与去重：`external_id` 幂等、段落/关系联合写入、Episode pending 队列处理。
- Episode 能力：按 source 重建、状态查询、批处理 pending。
- 人物画像：自动快照 + 手动 override。
- 管理能力：图谱、来源、Episode、画像、导入、调优、V5 运维、删除恢复全套管理工具。

## Tool 接口 (v2.0.0)

### 基础工具

| Tool | 说明 | 关键参数 |
| --- | --- | --- |
| `search_memory` | 检索长期记忆 | `query` `mode` `limit` `chat_id` `person_id` `time_start` `time_end` |
| `ingest_summary` | 写入聊天摘要 | `external_id` `chat_id` `text` |
| `ingest_text` | 写入普通文本记忆 | `external_id` `source_type` `text` |
| `get_person_profile` | 获取人物画像 | `person_id` `chat_id` `limit` |
| `maintain_memory` | 维护关系状态 | `action=reinforce/protect/restore/freeze/recycle_bin` |
| `memory_stats` | 获取统计信息 | 无 |

### 管理工具

| Tool | 常用 action |
| --- | --- |
| `memory_graph_admin` | `get_graph/create_node/delete_node/rename_node/create_edge/delete_edge/update_edge_weight` |
| `memory_source_admin` | `list/delete/batch_delete` |
| `memory_episode_admin` | `query/list/get/status/rebuild/process_pending` |
| `memory_profile_admin` | `query/list/set_override/delete_override` |
| `memory_runtime_admin` | `save/get_config/self_check/refresh_self_check/set_auto_save` |
| `memory_import_admin` | `settings/get_guide/create_upload/create_paste/create_raw_scan/create_lpmm_openie/create_lpmm_convert/create_temporal_backfill/create_maibot_migration/list/get/chunks/cancel/retry_failed` |
| `memory_tuning_admin` | `settings/get_profile/apply_profile/rollback_profile/export_profile/create_task/list_tasks/get_task/get_rounds/cancel/apply_best/get_report` |
| `memory_v5_admin` | `status/recycle_bin/restore/reinforce/weaken/remember_forever/forget` |
| `memory_delete_admin` | `preview/execute/restore/get_operation/list_operations/purge` |

### 检索模式语义（严格）

- `search_memory.mode` 仅支持：`search/time/hybrid/episode/aggregate`。
- `semantic` 模式已移除，传入将返回参数错误。
- `time/hybrid` 模式必须提供 `time_start` 或 `time_end`，否则返回错误，不再静默按“未命中”处理。

### 删除返回语义（source 模式）

- `requested_source_count`：请求删除的 source 数。
- `matched_source_count`：实际命中的 source 数（存在活跃段落）。
- `deleted_paragraph_count`：实际删除段落数。
- `deleted_count`：与实际删除对象一致；在 `source` 模式下等于 `deleted_paragraph_count`。
- `success`：基于实际命中与实际删除判定，未命中 source 时返回 `false`。

## 调用示例

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

```json
{
  "tool": "ingest_text",
  "arguments": {
    "external_id": "note:2026-03-18:001",
    "source_type": "note",
    "text": "今天完成了检索调优评审",
    "chat_id": "group:dev",
    "tags": ["worklog"]
  }
}
```

```json
{
  "tool": "maintain_memory",
  "arguments": {
    "action": "protect",
    "target": "完成了 检索调优评审",
    "hours": 72
  }
}
```

## 快速开始

### 1. 安装依赖

在 MaiBot 主程序使用的同一个 Python 环境中执行：

```bash
pip install -r plugins/A_memorix/requirements.txt --upgrade
```

如果当前目录已经是插件目录，也可以执行：

```bash
pip install -r requirements.txt --upgrade
```

### 2. 启用插件

在 `config.toml` 中启用插件（路径取决于你的宿主部署）：

```toml
[plugin]
enabled = true
```

### 3. 先做运行时自检

```bash
python plugins/A_memorix/scripts/runtime_self_check.py --json
```

### 4. 导入文本并验证统计

```bash
python plugins/A_memorix/scripts/process_knowledge.py
```

然后调用 `memory_stats` 或 `search_memory` 检查是否有数据。

## Web 页面说明

仓库内保留了 Web 静态页面：

- `web/index.html`（图谱与记忆管理）
- `web/import.html`（导入中心）
- `web/tuning.html`（检索调优）

当前分支不再内置独立 `server.py`，页面路由与 API 暴露由宿主侧集成负责。

## 常用脚本

| 脚本 | 用途 |
| --- | --- |
| `process_knowledge.py` | 批量导入原始文本（策略感知） |
| `import_lpmm_json.py` | 导入 OpenIE JSON |
| `convert_lpmm.py` | 转换 LPMM 数据 |
| `migrate_chat_history.py` | 迁移 chat_history |
| `migrate_maibot_memory.py` | 迁移 MaiBot 历史记忆 |
| `migrate_person_memory_points.py` | 迁移 person memory points |
| `backfill_temporal_metadata.py` | 回填时间元数据 |
| `audit_vector_consistency.py` | 审计向量一致性 |
| `backfill_relation_vectors.py` | 回填关系向量 |
| `rebuild_episodes.py` | 按 source 重建 Episode |
| `release_vnext_migrate.py` | 升级预检/迁移/校验 |
| `runtime_self_check.py` | 真实 embedding 运行时自检 |

## 配置重点

完整配置见 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)。

高频配置项：

- `storage.data_dir`
- `embedding.dimension`
- `embedding.quantization_type`（当前仅支持 `int8`）
- `retrieval.*`
- `retrieval.sparse.*`
- `episode.*`
- `person_profile.*`
- `memory.*`
- `web.import.*`
- `web.tuning.*`

## Troubleshooting

### SQLite 无 FTS5

如果环境中的 SQLite 未启用 `FTS5`，可关闭稀疏检索：

```toml
[retrieval.sparse]
enabled = false
```

### 向量维度不一致

若日志提示当前 embedding 输出维度与既有向量库不一致，请先执行：

```bash
python plugins/A_memorix/scripts/runtime_self_check.py --json
```

必要时重建向量或调整 embedding 配置后再启动插件。

## 许可证

默认许可证为 [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0)（见 `LICENSE`）。

针对 `Mai-with-u/MaiBot` 项目的 GPL 额外授权见 `LICENSE-MAIBOT-GPL.md`。

除上述额外授权外，其他使用场景仍适用 AGPL-3.0。

## 贡献说明

当前不接受 PR，只接受 issue。

**作者**: `A_Dawn`
