# 长时间运行内存诊断工具使用指南

本文面向没有读过代码的使用者，说明如何开启内存诊断、如何查看输出、如何判断长期运行后的内存占用异常原因，以及应该如何把诊断信息发给维护者。

## 这个工具能做什么

内存诊断工具会定期记录 MaiBot 进程的内存状态和几个常见高占用来源：

- 主进程和子进程内存占用。
- Python GC 和 asyncio 任务数量。
- HeartFlow/MaiSaka 会话数量、消息缓存、历史循环和内部队列。
- 图片、表情、语音等二进制内容是否滞留。
- WebUI WebSocket 队列是否因为慢客户端积压。
- A_Memorix 向量索引、元数据、embedding 缓存是否随记忆库增长。
- 可选的 `tracemalloc` Python 分配差异。

它只负责诊断，不会自动清理内存，也不会修改运行时数据。

## 什么时候应该开启

建议在以下场景开启：

- MaiBot 长时间运行后 RSS 或任务管理器里的内存持续升高。
- 群聊或私聊数量变多后，内存没有回落。
- 大量图片、表情、语音消息后，内存明显升高。
- WebUI 打开时间久了，内存或响应变慢。
- 启用 A_Memorix 后，需要判断内存增长是正常的记忆库增长，还是异常对象滞留。

如果只是日常短时间运行，没有内存问题，可以保持关闭。

## 开启方法

打开 `config/bot_config.toml`，找到 `[debug]` 段，设置：

```toml
enable_memory_diagnostics = true
memory_diagnostics_interval_seconds = 300
memory_diagnostics_top_sessions = 20
memory_diagnostics_jsonl_path = "logs/memory_diagnostics/memory_diagnostics.jsonl"
memory_diagnostics_enable_tracemalloc = false
memory_diagnostics_snapshot_growth_mb = 100
memory_diagnostics_binary_scan_message_limit = 5000
memory_diagnostics_jsonl_max_total_size_mb = 50
memory_diagnostics_warn_runtime_count = 0
memory_diagnostics_warn_message_cache_count = 0
memory_diagnostics_warn_voice_binary_mb = 0
```

保存后重启 MaiBot。

诊断任务只在启动阶段注册，所以只改配置但不重启，通常不会立即启动诊断。

## 推荐配置

### 日常低开销诊断

长期观察优先使用这套配置：

```toml
enable_memory_diagnostics = true
memory_diagnostics_interval_seconds = 300
memory_diagnostics_enable_tracemalloc = false
memory_diagnostics_binary_scan_message_limit = 5000
```

这套配置每 5 分钟采样一次，默认不开 `tracemalloc`，对正常运行影响较小。

### 问题复现时提高频率

如果你正在复现一个很快出现的内存问题，可以临时改成：

```toml
memory_diagnostics_interval_seconds = 60
```

问题复现结束后建议改回 `300`，避免日志过多。

### 深挖 Python 分配来源

如果 RSS 明显上涨，但常规字段看不出原因，可以短时间开启：

```toml
memory_diagnostics_enable_tracemalloc = true
memory_diagnostics_snapshot_growth_mb = 100
```

开启后需要重启 MaiBot。`tracemalloc` 有额外开销，不建议长期默认开启。

## 输出在哪里

工具有两类输出。

### 1. 控制台摘要日志

日志中会出现类似内容：

```text
内存诊断快照: rss=850.2MB tree_rss=920.5MB uss=720.1MB runtime=18 message_cache=32000 source_messages=32000 history_loop=450 internal_queue=0 voice_binary=128.5MB binary_lower_bound=true ws_queue=0
```

这行适合快速判断当前大方向。

### 2. JSONL 明细文件

默认写入：

```text
logs/memory_diagnostics/memory_diagnostics.jsonl
```

JSONL 是“一行一个 JSON 快照”。每一行都是一次采样结果。

在 Windows PowerShell 中查看最后 20 行：

```powershell
Get-Content logs\memory_diagnostics.jsonl -Tail 20
```

如果文件超过配置的大小，会轮转成带时间戳的历史文件，例如：

```text
logs/memory_diagnostics.20260509-153000.jsonl
```

