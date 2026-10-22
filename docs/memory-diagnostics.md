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
启动后建立基线，每十分钟对比一次，记录最多 20 个分配位置的存量及净变化；只保留前一个
内存快照，不保存完整快照文件。`delta_bytes` 和 `delta_count` 为两次快照间的净变化，
并非累计分配次数。不同进程 PID 的记录不能直接当作连续快照比较。

深入模式会增加分配开销、快照内存和 CPU 消耗；即使采集在独立线程，Python GIL 仍可能造成短暂
停顿，不应长期默认开启。SQLite、向量库等原生分配未必被 tracemalloc 统计。
同时观察 RSS、`traced_bytes`、`tracer_overhead_bytes` 与各项业务计数，不要仅凭 RSS 上涨断言泄漏。
本诊断不强制 GC、不调用 malloc_trim、不调整连接池、不清理业务对象。

排查时保留问题发生前后全部四个日志文件，并说明版本、启动时间、操作负载及是否开启深入模式。
日志没有请求正文，但代码文件名、线程 ID 和运行数量仍属于诊断信息，公开上传前应检查。
