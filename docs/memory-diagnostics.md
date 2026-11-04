# 内存诊断日志

在 `[log]` 中配置，修改后重启生效：

```toml
memory_diagnostics_enabled = true
memory_diagnostics_deep_enabled = false
memory_diagnostics_interval_seconds = 60.0
```

默认轻量采集；设 `memory_diagnostics_enabled = false` 完全关闭本诊断。
日志位于运行目录的 `logs/memory_diagnostics/memory.jsonl`，每个文件最多约 5 MiB，
保留三个备份，加当前文件共约 20 MiB。不会广播到 WebUI，不记录聊天正文、图片 Base64、
会话 ID、密钥、源码行或对象 repr。记录会立即 flush，但 SIGKILL 之后无法补写现场。

## 字段与限制

schema 2 新增 Python 版本、平台与追踪器归属信息，旧有顶层 RSS 仍为本轮诊断前读数。

- `rss_bytes`：当前进程常驻内存，不含插件子进程。
- Linux 的 `anonymous_rss_bytes`、`swap_bytes` 与 Windows 的 `private_commit_bytes`
  含义不同，不要直接相互对比。`python_threads` 只统计 Python 线程；Linux 另有 `process_threads`。
- `databases`：已加载主库的连接池状态、已加载记忆库的连接数与最多 64 个所属线程状态。
  诊断不会初始化数据库或创建连接；锁忙时标记 `memory_connections_busy`，不等待业务。
- `runtime`：主循环任务数、聊天流数、活跃实例数、历史条数、缓存消息数、待处理轮次数。
  主循环异步发布统计，`sampled_at` 用于检查新鲜度；若循环卡住，进程指标仍可继续记录，
  但运行时计数可能过期。这里只统计主循环任务与指定队列，并非全程序所有任务和队列。
- 历史计数最多扫描 256 个活跃实例；`counts_truncated` 表示计数不完整。
  图片只抽查至多 512 条历史、2048 个顶层组件，`sampled_image_bytes` 是这些图片的字节长度之和，
  不递归展开转发，不包含所有图片缓存，也不做共享字节去重，不能视作进程图片总内存。
- `collection_ms`：采集耗时；运行时回调有独立耗时字段。
- `error_type`：采集异常类型；诊断不会因采集失败中断机器人业务。

## 深入诊断

设置 `memory_diagnostics_deep_enabled = true` 后重启，启用一帧 traceback 的 tracemalloc。
启动后建立基线，每十分钟采集一次。schema 2 不再常驻完整快照：快照按代码行聚合后立即释放，
仅保留上一轮和首轮基线的分配大小、数量。不会保存完整快照文件。

- `allocations.largest_locations`：当前占用最大的 20 个位置，稳定的大对象也能看到。
- `growing_locations`：相对上一轮正增长最大的 20 个位置。
- `baseline_growing_locations`：相对启动后首次深度采样正增长最大的 20 个位置。
- `locations`：兼容原字段，首轮为存量排行，之后为变化绝对值排行，包含已经完全释放的位置。
- `sampled_at`、`previous_sampled_at`、`baseline_sampled_at`：各个对比点的时间。
- `traced_bytes`、`traced_peak_bytes`：本轮快照前的追踪存量与追踪期间峰值；峰值没有每轮清零。
- `tracer_overhead_bytes`：追踪器内部元数据开销，不能当作全部诊断开销。
- `traceback_limit`：实际使用的追踪栈深度；已存在的外部追踪器不会被重启或停止。

`delta_bytes` 和 `delta_count` 是净变化，不是累计分配次数。不同 PID 不能作为连续样本比较。
一帧 traceback 仍可能只显示 JSON、Base64 等分配入口，无法还原业务调用链。

## Linux 内存映射（轻量模式也采集）

