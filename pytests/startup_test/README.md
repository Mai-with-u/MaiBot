# Graceful shutdown regression tests

## 本地运行

使用已有项目开发依赖，从仓库根目录执行：

```sh
python -m pytest pytests/startup_test/test_graceful_shutdown.py pytests/A_memorix_test/test_host_shutdown_failure_propagation.py -q -p no:cacheprovider
```

不需要真实模型、QQ、配置或生产数据库。`shutdown_fixture.py` 用 AST 加载实际
`bot.py` 生命周期函数与 Worker 入口，业务服务为替身，Uvicorn 信号上下文是真实实现。
Host/Kernel 失败传播测试在新解释器中运行；`memory_shutdown_fixture.py` 在导入真实
A_Memorix 生命周期前隔离宿主配置、业务数据库和网络，避免污染其他测试的模块缓存。
存储替身只证明调用顺序/错误传播，其标记不是实际向量/图落盘证据。

## 覆盖与边界

- Runner 单次转发、等待、超时失败、restart=42 与停止竞争。
- Worker 重复 SIGTERM/SIGINT、Uvicorn handler 恢复、early-startup 停止。
- 初始化尚未完成、初始化末尾正常返回、初始化刚完成、scheduler 发布前/后及已经启动时
  的停止请求交接。任务发布后再次消费持久停止标记，已排队的回调不能重复取消同一任务。
- A_Memorix stop 恰好一次且早于剩余任务取消；关闭失败不能输出成功标记。
- 真实 Host → Kernel 的 persist/metadata close 异常传播及 writer-lock 释放。

四项 POSIX 子进程信号测试在 Windows 明确 skipped；其他 Windows 信号测试用
`signal.raise_signal`，不把 `terminate()` 冒充 POSIX SIGTERM。发布边界测试在本地
fixture/任务上注入时序，不连接或调试生产进程。不包含 fork 专用 workflow、固定 Git SHA、
证据 observer、JUnit 搬运或 Docker acceptance harness。

## 关闭语义

Worker 合作式总预算为 50 秒；其中 A_Memorix stop 单步骤最多 30 秒，实际还受剩余总预算限制。
超时会取消协程并失败退出：若取消发生在后台/manager 退出阶段，尚未进入 persist/close，
就不能宣称数据已落盘；本修复不重新设计 A_Memorix 的取消/持久化协议。

合作式超时不能强制中断阻塞 I/O 或拒绝取消的协程。Runner 收到外部停止请求后有独立
60 秒截止，必要时强杀 Worker 并返回失败；Compose 为此保留 70 秒宽限期。直接运行
Worker、无外部停止请求的内部关闭，以及 Windows 无控制台 CTRL_BREAK 投递，都不能
无条件套用这一进程级保证。

成功的 restart 请求保留退出码 42；清理失败返回非零，不把“进程已退出”当作持久化成功。
Runner 的循环停止判定与 `Popen()` 不是原子操作：判定后才到达的信号可能让一个 Worker
短暂启动，随后立即被通知关闭；本修复不承诺停止与进程创建之间的零窗口屏障。
Worker 生命周期中统一拥有信号并临时替换 Uvicorn capture_signals，退出时恢复；升级
Uvicorn 后应重新验证这一兼容点。
