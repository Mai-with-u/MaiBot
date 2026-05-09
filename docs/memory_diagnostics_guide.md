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
Get-Content logs\memory_diagnostics\memory_diagnostics.jsonl -Tail 20
```

如果当前文件和历史文件总大小超过配置的上限，会轮转成带时间戳的历史文件，并自动删除最旧的轮转文件，例如：

```text
logs/memory_diagnostics/memory_diagnostics.20260509-153000.jsonl
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

### 第八步：看 memory_automation 队列是否积压

重点字段：

```text
memory_automation.started
memory_automation.fact_writeback_queue
memory_automation.fact_writeback_worker_active
memory_automation.chat_summary_queue
memory_automation.chat_summary_worker_active
memory_automation.chat_summary_states
```

判断：

- `fact_writeback_queue` 持续增长：事实写回任务可能积压。
- `chat_summary_queue` 持续增长：聊天总结写回任务可能积压。
- 队列增长但对应 `*_worker_active=false`：优先排查 worker 是否未启动、异常退出或被阻塞。
- `chat_summary_states` 长期很高：可能有较多总结状态对象被保留，需要结合日志看是否有长时间未完成的总结。

### 第九步：看 A_Memorix 是否是正常增长

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

### 第十步：必要时看 tracemalloc

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
Get-Content logs\memory_diagnostics\memory_diagnostics.jsonl -Tail 50 > memory_diagnostics_tail.txt
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
| `timestamp` | 快照采集时的 Unix 时间戳，单位秒 | 用于和日志时间线对齐 |
| `process` | 主进程和子进程资源指标对象 | 进程级内存、句柄、连接或子进程异常 |
| `python` | Python 运行时指标对象 | GC、对象数量或 Python 层分配异常 |
| `asyncio` | asyncio 任务指标对象 | 后台任务堆积或协程挂起 |
| `heartflow` | HeartFlow/MaiSaka 会话指标对象 | 会话 runtime、消息缓存或二进制滞留 |
| `chat_manager` | ChatManager 消息接收缓存指标对象 | 聊天 session 或最后消息缓存滞留 |
| `websocket` | WebUI WebSocket 指标对象 | WebUI 连接或发送队列积压 |
| `media_tasks` | 图片、表情描述任务指标对象 | VLM 描述任务挂起或持有大二进制 |
| `memory_automation` | 记忆自动化写回服务指标对象 | 事实写回或聊天总结写回积压 |
| `a_memorix` | A_Memorix 记忆系统指标对象 | 向量库、embedding 缓存或 metadata 增长 |
| `tracemalloc` | Python 分配快照指标对象，仅启用时出现 | Python 层分配增长来源 |
| `collector` | 诊断采集器自身元信息对象 | 诊断开销或配置状态 |
| `collector.duration_ms` | 本轮诊断采集、组装快照的耗时，单位毫秒 | 诊断本身开销过高或采集被阻塞 |
| `collector.tracemalloc_enabled` | 本轮是否启用了 `tracemalloc` | 判断是否应出现 `tracemalloc` 字段 |
| `collector_errors` | 分区采集失败信息列表，仅失败时出现 | 某个模块状态不可读或采集逻辑异常 |
| `process.available` | 进程指标是否可用 | `psutil` 不可用或进程信息读取失败 |
| `process.reason` | 进程指标不可用的原因，仅失败时出现 | `psutil` 缺失或系统权限限制 |
| `process.rss_mb` | 主进程 RSS，当前驻留物理内存 | 主进程 Python 对象、native 内存、模型库或缓存增长 |
| `process.vms_mb` | 主进程 VMS，虚拟地址空间规模 | 地址空间保留、映射文件、native 库提交量增长；不要单独当作泄漏证据 |
| `process.uss_mb` | 主进程 USS，主进程独占内存 | MaiBot 主进程自身实际占用增长 |
| `process.thread_count` | 主进程线程数 | 线程池、阻塞任务或第三方库线程泄漏 |
| `process.open_files` | 主进程打开文件数量 | 文件句柄泄漏、日志或数据库文件未关闭 |
| `process.open_files_error` | 打开文件数量读取失败标记，仅失败时出现 | 系统权限限制或平台不支持 |
| `process.connections` | 主进程网络连接数量 | HTTP/WebSocket/插件连接未释放 |
| `process.connections_error` | 网络连接数量读取失败标记，仅失败时出现 | 系统权限限制或平台不支持 |
| `process.handle_count` | Windows 主进程句柄数，仅支持时出现 | 句柄泄漏、文件/管道/socket 未释放 |
| `process.fd_count` | 类 Unix 主进程文件描述符数，仅支持时出现 | FD 泄漏、socket 或文件未关闭 |
| `process.fd_count_error` | FD 数读取失败标记，仅失败时出现 | 系统权限限制或平台不支持 |
| `process.process_tree_rss_mb` | 主进程加所有子进程 RSS | 插件 runner、浏览器或其它子进程占用 |
| `process.process_tree_uss_mb` | 主进程加所有子进程 USS | 整个进程树独占内存增长 |
| `process.process_tree_vms_mb` | 主进程加所有子进程 VMS | 进程树虚拟地址空间增长 |
| `process.children` | 子进程汇总和高占用子进程明细对象 | 插件 runner、浏览器或其它子进程异常 |
| `process.children.available` | 子进程指标是否可用 | 子进程枚举失败或权限不足 |
| `process.children.reason` | 子进程指标不可用的原因，仅失败时出现 | 系统权限限制或进程已退出 |
| `process.children.count` | 子进程总数 | 插件 runner 或外部子进程数量异常 |
| `process.children.sampled_count` | 成功采集明细的子进程数量 | 子进程采集被跳过或进程频繁退出 |
| `process.children.skipped_count` | 子进程明细采集失败数量 | 子进程退出过快或权限不足 |
| `process.children.rss_mb` | 所有子进程 RSS 合计 | 子进程物理内存增长 |
| `process.children.uss_mb` | 所有子进程 USS 合计 | 子进程独占内存增长 |
| `process.children.vms_mb` | 所有子进程 VMS 合计 | 子进程虚拟地址空间增长 |
| `process.children.top` | 按 RSS 排序的高占用子进程列表 | 定位最占内存的子进程 |
| `process.children.top[].pid` | 高 RSS 子进程 PID | 定位具体子进程 |
| `process.children.top[].ppid` | 高 RSS 子进程父 PID | 定位进程树关系 |
| `process.children.top[].name` | 高 RSS 子进程名称 | 判断是插件 runner、浏览器还是其它进程 |
| `process.children.top[].status` | 高 RSS 子进程状态 | 子进程卡死、僵住或长期运行 |
| `process.children.top[].rss_mb` | 单个子进程 RSS | 单个插件或外部进程占用 |
| `process.children.top[].uss_mb` | 单个子进程 USS | 单个子进程自身实际占用 |
| `process.children.top[].vms_mb` | 单个子进程 VMS | 单个子进程虚拟地址空间增长 |
| `process.children.top[].cmdline` | 单个子进程命令行，最多保留前 6 项 | 定位具体 runner 或启动参数 |
| `python.gc_count` | Python GC 各代当前计数 | 短期对象分配压力、GC 触发频率异常 |
| `python.gc_threshold` | Python GC 各代阈值 | 判断当前 GC 策略是否过宽或被改动 |
| `python.object_count` | `gc.get_objects()` 对象数量，仅启用 `tracemalloc` 时采集 | Python 可 GC 对象总量增长 |
| `asyncio.task_count` | 当前事件循环任务总数 | 任务泄漏、后台循环过多 |
| `asyncio.top_task_names` | 按任务名聚合的任务数量列表 | 某类命名任务大量堆积 |
| `asyncio.top_task_names[].name` | 任务名称分布项名称 | 某类命名任务大量堆积 |
| `asyncio.top_task_names[].count` | 任务名称分布项数量 | 某类命名任务大量堆积 |
| `asyncio.top_coro_names` | 按协程名聚合的任务数量列表 | 某类协程大量堆积 |
| `asyncio.top_coro_names[].name` | 协程名称分布项名称 | 某类协程大量堆积 |
| `asyncio.top_coro_names[].count` | 协程名称分布项数量 | 某类协程大量堆积 |
| `asyncio.interesting_tasks` | 命中诊断关键词的任务列表 | 重点后台任务挂起或长期运行 |
| `asyncio.interesting_tasks[].name` | 命中关键词的任务名称 | memory、websocket、embedding、description 等相关任务挂起 |
| `asyncio.interesting_tasks[].coro` | 命中关键词的协程名称 | 定位挂起协程入口 |
| `asyncio.interesting_tasks[].done` | 命中关键词任务是否完成 | 完成任务仍滞留或长期未完成 |
| `asyncio.interesting_tasks[].cancelled` | 命中关键词任务是否取消 | 取消任务未清理或异常任务状态 |
| `heartflow.loaded` | HeartFlow 管理器模块是否已加载 | 未进入聊天运行态或模块未初始化 |
| `heartflow.runtime_count` | MaiSaka runtime 数 | 会话池无淘汰、群聊/私聊 session 增长 |
| `heartflow.lock_count` | 会话创建锁数量 | session churn 后锁对象累积 |
| `heartflow.scan_budget_remaining` | HeartFlow 二进制扫描剩余预算 | 扫描预算是否足够覆盖消息缓存 |
| `heartflow.totals` | 所有 runtime 的汇总指标对象 | 判断全局会话缓存、队列和二进制趋势 |
| `heartflow.totals.message_cache` | 所有 runtime 消息缓存总量 | 消息缓存未裁剪 |
| `heartflow.totals.source_messages` | 所有 runtime 源消息索引总量 | 消息对象被强引用 |
| `heartflow.totals.message_received_markers` | 所有 runtime 已接收消息标记数量 | 消息标记未清理 |
| `heartflow.totals.history_loop` | 所有 runtime 推理循环历史数量 | cycle detail 未裁剪 |
| `heartflow.totals.history_loop_estimated_mb` | 所有 runtime 推理循环历史估算大小 | cycle detail 内容过大 |
| `heartflow.totals.chat_history` | 所有 runtime 聊天历史数量 | 聊天历史未裁剪 |
| `heartflow.totals.internal_queue` | 所有 runtime 内部队列积压数量 | MaiSaka 内部处理阻塞 |
| `heartflow.totals.voice_binary_mb` | 所有 runtime 语音二进制估算 | 语音原始数据滞留 |
| `heartflow.totals.binary_mb` | 所有 runtime 图片、表情、语音二进制估算 | 大二进制消息滞留 |
| `heartflow.totals.binary_lower_bound` | 二进制估算是否可能偏低 | 扫描预算不足或存在未扫描 session |
| `heartflow.totals.binary_scan_budget` | 本轮全局消息扫描预算 | 配置过低导致估算偏保守 |
| `heartflow.totals.binary_scan_remaining` | 本轮全局消息扫描剩余预算 | 判断是否扫完整体缓存 |
| `heartflow.totals.binary_scanned_messages` | 本轮实际扫描消息数 | 判断二进制估算覆盖面 |
| `heartflow.totals.binary_skipped_messages` | 本轮未扫描消息数 | 未扫描消息中可能仍有大二进制 |
| `heartflow.totals.binary_scanned_sessions` | 本轮扫描过的 session 数 | 判断扫描是否覆盖主要会话 |
| `heartflow.totals.binary_truncated_sessions` | 因预算不足被截断扫描的 session 数 | 大 backlog 会话估算偏低 |
| `heartflow.totals.binary_unscanned_sessions` | 完全未扫描的 session 数 | 扫描预算为 0 或预算耗尽 |
| `heartflow.totals.reply_effect_pending` | 所有 runtime 回复效果待处理记录数 | 回复效果追踪对象滞留 |
| `heartflow.totals.reply_effect_timeout_tasks` | 所有 runtime 回复效果超时任务数 | 超时任务未释放 |
| `heartflow.top_sessions[]` | 按消息、源消息和历史循环数量排序的高占用会话列表 | 定位最可疑会话 |
| `heartflow.top_binary_sessions[]` | 按语音二进制和总二进制排序的高占用会话列表 | 定位二进制滞留会话 |
| `heartflow.top_sessions[].component_counts` | 单会话扫描到的消息组件类型计数，扫描时出现 | 判断是图片、表情、语音、转发等哪类对象占用 |
| `heartflow.top_sessions[].session_id` | 会话 ID | 定位具体 runtime |
| `heartflow.top_sessions[].session_name` | 会话名称 | 定位具体群聊或私聊 |
| `heartflow.top_sessions[].running` | runtime 是否处于运行态 | 停止态 runtime 是否仍保留大量对象 |
| `heartflow.top_sessions[].agent_state` | agent 内部状态字符串 | 会话停在异常状态或长期 stop/running |
| `heartflow.top_sessions[].message_cache` | 单会话消息缓存数量 | 单会话消息未裁剪 |
| `heartflow.top_sessions[].runtime_processed_index` | runtime 已处理到的消息下标 | 处理游标落后或处理停滞 |
| `heartflow.top_sessions[].expression_pending` | 表达学习待处理消息数量 | 表达学习消费者落后，可能阻止裁剪 |
| `heartflow.top_sessions[].expression_processed_index` | 表达学习已处理到的消息下标 | 表达学习处理停滞 |
| `heartflow.top_sessions[].source_messages` | 单会话源消息索引数量 | 源消息对象被保留 |
| `heartflow.top_sessions[].message_received_markers` | 单会话消息接收标记数量 | 接收标记未清理 |
| `heartflow.top_sessions[].history_loop` | 单会话推理循环历史数量 | 单会话 cycle detail 未裁剪 |
| `heartflow.top_sessions[].history_loop_estimated_bytes` | 单会话推理循环历史估算字节数 | 单会话 cycle detail 内容过大 |
| `heartflow.top_sessions[].history_loop_estimated_mb` | 单会话推理循环历史估算 MB | 单会话 cycle detail 内容过大 |
| `heartflow.top_sessions[].history_loop_sample_count` | 推理循环历史估算采样数量 | 估算样本过少导致误差 |
| `heartflow.top_sessions[].history_loop_average_bytes` | 推理循环历史单项平均估算字节数 | 单轮 cycle detail 过大 |
| `heartflow.top_sessions[].chat_history` | 单会话聊天历史数量 | 聊天历史未裁剪 |
| `heartflow.top_sessions[].internal_queue` | 单会话内部队列积压数量 | 会话内部处理阻塞 |
| `heartflow.top_sessions[].reply_effect_pending` | 单会话回复效果待处理记录数 | 回复效果记录滞留 |
| `heartflow.top_sessions[].reply_effect_timeout_tasks` | 单会话回复效果超时任务数 | 超时任务未释放 |
| `heartflow.top_sessions[].binary_bytes` | 单会话扫描到的二进制字节数，扫描时出现 | 单会话大二进制滞留 |
| `heartflow.top_sessions[].binary_mb` | 单会话扫描到的二进制 MB，扫描时出现 | 单会话大二进制滞留 |
| `heartflow.top_sessions[].voice_binary_bytes` | 单会话语音二进制字节数，扫描时出现 | 单会话语音原始数据滞留 |
| `heartflow.top_sessions[].voice_binary_mb` | 单会话语音二进制 MB，扫描时出现 | 单会话语音原始数据滞留 |
| `heartflow.top_sessions[].image_binary_bytes` | 单会话图片二进制字节数，扫描时出现 | 单会话图片原始数据滞留 |
| `heartflow.top_sessions[].image_binary_mb` | 单会话图片二进制 MB，扫描时出现 | 单会话图片原始数据滞留 |
| `heartflow.top_sessions[].emoji_binary_bytes` | 单会话表情二进制字节数，扫描时出现 | 单会话表情原始数据滞留 |
| `heartflow.top_sessions[].emoji_binary_mb` | 单会话表情二进制 MB，扫描时出现 | 单会话表情原始数据滞留 |
| `heartflow.top_sessions[].component_counts.<ComponentName>` | 某种消息组件数量，按实际类名动态出现 | 某类组件异常堆积 |
| `heartflow.top_sessions[].component_counts.ForwardDepthTruncated` | 转发组件递归深度被截断次数 | 转发消息层级过深，估算偏保守 |
| `heartflow.top_sessions[].component_counts.ForwardCycleSkipped` | 转发组件循环引用跳过次数 | 消息组件存在循环引用 |
| `heartflow.top_sessions[].binary_scan_skipped` | 单会话是否跳过二进制扫描 | 扫描预算为 0 或预算不足 |
| `heartflow.top_sessions[].binary_scan_messages` | 单会话实际扫描消息数 | 判断单会话估算覆盖面 |
| `heartflow.top_sessions[].binary_scan_truncated` | 单会话扫描是否被截断 | 单会话估算可能偏低 |
| `heartflow.top_sessions[].binary_scan_strategy` | 单会话扫描策略，目前为 `spread` | 说明采样覆盖首尾和中间消息 |
| `heartflow.top_sessions[].binary_scan_skipped_messages` | 单会话未扫描消息数 | 未扫描消息中可能仍有大二进制 |
| `heartflow.top_sessions[].binary_lower_bound` | 单会话二进制估算是否可能偏低 | 单会话扫描未覆盖全部消息 |
| `heartflow.top_binary_sessions[].*` | 与 `heartflow.top_sessions[].*` 相同，但按二进制占用排序 | 定位二进制占用最高的会话 |
| `chat_manager.loaded` | ChatManager 模块是否已加载 | 未进入消息接收运行态或模块未初始化 |
| `chat_manager.sessions` | ChatManager 当前 session 数 | 聊天 session 对象滞留 |
| `chat_manager.last_messages` | ChatManager 记录的最后消息数量 | 最后一条消息缓存增长 |
| `chat_manager.last_message_binary_mb` | 最后消息缓存中的二进制估算 | 最后一条图片、表情、语音被保留 |
| `chat_manager.last_message_voice_binary_mb` | 最后消息缓存中的语音二进制估算 | 最后一条语音原始数据被保留 |
| `websocket.unified` | 新 WebSocket 管理器指标对象 | WebUI 主 WebSocket 连接或队列异常 |
| `websocket.unified.loaded` | 新 WebSocket 管理器是否已加载 | WebUI WebSocket 模块未初始化 |
| `websocket.unified.connections` | 新 WebSocket 当前连接数 | WebUI 页面或客户端连接滞留 |
| `websocket.unified.total_send_queue` | 新 WebSocket 总发送队列长度 | 慢客户端消费不过来 |
| `websocket.unified.max_send_queue` | 单连接最大发送队列长度 | 某个慢客户端严重积压 |
| `websocket.unified.subscribed_connections` | 有订阅的 WebSocket 连接数 | WebUI 订阅连接未释放 |
| `websocket.unified.chat_session_mappings` | WebSocket 连接绑定的聊天 session 映射数量 | WebUI 会话映射未清理 |
| `websocket.legacy_logs` | 旧日志 WebSocket 指标对象 | 旧日志 WebSocket 连接滞留 |
| `websocket.legacy_logs.loaded` | 旧日志 WebSocket 模块是否已加载 | 旧日志通道未初始化 |
| `websocket.legacy_logs.active_connections` | 旧日志 WebSocket 活跃连接数 | 旧日志页面或连接滞留 |
| `media_tasks.image` | 图片描述任务指标对象 | 图片描述任务挂起或滞留 |
| `media_tasks.image.loaded` | 图片描述管理器是否已加载 | 图片系统未初始化 |
| `media_tasks.image.task_count` | 图片描述任务总数 | 图片描述任务字典未清理 |
| `media_tasks.image.pending_task_count` | 图片描述未完成任务数 | VLM 请求卡住或处理能力不足 |
| `media_tasks.image.estimated_binary_mb` | 图片 pending task 局部变量中二进制估算 | pending 图片任务持有大对象 |
| `media_tasks.image.done_task_count` | 图片描述已完成但仍在任务字典中的数量 | 已完成任务未移除 |
| `media_tasks.emoji` | 表情描述任务指标对象 | 表情描述任务挂起或滞留 |
| `media_tasks.emoji.loaded` | 表情描述管理器是否已加载 | 表情系统未初始化 |
| `media_tasks.emoji.task_count` | 表情描述任务总数 | 表情描述任务字典未清理 |
| `media_tasks.emoji.pending_task_count` | 表情描述未完成任务数 | VLM 请求卡住或处理能力不足 |
| `media_tasks.emoji.estimated_binary_mb` | 表情 pending task 局部变量中二进制估算 | pending 表情任务持有大对象 |
| `media_tasks.emoji.done_task_count` | 表情描述已完成但仍在任务字典中的数量 | 已完成任务未移除 |
| `memory_automation.loaded` | memory automation 服务模块是否已加载 | 记忆自动化服务未初始化 |
| `memory_automation.started` | memory automation 是否已启动 | worker 未启动或启动流程异常 |
| `memory_automation.fact_writeback_queue` | 事实写回队列长度 | 事实写回积压 |
| `memory_automation.fact_writeback_worker_active` | 事实写回 worker 是否仍在运行 | worker 异常退出或未启动 |
| `memory_automation.chat_summary_queue` | 聊天总结写回队列长度 | 聊天总结写回积压 |
| `memory_automation.chat_summary_worker_active` | 聊天总结写回 worker 是否仍在运行 | worker 异常退出或未启动 |
| `memory_automation.chat_summary_states` | 聊天总结状态对象数量 | 总结状态滞留或长期未完成 |
| `a_memorix.loaded` | A_Memorix host service 模块是否已加载 | A_Memorix 未初始化 |
| `a_memorix.enabled` | A_Memorix kernel 是否启用 | A_Memorix 配置关闭或 kernel 未创建 |
| `a_memorix.kernel_loaded` | A_Memorix kernel 是否已加载 | kernel 初始化失败或尚未启动 |
| `a_memorix.vector_store` | A_Memorix 向量存储指标对象 | 向量索引、缓冲或删除标记增长 |
| `a_memorix.vector_store.dimension` | 向量维度 | embedding 维度配置是否符合预期 |
| `a_memorix.vector_store.index_ntotal` | 主向量索引条目数 | 记忆库规模增长或主索引未回收 |
| `a_memorix.vector_store.fallback_ntotal` | fallback 向量索引条目数 | 索引训练前回放状态或 fallback 长期不释放 |
| `a_memorix.vector_store.is_trained` | 主向量索引是否已训练 | 索引长期未训练导致 fallback 累积 |
| `a_memorix.vector_store.bin_count` | 向量存储记录 bin 数量 | 记忆库规模增长 |
| `a_memorix.vector_store.known_hashes` | 已知向量哈希数量 | 去重索引随记忆增长 |
| `a_memorix.vector_store.deleted_ids` | 标记删除的向量 ID 数量 | 删除标记长期累积 |
| `a_memorix.vector_store.reservoir_buffer` | reservoir 训练缓冲数量 | 训练缓冲未消费 |
| `a_memorix.vector_store.write_buffer_ids` | 待写入向量 ID 缓冲数量 | 向量写入积压 |
| `a_memorix.embedding` | A_Memorix embedding 管理器指标对象 | embedding 缓存或编码错误增长 |
| `a_memorix.embedding.cache_enabled` | embedding 缓存是否启用 | 判断缓存字段是否应增长 |
| `a_memorix.embedding.global_text_cache` | 全局文本 embedding 缓存数量 | 全局文本缓存增长 |
| `a_memorix.embedding.global_dimension_cache` | 全局维度缓存数量 | 多维度 embedding 缓存增长 |
| `a_memorix.embedding.local_cache` | embedding manager 本地缓存数量 | 本地 embedding 缓存增长 |
| `a_memorix.embedding.total_encoded` | embedding 编码累计次数 | embedding 调用频率异常 |
| `a_memorix.embedding.total_errors` | embedding 编码累计错误次数 | embedding 服务异常导致重试或积压 |
| `a_memorix.metadata` | metadata store 统计对象 | 记忆元数据规模增长 |
| `a_memorix.metadata.paragraph_count` | 段落记忆数量 | 记忆库正常增长或段落未清理 |
| `a_memorix.metadata.entity_count` | 实体数量 | 实体抽取结果增长 |
| `a_memorix.metadata.relation_count` | 关系数量 | 关系抽取结果增长 |
| `a_memorix.metadata.stale_paragraph_mark_count` | 过期段落标记数量 | 过期标记未压缩或未清理 |
| `a_memorix.metadata.person_profile_refresh_pending_count` | 人物画像刷新待处理数量 | 人物画像刷新积压 |
| `a_memorix.metadata.person_profile_refresh_failed_count` | 人物画像刷新失败数量 | 人物画像刷新异常 |
| `a_memorix.metadata.total_words` | metadata 统计的总词数 | 记忆文本规模增长 |
| `a_memorix.metadata_error` | metadata 统计读取失败原因，仅失败时出现 | metadata store 异常 |
| `tracemalloc.current_mb` | 当前 `tracemalloc` 追踪到的 Python 分配量 | Python 层对象占用增长 |
| `tracemalloc.rss_growth_mb` | 当前 RSS 相对基线增长量 | 判断是否达到差异输出阈值 |
| `tracemalloc.rss_interval_growth_mb` | 当前 RSS 相对上一轮增长量 | 短周期内存增长趋势 |
| `tracemalloc.rss_baseline_growth_mb` | 当前 RSS 相对差异基线增长量 | 长周期内存增长趋势 |
| `tracemalloc.diff` | 达到阈值时的 Python 分配差异列表 | Python 层增长来源 |
| `tracemalloc.diff[].file` | 分配差异来源文件 | 定位 Python 分配源 |
| `tracemalloc.diff[].line` | 分配差异来源行号 | 定位 Python 分配源 |
| `tracemalloc.diff[].size_diff_mb` | 该位置相对基线增长的大小 | 主要 Python 分配热点 |
| `tracemalloc.diff[].count_diff` | 该位置相对基线增长的分配次数 | 小对象大量增长 |
| `tracemalloc.diff_baseline` | `diff` 使用的基线说明，达到阈值时出现 | 判断 diff 是相对上次告警还是重置点 |

## 注意事项

- 不要长期高频采样，例如每 10 秒采样一次只适合短时间复现。
- 不要长期默认开启 `tracemalloc`，除非正在深挖 Python 分配。
- `binary_mb` 是估算值，不是精确全量统计。
- `binary_lower_bound=true` 时，实际二进制占用可能高于日志值。
- RSS 不会总是立刻回落，Python 和 native allocator 都可能保留已申请的内存页。
- 判断问题时要看趋势，不要只看单次快照。