每轮读取 `memory_maps.rollup`（`/proc/self/smaps_rollup`），记录 RSS、PSS、私有/共享干净页和脏页、
匿名页、交换区，以及内核提供的匿名/文件/共享内存 PSS。可选字段未提供时省略，不能按零占用理解。
字段语义参考 [Linux 内核 proc 文档](https://docs.kernel.org/filesystems/proc.html)。

启动时及每十分钟流式读取完整 `smaps`，不依赖是否启用深度追踪：

- `categories`：heap、stack、anonymous_private、anonymous_shared、shared_memory、file_private、
  file_shared、kernel_special 各类映射的数量和字节汇总。
- `category_delta_bytes`：相对上一份成功的分类汇总的变化，`previous_sampled_at` 给出对比时间。
- `largest_mappings`：RSS 最大的 20 段，包含类别、权限、各内存指标和文件映射的 basename，
  不记录完整路径或原始地址。`mapping_id` 为映射头的摘要，仅用于同一进程内辅助关联；
  地址复用、拆分、合并会影响关联，不能视为分配对象 ID。
- `mapping_count`、`largest_mappings_truncated`：全量映射数与排行截断标记；分类汇总不截断。
- `rollup_error`、`smaps_error`：读取失败的类型和 errno；部分结果不会冒充完整分类。
  Windows 明确记录 `supported=false`，继续采集 Windows 进程和深度诊断指标。

分类描述的是映射，不是分配器：file_private 中也可能含写时复制的匿名页，匿名映射也可能包括
线程栈和原生分配，并非所有线程栈都有 stack 名称。RSS、PSS、Anonymous、Private_Dirty 等有重叠，
不能把这些字段相加。rollup 与完整映射扫描不是原子快照，业务运行时数值可能略有差异。

## 诊断自身的影响

`diagnostics` 保存各阶段的进程内存、追踪分配和追踪器开销：

1. `before`：映射与深度诊断前。
2. `after_maps`：读取内存映射后；`maps_ms` 为耗时。
3. `after_snapshot`：生成完整快照后；`snapshot_ms` 为耗时。
4. `after_aggregation`：按行聚合后，完整快照尚在内存中。
5. `after_snapshot_release`：释放完整快照和统计列表后；`aggregation_ms` 包括聚合及释放。
6. `after_deep_cleanup`：深度采集函数退出时。
7. `after`：退出采集与异常处理作用域后的最终读数；`rss_change_bytes` 为与 before 的差值。

异常时已完成的阶段仍保留，最终 after 读数在异常 traceback 释放后采集。
这些是**进程级阶段读数，不是诊断器独占内存**：并行业务也可能分配/释放，且释放对象未必令 RSS
立即下降。`peak_rss_bytes` 是进程历史高水位，不是本轮峰值；阶段读数也不能保证捕捉阶段内部瞬时峰值。
JSON 编码、日志写入发生在最后的读数之后，不包含在这些阶段差值里。

排查顺序：先比较同 PID 的分类增长，再看深度采样阶段的 RSS 变化和 tracemalloc 排行。
若匿名 RSS 大涨而追踪分配变化很小，仍需原生分配调用栈分析来区分原生存活分配、分配器保留和碎片；
本日志不声称可以给所有原生内存定位到业务代码行。

深入模式会增加分配开销、快照内存和 CPU 消耗；即使采集在独立线程，Python GIL 仍可能造成短暂
停顿，不应长期默认开启。SQLite、向量库等原生分配未必被 tracemalloc 统计。
同时观察 RSS、`traced_bytes`、`tracer_overhead_bytes` 与各项业务计数，不要仅凭 RSS 上涨断言泄漏。
本诊断不强制 GC、不调用 malloc_trim、不调整连接池、不清理业务对象。

排查时保留问题发生前后全部四个日志文件，并说明版本、启动时间、操作负载及是否开启深入模式。
日志没有请求正文，但代码文件名、映射文件 basename、线程 ID 和运行数量仍属于诊断信息，公开上传前应检查。