## 快速判断流程

先不要急着看所有字段。按下面顺序看，通常能快速缩小范围。

### 第一步：先看进程内存

重点字段：

```text
process.rss_mb
process.uss_mb
process.process_tree_rss_mb
process.children
```

含义：

- `rss_mb`：MaiBot 主进程占用的物理内存。
- `uss_mb`：主进程独占内存，更适合判断这个进程自己实际占了多少。
- `process_tree_rss_mb`：主进程加子进程的总 RSS。
- `children`：子进程明细。

判断：

- `rss_mb` 和 `uss_mb` 都涨：主进程内部对象或 native 内存更可疑。
- `process_tree_rss_mb` 涨但 `rss_mb` 不涨：优先看子进程。
- `rss_mb` 涨但业务字段都不涨：可能是 native 内存、Faiss、numpy、模型库或 Python 分配外资源。

### 第二步：看会话是否无界增长

重点字段：

```text
heartflow.runtime_count
heartflow.lock_count
heartflow.top_sessions
```

判断：

- `runtime_count` 持续增长，而且不回落：可能是会话 runtime 没有淘汰。
- `lock_count` 跟着 session 数增长：说明会话创建锁也在累积。
- `top_sessions` 可以看到占用最高的会话。

常见原因：

- 很多群聊或私聊陆续触发 MaiSaka。
- 会话长期不关闭。
- 空闲 session 没有淘汰策略。

### 第三步：看消息缓存是否增长

重点字段：

```text
heartflow.totals.message_cache
heartflow.totals.source_messages
heartflow.totals.message_received_markers
heartflow.top_sessions[].message_cache
heartflow.top_sessions[].source_messages
```

判断：

- `message_cache` 持续随总消息数线性增长：说明 runtime 内消息缓存可能没有裁剪。
- `source_messages` 也同步增长：说明源消息索引也在保留消息对象。
- 某个 `top_sessions` 特别大：优先排查那个 session。

常见原因：

- 单个活跃群聊运行很久。
- 消息被处理后仍被 runtime 强引用。
- 表达学习或其它消费者的处理游标阻止裁剪。

### 第四步：看 history_loop 是否增长

重点字段：

```text
heartflow.totals.history_loop
heartflow.totals.history_loop_estimated_mb
heartflow.top_sessions[].history_loop
heartflow.top_sessions[].history_loop_estimated_mb
```

判断：

- `history_loop` 持续增长：可能是每轮推理的循环详情没有裁剪。
- `history_loop_estimated_mb` 明显变大：说明这些循环详情本身也占内存。

常见原因：

- MaiSaka 长时间运行并持续进入推理循环。
- 每轮 cycle detail 保存了较多计划、动作或时间记录。

### 第五步：看语音、图片、表情二进制是否滞留

重点字段：

```text
heartflow.totals.voice_binary_mb
heartflow.totals.binary_mb
heartflow.totals.binary_lower_bound
heartflow.top_binary_sessions
chat_manager.last_message_binary_mb
chat_manager.last_message_voice_binary_mb
```

判断：

- `voice_binary_mb` 很高：语音原始二进制可能被消息缓存保留。
- `binary_mb` 很高：图片、表情、语音等二进制总量较高。
- `binary_lower_bound=true`：说明本轮只扫描了一部分消息，实际二进制占用可能更高。
- `top_binary_sessions` 会列出二进制占用最可疑的会话。

常见原因：

- 大量语音消息。
- 图片或表情描述任务挂起。
- 最后一条消息含大二进制，被 `chat_manager.last_messages` 保留。

注意：二进制扫描有预算，默认每轮最多扫描 5000 条消息。这个值是为了控制诊断开销，不是准确的全量内存统计。

### 第六步：看 WebSocket 是否积压

重点字段：

```text
websocket.unified.connections
websocket.unified.total_send_queue
websocket.unified.max_send_queue
websocket.unified.subscribed_connections
websocket.legacy_logs.active_connections
```

判断：

- `total_send_queue` 或 `max_send_queue` 持续增长：可能是 WebUI 慢客户端消费不过来。
- `connections` 很多：可能有多个 WebUI 页面或连接没有断开。
- `legacy_logs.active_connections` 长期不降：旧日志 WebSocket 可能有连接滞留。

