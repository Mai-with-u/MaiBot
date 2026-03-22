# A_Memorix 导入指南 (v2.0.0)

本文档对应当前 `2.0.0` 代码路径，覆盖两类导入方式：

1. 脚本导入（离线批处理）
2. `memory_import_admin` 任务导入（在线任务化）

## 1. 导入前检查

建议先执行：

```bash
python plugins/A_memorix/scripts/runtime_self_check.py --json
```

再确认：

- `storage.data_dir` 路径可写
- embedding 配置可用
- 若是升级项目，先完成迁移脚本

## 2. 方式 A：脚本导入（推荐起步）

## 2.1 原始文本导入

将 `.txt` 文件放入：

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

## 2.2 OpenIE JSON 导入

```bash
python plugins/A_memorix/scripts/import_lpmm_json.py <json文件或目录>
```

## 2.3 LPMM 数据转换

```bash
python plugins/A_memorix/scripts/convert_lpmm.py -i <lpmm数据目录> -o plugins/A_memorix/data
```

## 2.4 历史数据迁移

```bash
python plugins/A_memorix/scripts/migrate_chat_history.py --help
python plugins/A_memorix/scripts/migrate_maibot_memory.py --help
python plugins/A_memorix/scripts/migrate_person_memory_points.py --help
```

## 2.5 导入后修复与重建

```bash
python plugins/A_memorix/scripts/backfill_temporal_metadata.py --dry-run
python plugins/A_memorix/scripts/backfill_relation_vectors.py --limit 1000
python plugins/A_memorix/scripts/rebuild_episodes.py --all --wait
python plugins/A_memorix/scripts/audit_vector_consistency.py --json
```

## 3. 方式 B：`memory_import_admin` 任务导入

`memory_import_admin` 是在线任务化导入入口，适合宿主侧面板或自动化管道。

### 3.1 常用 action

- `settings` / `get_settings` / `get_guide`
- `path_aliases` / `get_path_aliases`
- `resolve_path`
- `create_upload`
- `create_paste`
- `create_raw_scan`
- `create_lpmm_openie`
- `create_lpmm_convert`
- `create_temporal_backfill`
- `create_maibot_migration`
- `list`
- `get`
- `chunks` / `get_chunks`
- `cancel`
- `retry_failed`

### 3.2 调用示例

查看运行时设置：

```json
{
  "tool": "memory_import_admin",
  "arguments": {
    "action": "settings"
  }
}
```

创建粘贴导入任务：

```json
{
  "tool": "memory_import_admin",
  "arguments": {
    "action": "create_paste",
    "content": "今天完成了检索调优回归。",
    "input_mode": "plain_text",
    "source": "manual:worklog"
  }
}
```

查询任务列表：

```json
{
  "tool": "memory_import_admin",
  "arguments": {
    "action": "list",
    "limit": 20
  }
}
```

查看任务详情：

```json
{
  "tool": "memory_import_admin",
  "arguments": {
    "action": "get",
    "task_id": "<task_id>",
    "include_chunks": true
  }
}
```

重试失败任务：

```json
{
  "tool": "memory_import_admin",
  "arguments": {
    "action": "retry_failed",
    "task_id": "<task_id>"
  }
}
```

## 4. 直接写入 Tool（非任务化）

若你不需要任务编排，也可以直接调用：

- `ingest_summary`
- `ingest_text`

示例：

```json
{
  "tool": "ingest_text",
  "arguments": {
    "external_id": "note:2026-03-18:001",
    "source_type": "note",
    "text": "新的召回阈值方案已通过评审",
    "chat_id": "group:dev",
    "tags": ["worklog", "review"]
  }
}
```

`external_id` 建议全局唯一，用于幂等去重。

## 5. 时间字段建议

可用时间字段（按常见优先级）：

- `timestamp`
- `time_start`
- `time_end`

建议：

- 事件类记录优先写 `time_start/time_end`
- 仅有单点时间时写 `timestamp`
- 历史数据可先导入，再用 `backfill_temporal_metadata.py` 回填

## 6. source_type 建议

常见值：

- `chat_summary`
- `note`
- `person_fact`
- `lpmm_openie`
- `migration`

建议保持稳定枚举，便于后续按来源治理与重建 Episode。

## 7. 导入完成后的验证

建议执行以下顺序：

1. `memory_stats` 看总量是否增长
2. `search_memory`（`mode=search`/`aggregate`）抽检召回
3. `memory_episode_admin` 的 `status`/`query` 检查 Episode 生成
4. `memory_runtime_admin` 的 `self_check` 再确认运行时健康

