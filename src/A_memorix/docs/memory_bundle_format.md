# A_Memorix 可分享记忆包

`.amembundle` 用于在 A_Memorix 实例之间分享、安装和迁移已经处理完成的记忆。安装过程直接恢复结构化结果，不调用 LLM 做分块、实体抽取或关系抽取。

## 内容级别与范围

内容级别和导出范围是两个独立维度。

- `knowledge`：导出段落、实体、三元组、知识类型、时间信息和作用域元数据。`knowledge.json` 保留 LPMM OpenIE 的 `docs[].passage`、`extracted_entities`、`extracted_triples` 语义，并通过 `a_memorix` 扩展字段保存 A_Memorix 信息。
- `full`：在知识内容之外，导出所选段落闭包内的 Episode、人物画像快照、画像覆盖、别名覆盖、事实账本、外部引用和生命周期字段。

导出范围支持全部记忆、来源、聊天流、导入任务和已安装知识包。聊天流必须使用已经存在的真实 `ChatSession.session_id`。历史导入任务没有逐段批次标记时，系统根据任务报告中的来源集合选择段落，并把 `manifest.selector.precision` 标记为 `source_set`。知识包安装后会保存逐段映射，后续按包导出是精确的。

## 文件结构

记忆包是一个不落地解压的 ZIP 文件，固定使用 `.amembundle` 扩展名。

```text
manifest.json
knowledge.json
state.json                  # 仅 full 包存在
vectors/paragraphs.npz      # 可选
vectors/graph.npz           # 可选
checksums.json
```

`manifest.json` 记录格式版本、包 ID、包版本、内容摘要、选择器、各类记录数量和 embedding 指纹。`checksums.json` 使用 SHA-256 校验每个成员。导入端限制成员数量和解压后总大小，不会把 ZIP 成员写入文件系统。

`knowledge.json` 的基础结构如下：

```json
{
  "format": "lpmm_openie",
  "format_version": 1,
  "docs": [
    {
      "id": "稳定段落标识",
      "passage": "已经处理好的知识段落",
      "extracted_entities": ["实体"],
      "extracted_triples": [["主语", "谓语", "宾语"]],
      "a_memorix": {
        "knowledge_type": "factual",
        "source": "原始来源",
        "time_meta": {},
        "metadata": {}
      }
    }
  ]
}
```

## 安装语义

`merge` 是默认安装方式。段落、实体和关系使用稳定内容哈希去重；相同包版本安装到相同范围时返回已安装，不会重复计数。同一包可以分别安装到全局和不同聊天流，每次安装都有稳定的 `installation_id`。

`restore` 只接受 `full` 包，并要求目标记忆库为空。该限制用于避免画像版本、Episode 和事实状态与目标实例中的既有语义发生冲突。

安装到聊天流时，段落作用域、聊天摘要来源、Episode 标识和聊天事实标识会一起映射到目标聊天流。画像、事实证据、状态转换中的引用同步更新，避免只改显示范围而留下失效 ID。

知识包来源统一使用 `knowledge_pack:<package_id>`。知识级安装不会为这些段落再次生成 Episode；完整包中的 Episode 直接恢复。FTS、N-gram、关系图快照和关系哈希别名在目标实例重建，后台队列、运行时任务、反馈审计、删除审计和聊天流开关不属于可移植语义状态。

安装过程会记录段落、实体、关系、Episode、事实账本和人物画像等资源是由本次安装创建还是复用。卸载时只删除该安装独占创建的资源；其他知识包仍在使用的资源会转移归属并保留，同时清理对应向量并重建派生索引。缺少资源归属信息的旧安装记录不会执行推测性删除。

语义状态、安装记录和资源归属在同一个 SQLite 事务中提交，提交时安装状态为 `installing`。图与向量投影完成后才切换为 `installed`；普通投影失败会自动清理本次安装，进程中断留下的未完成安装会在同包重试时先安全卸载，再重新执行安装。

## 向量复用

向量是可选加速数据，不是知识真值。只有包内 embedding 指纹与目标实例当前观测到的指纹完全一致时，段落向量和图向量才会直接复用。指纹缺失或不同会触发目标实例本地 embedding 写入或回填，不会使用维度相同但模型不同的向量。

## 隐私边界

记忆包可能包含聊天内容、人物画像、人物 ID 和事实证据。导出是显式管理操作，分享前应按用途选择范围和内容级别。面向公共发布时优先使用 `knowledge`，只有受信任的实例迁移才使用 `full`。