常见原因：

- 浏览器页面长时间打开。
- 网络慢或 WebUI 客户端断线但服务端仍保留连接。
- 监控事件太多，发送队列积压。

### 第七步：看媒体描述任务是否挂起

重点字段：

```text
media_tasks.image.task_count
media_tasks.image.pending_task_count
media_tasks.image.estimated_binary_mb
media_tasks.emoji.task_count
media_tasks.emoji.pending_task_count
media_tasks.emoji.estimated_binary_mb
```

判断：

- `pending_task_count` 长期不下降：图片或表情描述任务可能卡住。
- `estimated_binary_mb` 高：pending task 可能持有大图片或表情二进制。

常见原因：

- VLM 请求慢。
- VLM 服务不可用。
- 短时间收到大量图片或表情。

### 第八步：看 A_Memorix 是否是正常增长

重点字段：

```text
a_memorix.kernel_loaded
a_memorix.vector_store.index_ntotal
a_memorix.vector_store.fallback_ntotal
a_memorix.vector_store.bin_count
a_memorix.vector_store.known_hashes
a_memorix.vector_store.reservoir_buffer
a_memorix.vector_store.write_buffer_ids
a_memorix.embedding.global_text_cache
a_memorix.embedding.local_cache
a_memorix.metadata
```

判断：

- `index_ntotal`、`bin_count`、`known_hashes` 随记忆数量增长：通常是记忆库正常增长。
- `fallback_ntotal` 很大且长期不释放：可能需要关注向量索引训练或回放状态。
- `embedding.*cache` 增长很快：可能是 embedding 缓存占用。
- RSS 增长和 `a_memorix.vector_store.*` 同步：更像向量库/native 内存增长，不一定是泄漏。

### 第九步：必要时看 tracemalloc

开启 `memory_diagnostics_enable_tracemalloc = true` 后，重点看：

```text
tracemalloc.current_mb
tracemalloc.rss_interval_growth_mb
tracemalloc.rss_baseline_growth_mb
tracemalloc.diff
```

判断：

- RSS 增长，`tracemalloc.diff` 也有明显增长：更可能是 Python 对象分配。
- RSS 增长，但 `tracemalloc.current_mb` 不怎么变：优先怀疑 native 内存、Faiss、numpy、模型库、子进程或底层资源。

## 常见症状和判断

### 症状：运行几天后内存越来越高

优先看：

```text
heartflow.runtime_count
heartflow.totals.message_cache
heartflow.totals.source_messages
heartflow.totals.history_loop
```

如果这些字段一起涨，通常是会话 runtime 或消息历史滞留。

### 症状：发很多语音后内存明显升高

优先看：

```text
heartflow.totals.voice_binary_mb
heartflow.top_binary_sessions
chat_manager.last_message_voice_binary_mb
```

如果 `voice_binary_mb` 很高，说明语音原始二进制可能还在内存对象里。

### 症状：打开 WebUI 后内存或响应变差

优先看：

```text
websocket.unified.total_send_queue
websocket.unified.max_send_queue
websocket.unified.connections
```

如果队列持续增长，优先排查慢客户端或 WebUI 连接积压。

### 症状：启用 A_Memorix 后 RSS 增长

优先看：

```text
a_memorix.vector_store.index_ntotal
a_memorix.vector_store.bin_count
a_memorix.vector_store.known_hashes
a_memorix.embedding.local_cache
a_memorix.embedding.global_text_cache
```

如果这些字段和 RSS 同步增长，可能是记忆库规模增长；如果 RSS 增长但这些不变，再看其它字段或开启 `tracemalloc`。

### 症状：图片或表情很多后内存涨

优先看：

```text
media_tasks.image.pending_task_count
media_tasks.image.estimated_binary_mb
media_tasks.emoji.pending_task_count
media_tasks.emoji.estimated_binary_mb
heartflow.totals.binary_mb
```

如果 pending task 长期不降，重点排查 VLM 描述任务。

## 告警阈值怎么用

可以设置这些字段让日志主动输出 WARNING：

