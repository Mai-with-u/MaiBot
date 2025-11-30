# LPMM 知识库脚本使用指南（零基础用户版）

本指南面向不熟悉命令行和代码的 C 端用户，帮助你完成：

- LPMM 知识库的初始部署（从本地 txt 到可检索知识库）
- 安全删除知识（按批次、按原文、按哈希、按关键字）
- 导入 / 删除后的自检与检索效果验证

> 说明：本文默认你已经完成 MaiBot 的基础安装，并能在项目根目录打开命令行终端。
> 重要提醒：每次使用导入 / 删除相关脚本（如 `import_openie.py`、`delete_lpmm_items.py`）修改 LPMM 知识库后，聊天机器人 / WebUI 端要想看到最新知识，需要重启主程序，或在主程序内部显式调用一次 `lpmm_start_up()` 重新初始化 LPMM

---
。


## 一、需要用到的脚本一览

在项目根目录（`MaiBot-dev`）下，这些脚本是 LPMM 相关的“工具箱”：

- 导入相关：
  - `scripts/raw_data_preprocessor.py`  
    从 `data/lpmm_raw_data` 目录读取 `.txt` 文件，按空行拆分为一个个段落，并做去重。
  - `scripts/info_extraction.py`  
    调用大模型，从每个段落里抽取实体和三元组，生成中间的 OpenIE JSON 文件。
  - `scripts/import_openie.py`  
    把 `data/openie` 目录中的 OpenIE JSON 文件导入到 LPMM 知识库（向量库 + 知识图）。
- 删除相关：
  - `scripts/delete_lpmm_items.py`  
    LPMM 知识库删除入口，支持按批次、按原始文本段落、按哈希列表、按关键字模糊搜索删除。
- 自检相关：
  - `scripts/inspect_lpmm_global.py`  
    查看整个知识库的当前状态：段落/实体/关系条数、知识图节点/边数量、示例内容等。
  - `scripts/inspect_lpmm_batch.py`  
    针对某个 OpenIE JSON 批次，检查它在向量库和知识图中的“残留情况”（导入与删除前后对比）。
  - `scripts/test_lpmm_retrieval.py`  
    使用几条预设问题测试 LPMM 检索能力，帮助你判断知识库是否正常工作。
    - `scripts/refresh_lpmm_knowledge.py`  
      手动重新加载 `data/embedding` 和 `data/rag` 到内存，用来确认当前磁盘上的 LPMM 知识库能正常初始化。

> 注意：所有命令示例都假设你已经在虚拟环境中，命令行前缀类似 `(.venv)`，并且当前目录是项目根目录。

---

## 二、LPMM 知识库的初始部署

### 2.1 准备原始 txt 文本

1. 把要导入的知识文档放到：

   ```text
   data/lpmm_raw_data
   ```

2. 文件要求：

   - 必须是 `.txt` 文件，建议使用 UTF-8 编码；
   - 用**空行**分隔段落：一段话后空一行，即视为一条独立知识。

示例文件：

- `data/lpmm_raw_data/lpmm_large_sample.txt`：仓库内已经提供了一份大样本测试文本，可以直接用来练习。

### 2.2 第一步：预处理原始文本（拆段 + 去重）

在项目根目录执行：

```bash
.\.venv\Scripts\python.exe scripts/raw_data_preprocessor.py
```

成功时通常会看到日志类似：

- 正在处理文件: `lpmm_large_sample.txt`
- 共读取到 XX 条数据

这一步不会调用大模型，仅做拆段和去重。

### 2.3 第二步：进行信息抽取（生成 OpenIE JSON）

执行：

```bash
.\.venv\Scripts\python.exe scripts/info_extraction.py
```

你会看到一个“重要操作确认”提示，说明：

- 信息抽取会调用大模型，消耗 API 费用和时间；
- 如果确认无误，输入 `y` 回车继续。

提取过程中可能出现：

- 类似“模型 ... 网络错误(可重试)”这样的日志；  
  这表示脚本在遇到网络问题时自动重试，一般无需手动干预。

运行结束后，会有类似提示：

```text
信息提取结果已保存到: data/openie/11-27-10-06-openie.json
```

- 请记住这个文件名，比如：`11-27-10-06-openie.json`  
  接下来我们会用 `<OPENIE>` 来代指这类文件。

### 2.4 第三步：导入 OpenIE 数据到 LPMM 知识库

执行：

```bash
.\.venv\Scripts\python.exe scripts/import_openie.py
```

这个脚本会：

- 从 `data/openie` 目录读取所有 `*.json` 文件，并合并导入；
- 将新段落的嵌入向量写入 `data/embedding`；
- 将三元组构建为知识图写入 `data/rag`。

> 提示：如果你希望“只导入某几批数据”，可以暂时把不需要的 JSON 文件移出 `data/openie`，导入结束后再移回。

