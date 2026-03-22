# A_Memorix Quick Start (v2.0.0)

本文档面向当前 `2.0.0` 架构（SDK Tool 接口）。

## 0. 版本与接口变更

- 当前插件版本：`2.0.0`
- 接口形态：`memory_provider` + Tool 调用
- 旧版 slash 命令（如 `/query`、`/memory`、`/visualize`）不再作为本分支主文档入口

## 1. 环境准备

- Python 3.10+
- 与 MaiBot 主程序相同的运行环境
- 可访问你配置的 embedding 服务

安装依赖：

```bash
pip install -r plugins/A_memorix/requirements.txt --upgrade
```

如果当前目录就是插件目录，也可以：

```bash
pip install -r requirements.txt --upgrade
```

## 2. 启用插件

在主程序插件配置中启用 `A_Memorix`。

若你使用 `plugins/A_memorix/config.toml` 方式，最小示例：

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
quantization_type = "int8"
```

## 3. 运行时自检（强烈建议）

先确认 embedding 实际输出维度与向量库兼容：

```bash
python plugins/A_memorix/scripts/runtime_self_check.py --json
```

如果结果 `ok=false`，先修复 embedding 配置或向量库，再继续导入。

## 4. 导入数据

### 4.1 文本批量导入

把文本放到：

```text
plugins/A_memorix/data/raw/
```

执行：

```bash
python plugins/A_memorix/scripts/process_knowledge.py
```

常用参数：

```bash
python plugins/A_memorix/scripts/process_knowledge.py --force
python plugins/A_memorix/scripts/process_knowledge.py --chat-log
python plugins/A_memorix/scripts/process_knowledge.py --chat-log --chat-reference-time "2026/02/12 10:30"
```

### 4.2 其他导入脚本

```bash
python plugins/A_memorix/scripts/import_lpmm_json.py <json文件或目录>
python plugins/A_memorix/scripts/convert_lpmm.py -i <lpmm数据目录> -o plugins/A_memorix/data
python plugins/A_memorix/scripts/migrate_chat_history.py --help
python plugins/A_memorix/scripts/migrate_maibot_memory.py --help
python plugins/A_memorix/scripts/migrate_person_memory_points.py --help
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

### Q3: Web 页面打不开

本分支不内置独立 `server.py`。

- `web/index.html`、`web/import.html`、`web/tuning.html` 由宿主侧路由/API 集成暴露
- 请检查宿主是否已映射对应静态页与 `/api/*` 接口

## 8. 下一步

- 配置细节见 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)
- 导入细节见 [IMPORT_GUIDE.md](IMPORT_GUIDE.md)
- 版本历史见 [CHANGELOG.md](CHANGELOG.md)