## 8. 常见问题

### Q1: 导入任务创建成功但无写入

- 检查聊天过滤配置 `filter`（若 `respect_filter=true` 可能被过滤）
- 检查任务详情中的失败原因与分块状态

### Q2: 任务反复失败

- 检查 embedding 与 LLM 可用性
- 降低并发（`web.import.default_*_concurrency`）
- 调整重试参数（`web.import.llm_retry.*`）

### Q3: 导入后检索效果差

- 先做 `runtime_self_check`
- 检查 `retrieval.sparse` 是否启用
- 使用 `memory_tuning_admin` 创建调优任务做参数回归

## 9. 相关文档

- [QUICK_START.md](QUICK_START.md)
- [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)
- [README.md](README.md)
- [CHANGELOG.md](CHANGELOG.md)

## 10. 附录：策略模式参考

A_Memorix 导入链路仍然遵循策略模式（Strategy-Aware）。`process_knowledge.py` 会自动识别文本类型，也支持手动指定。

| 策略类型 | 适用场景 | 核心逻辑 | 自动识别特征 |
| :-- | :-- | :-- | :-- |
| `Narrative` (叙事) | 小说、同人文、剧本、长篇故事 | 按场景/章节切分，使用滑动窗口；提取事件与角色关系 | `#`、`Chapter`、`***` 等章节标记 |
| `Factual` (事实) | 设定集、百科、说明书 | 按语义块切分，保留列表/定义结构；提取 SPO 三元组 | 列表符号、`术语: 解释` |
| `Quote` (引用) | 歌词、诗歌、名言、台词 | 按双换行切分，原文即知识，不做概括 | 平均行长短、行数多 |

## 11. 附录：参考用例（已恢复）

以下样例可直接复制保存为文件测试，或作为 LLM few-shot 示例。

### 11.1 叙事文本 (`plugins/A_memorix/data/raw/story_demo.txt`)

```text
# 第一章：星之子

艾瑞克在废墟中醒来，手中的星盘发出微弱的蓝光。他并不记得自己是如何来到这里的，只依稀记得莉莉丝最后的警告：“千万不要回头。”

远处传来了机械守卫的轰鸣声。艾瑞克迅速收起星盘，向着北方的废弃都市奔去。他知道，那里有反抗军唯一的据点。

***

# 第二章：重逢

在反抗军的地下掩体中，艾瑞克见到了那个熟悉的身影。莉莉丝正站在全息地图前，眉头紧锁。

“你还是来了。”莉莉丝没有回头，但声音中带着一丝颤抖。
“我必须来，”艾瑞克握紧了拳头，“为了解开星盘的秘密，也为了你。”
```

### 11.2 事实文本 (`plugins/A_memorix/data/raw/rules_demo.txt`)

```text
# 联邦安全协议 v2.0

## 核心法则
1. **第一公理**：任何人工智能不得伤害人类个体，或因不作为而使人类个体受到伤害。
2. **第二公理**：人工智能必须服从人类的命令，除非该命令与第一公理冲突。

## 术语定义
- **以太网络**：覆盖全联邦的高速量子通讯网络。
- **黑色障壁**：用于隔离高危 AI 的物理防火墙设施。
```

### 11.3 引用文本 (`plugins/A_memorix/data/raw/poem_demo.txt`)

```text
致橡树

我如果爱你——
绝不像攀援的凌霄花，
借你的高枝炫耀自己；

我如果爱你——
绝不学痴情的鸟儿，
为绿荫重复单调的歌曲；

也不止像泉源，
常年送来清凉的慰籍；
也不止像险峰，
增加你的高度，衬托你的威仪。
```

### 11.4 LPMM JSON (`lpmm_data-openie.json`)

```json
{
  "docs": [
    {
      "passage": "艾瑞克手中的星盘是打开遗迹的唯一钥匙。",
      "extracted_triples": [
        ["星盘", "是", "唯一的钥匙"],
        ["星盘", "属于", "艾瑞克"],
        ["钥匙", "用于", "遗迹"]
      ],
      "extracted_entities": ["星盘", "艾瑞克", "遗迹", "钥匙"]
    },
    {
      "passage": "莉莉丝是反抗军的现任领袖。",
      "extracted_triples": [
        ["莉莉丝", "是", "领袖"],
        ["领袖", "所属", "反抗军"]
      ]
    }
  ]
}
```