### 2.5 第四步：全局自检（确认导入成功）

执行：

```bash
.\.venv\Scripts\python.exe scripts/inspect_lpmm_global.py
```

你会看到类似输出：

- 段落向量条数: `52`
- 实体向量条数: `260`
- 关系向量条数: `299`
- KG 节点总数 / 边总数 / 段落节点数 / 实体节点数
- 若干条示例段落与实体内容预览

只要这些数字大于 0，就表示 LPMM 知识库已经有可用的数据了。

### 2.6 第五步：用脚本测试 LPMM 检索效果（可选但推荐）

执行：

```bash
.\.venv\Scripts\python.exe scripts/test_lpmm_retrieval.py
```

脚本会：

- 自动初始化 LPMM（加载向量库与知识图）；
- 用几条预设问题查询 LPMM；
- 打印原始检索结果和关键词命中情况。

你可以通过观察“RAW RESULT”里的内容，粗略判断：

- 能否命中与问题高度相关的知识；
- 删除或导入新知识后，回答内容是否发生变化。

---

## 三、安全删除知识的几种方式

> 强烈建议：删除前先备份以下目录，以便“回档”：
>
> - `data/embedding`（向量库）
> - `data/rag`（知识图）

所有删除操作使用同一个脚本：

```bash
.\.venv\Scripts\python.exe scripts/delete_lpmm_items.py [参数...]
```

脚本特点：

- 删除前会打印“待删除段落数量 / 实体数量 / 关系数量 / 预计删除节点数”等摘要；
- 需要你输入大写 `YES` 确认才会真正执行；
- 支持多种删除策略，可灵活组合。

### 3.1 按批次删除（推荐：整批回滚）

适用场景：某次导入的整批知识有问题，希望整体回滚。

1. 删除前，先检查该批次状态：

   ```bash
   .\.venv\Scripts\python.exe scripts/inspect_lpmm_batch.py ^
     --openie-file data/openie/<OPENIE>.json
   ```

   你会看到该批次：

   - 段落：总计多少条、向量库剩余多少、KG 中剩余多少；
   - 实体、关系的类似统计；
   - 少量示例段落/实体内容预览。

2. 确认无误后，按批次删除：

   ```bash
   .\.venv\Scripts\python.exe scripts/delete_lpmm_items.py ^
     --openie-file data/openie/<OPENIE>.json ^
     --delete-entities --delete-relations --remove-orphan-entities
   ```

   参数含义：

   - `--delete-entities`：删除该批次涉及的实体向量；
   - `--delete-relations`：删除该批次涉及的关系向量；
   - `--remove-orphan-entities`：顺带清理删除后不再参与任何边的“孤立实体”节点。

3. 删除后再检查：

   ```bash
   .\.venv\Scripts\python.exe scripts/inspect_lpmm_batch.py ^
     --openie-file data/openie/<OPENIE>.json
   
   .\.venv\Scripts\python.exe scripts/inspect_lpmm_global.py
   ```

   若批次检查显示“向量库剩余 0 / KG 中剩余 0”，则说明该批次已被彻底删除。

### 3.2 按原始文本段落删除（精确定位某一段）

适用场景：某个原始 txt 的特定段落写错了，只想删这段对应的知识。

命令示例：

```bash
.\.venv\Scripts\python.exe scripts/delete_lpmm_items.py ^
  --raw-file data/lpmm_raw_data/lpmm_large_sample.txt ^
  --raw-index 2
```

说明：

- `--raw-index` 从 1 开始计数，可用逗号多选，例如：`1,3,5`；
- 脚本会展示该段落的内容预览和哈希值，再请求你确认。

### 3.3 按哈希列表删除（进阶用法）

适用场景：你有一份“需要删除的段落哈希列表”（比如从其他系统导出）。

示例哈希列表文件：

- `data/openie/lpmm_delete_test_hashes.txt`

命令：

```bash
.\.venv\Scripts\python.exe scripts/delete_lpmm_items.py ^
  --hash-file data/openie/lpmm_delete_test_hashes.txt
```

说明：

- 文件中每行一条，可以是 `paragraph-xxxx` 或纯哈希，脚本会自动识别；
- 适合“精确控制删除哪些段落”，但准备哈希列表需要一定技术基础。

### 3.4 按关键字模糊搜索删除（对非技术用户最友好）

适用场景：只知道某段话里包含某个关键词，不知道它在哪个 txt 或批次里。

示例 1：删除与“近义词扩展”相关的段落

```bash
.\.venv\Scripts\python.exe scripts/delete_lpmm_items.py   --search-text "近义词扩展"   --search-limit 5
```

示例 2：删除与“LPMM”强相关的一些段落