```toml
memory_diagnostics_warn_runtime_count = 100
memory_diagnostics_warn_message_cache_count = 50000
memory_diagnostics_warn_voice_binary_mb = 500
```

含义：

- runtime 数超过 100 时告警。
- message_cache 总数超过 50000 时告警。
- 语音二进制估算超过 500MB 时告警。

这些阈值没有统一标准，要按你的机器人规模调整。

小群或低频机器人可以设低一些；多群、高频机器人要设高一些。

## 如何把信息发给维护者

如果你要请别人帮忙分析，请至少提供这些信息：

- 发生问题的时间段。
- MaiBot 大概运行了多久。
- 是否启用 WebUI。
- 是否启用 A_Memorix。
- 最近是否有大量语音、图片、表情。
- `logs/memory_diagnostics/memory_diagnostics.jsonl` 最后 20 到 50 行。
- 如果开了 `tracemalloc`，提供包含 `tracemalloc.diff` 的几行。

PowerShell 导出最后 50 行：

```powershell
Get-Content logs\memory_diagnostics.jsonl -Tail 50 > memory_diagnostics_tail.txt
```

建议同时提供问题发生前和发生后的日志片段。只有最后一行通常不够判断趋势。

## 推荐排查模板

可以按这个格式发给维护者：

```text
问题：MaiBot 长时间运行后内存持续升高
运行时长：约 2 天
是否启用 WebUI：是
是否启用 A_Memorix：是
最近是否大量语音/图片/表情：语音较多
内存变化：从约 800MB 增长到 2.5GB
诊断配置：interval=300, tracemalloc=false, binary_scan_limit=5000
附件：memory_diagnostics_tail.txt
补充：问题发生时 WebUI 开着两个页面
```

## 字段速查表

| 字段 | 主要含义 | 异常时优先怀疑 |
| --- | --- | --- |
| `process.rss_mb` | 主进程 RSS | 主进程内对象或 native 内存 |
| `process.uss_mb` | 主进程独占内存 | MaiBot 自身实际占用 |
| `process.process_tree_rss_mb` | 主进程加子进程 RSS | 子进程或浏览器/插件进程 |
| `heartflow.runtime_count` | MaiSaka runtime 数 | 会话池无淘汰 |
| `heartflow.lock_count` | 会话创建锁数量 | session churn 后锁累积 |
| `heartflow.totals.message_cache` | 消息缓存总量 | 消息缓存未裁剪 |
| `heartflow.totals.source_messages` | 源消息索引总量 | 消息对象被强引用 |
| `heartflow.totals.history_loop` | 推理循环历史数量 | cycle detail 未裁剪 |
| `heartflow.totals.voice_binary_mb` | 语音二进制估算 | 语音原始数据滞留 |
| `heartflow.totals.binary_lower_bound` | 二进制估算是否可能偏低 | 扫描预算不足 |
| `websocket.unified.total_send_queue` | WebSocket 总发送队列 | 慢客户端积压 |
| `media_tasks.*.pending_task_count` | 媒体描述 pending 任务数 | VLM 任务卡住 |
| `media_tasks.*.estimated_binary_mb` | pending task 二进制估算 | 媒体任务持有大对象 |
| `a_memorix.vector_store.index_ntotal` | A_Memorix 主索引条目数 | 记忆库规模增长 |
| `a_memorix.vector_store.fallback_ntotal` | A_Memorix fallback 索引条目数 | 索引训练/回放状态 |
| `a_memorix.embedding.local_cache` | embedding 本地缓存数量 | embedding 缓存增长 |
| `tracemalloc.diff` | Python 分配增长来源 | Python 层对象增长 |

## 注意事项

- 不要长期高频采样，例如每 10 秒采样一次只适合短时间复现。
- 不要长期默认开启 `tracemalloc`，除非正在深挖 Python 分配。
- `binary_mb` 是估算值，不是精确全量统计。
- `binary_lower_bound=true` 时，实际二进制占用可能高于日志值。
- RSS 不会总是立刻回落，Python 和 native allocator 都可能保留已申请的内存页。
- 判断问题时要看趋势，不要只看单次快照。