```bash
.\.venv\Scripts\python.exe scripts/delete_lpmm_items.py   --search-text "LPMM"   --search-limit 20

```

执行过程：

1. 脚本在当前段落库中查找包含该关键字的段落；
2. 列出前 N 条候选（`--search-limit` 决定数量）；
3. 提示你输入要删除的序号列表，例如：`1,2,5`；
4. 再次提示你输入 `YES` 确认，才会真正执行删除。

> 建议：
>
> - 第一次使用时可以先加 `--dry-run` 看看效果：
>   ```bash
>   .\.venv\Scripts\python.exe scripts/delete_lpmm_items.py ^
>     --search-text "LPMM" ^
>     --search-limit 20 ^
>     --dry-run
>   ```
> - 确认候选列表确实是你要删的内容后，再去掉 `--dry-run` 正式执行。

---

## 四、自检：如何确认导入 / 删除是否“生效”

### 4.1 全局状态检查

每次导入或删除之后，建议跑一次：

```bash
.\.venv\Scripts\python.exe scripts/inspect_lpmm_global.py
```

你可以在这里看到：

- 段落向量条数、实体向量条数、关系向量条数；
- 知识图的节点总数、边总数、段落节点和实体节点数量；
- 若干条“剩余段落示例”和“剩余实体示例”。

观察方式：

- 导入后：数字应该明显上升（说明新增数据生效）；
- 删除后：数字应该明显下降（说明删除操作生效）。

### 4.2 某个批次的局部状态

如果你想确认“某一个 OpenIE 文件对应的那一批知识”是否存在，可以使用：

```bash
.\.venv\Scripts\python.exe scripts/inspect_lpmm_batch.py   --openie-file data/openie/<OPENIE>.json
```

输出中会包含：

- 该批次的段落 / 实体 / 关系的总数；
- 在向量库中还剩多少条，在 KG 中还剩多少条；
- 若干条仍存在的段落/实体示例。

典型用法：

- 导入后立刻检查一次：确认这一批已经“写入”；
- 删除后再检查一次：确认这一批是否已经“清空”。

### 4.3 检索效果回归测试

每次做完导入或删除，你都可以用这条命令快速验证检索效果：

```bash
.\.venv\Scripts\python.exe scripts/test_lpmm_retrieval.py
```

它会：

- 初始化 LPMM（加载当前向量库和知识图）；
- 用几条预设问题（包括与 LPMM 和配置相关的问题）进行检索；
- 打印检索结果以及命中关键词情况。

通过对比不同时间点的输出，你可以判断：

- 某些知识是否已经被成功删除（不再出现在回答中）；

- 新增的知识是否已经能被检索到。

### 4.4 进阶：一键刷新（可选）

- 想简单确认“现在这份 data/embedding + data/rag 是否健康”？执行：

  `.\.venv\Scripts\python.exe scripts/refresh_lpmm_knowledge.py `

  它会尝试初始化 LPMM，并打印当前段落/实体/关系条数和图大小。





---

## 五、常见提示与注意事项

1. **看到“网络错误(可重试)”需要担心吗？**

   - 不需要。  
   - 这些日志说明脚本在自动处理网络抖动，多数情况下会在重试后成功返回结果。
   - 只要脚本最后没有报“重试耗尽并退出”，一般导入/提取结果是有效的。

2. **删除操作会不会“一删全没”？**

   - 不会直接“一删全没”：
     - 每次删除会打印摘要信息；
     - 必须输入 `YES` 才会真正执行；
     - 大批次时还有 `--max-delete-nodes` 保护，超过阈值会警告。
   - 但仍然建议：
     - 在大规模删除前备份 `data/embedding` 和 `data/rag`；
     - 先通过 `--dry-run` 看看待删列表。

3. **可以多次导入吗？需要先清空吗？**

   - 可以多次导入，系统会根据段落内容的哈希做去重；
   - 不需要每次都清空，只要你希望老数据仍然保留即可；
   - 如果你确实想“重来一遍”，可以：
     - 先备份，然后删除 `data/embedding` 和 `data/rag`；
     - 再重新跑导入流程。

4. **LPMM 开关在哪里？**

   - 配置文件：`config/bot_config.toml`；
   - 小节：`[lpmm_knowledge]`；
   - 其中有 `enable = true/false` 开关：
     - 为 `true`：LPMM 知识库启用，问答时会使用；
     - 为 `false`：LPMM 关闭，即使知识库有数据，也不会参与回答。
   - 修改后需要重启主程序，让设置生效。

---

如果你是普通用户，只需要记住一句话：

> “导入三步走：预处理 → 信息抽取 → 导入 OpenIE；  
> 删除三步走：先检查 → 再删除 → 然后再检查。”

照着本指南中的命令一步一步执行，就可以安全地管理你的 LPMM 知识库。***
