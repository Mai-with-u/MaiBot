# Hook 主动中止与回复失败类型

命名 Hook 沿用 `action: "continue" | "abort"` 控制流程。对于允许中止的 Hook，
blocking 处理器可以返回：

```python
return {
    "action": "abort",
    "custom_result": {"reason": "目标消息已被同一发送者的新消息取代，请选择最新消息。"},
}
```

`custom_result.reason` 为可选字符串，是所有允许中止的 Hook 共用的原因约定。
原因应说明拦截条件，必要时说明调用方可以怎样调整；不要把原因写入回复正文。
未提供原因的旧插件仍可中止。观察型处理器不能控制流程。

Host 在第一个有效中止后停止调度后续处理器，并记录该处理器的完整名称及原因。
处理器执行异常仍走原有 `error_policy`；若策略为 `abort`，中止原因记录实际异常。

## 回复与发送

- `maisaka.replyer.before_request`：生成前取消本次回复，不调用模型。
- `maisaka.replyer.before_model_request`：实际模型及请求内容确定后取消本次回复，
  不发送模型请求，不触发模型重试、切换或故障惩罚。
- `maisaka.replyer.after_response`：模型生成完成后中止本次回复；中止优先于
  `modified_kwargs` 中的正文改写与 `retry`，不再进入 Replyer 内部的重生成。
- `maisaka.reply.before_post_process`：取消本次回复，不再执行正文后处理和发送。
- `emoji.maisaka.before_select` / `emoji.maisaka.after_select`：取消本次表情发送。
- `send_service.after_build_message`：待发送消息构建完成后取消发送。
- `send_service.before_send`：调用平台发送前取消发送。

发送服务原有调用默认保留取消后返回 `None` 的行为。需要获知中止详情的内部调用方
可以传入 `raise_on_hook_abort=True`，接收携带 Hook 名称、处理器名称及原因的
`HookAbortedError`；reply 和 send_emoji 工具使用此方式将发送拦截反馈给 Planner。
分段发送中途被拦截时停止后续发送，已送达的消息仍保留在工具回执中。

reply 和 send_emoji 工具共用同一个中止结果构造器，结构化失败结果包含
`msg_id`、`error_code`、`reason` 与 `message`，
主动中止另含 `hook_abort: {hook_name, handler_name, reason}`：

| error_code | 含义 |
| --- | --- |
| `model_empty_response` | 模型本次生成的正文为空，Hook 未补充正文 |
| `plugin_empty_response` | 模型有正文，经 Hook 处理后变为空；不等同于明确中止 |
| `plugin_aborted` | 插件通过允许中止的 Hook 明确取消本次回复或发送 |
| `generation_failed` | 其他生成失败，原因见 `reason` |

以上内容进入 Planner 的工具结果历史；主动中止会提示根据原因调整目标或停止回复，
避免原样重复调用。它取消本次调用，不建立跨调用的消息封禁或固定时长缓存。
旧插件清空 `response` 的行为仍受支持，但不会被推断为明确的 `abort`，其私有原因字段
也不会被自动读取；应迁移到上述公共约定。

## 各业务的中止范围

中止信息统一为 Hook 名称、处理器名称和原因；业务代码在自己的操作边界处理它，
不将所有中止都解释为取消回复。

| 检查点 | 中止范围与原因去向 |
| --- | --- |
| `chat.receive.before_process` / `after_process` | 停止处理当前入站消息，日志记录来源和原因 |
| `chat.command.before_execute` | 跳过当前命令，日志记录来源和原因 |
| `expression.select.before_select` / `after_selection` | 跳过表达选择，回复仍可继续；原因保留在选择结果和生成指标中 |
| `expression.learn.after_extract` | 跳过当前学习批次，原因进入学习状态和日志 |
| `expression.learn.before_upsert` | 跳过当前候选的写入，原因进入批次学习详情和日志 |
| `jargon.extract.before_persist` / `jargon.inference.before_finalize` | 取消本次写入或写回，日志记录来源和原因 |
| `emoji.register.after_build_description` | 取消本次表情注册，日志记录来源和原因 |

旧表情 Hook 的 `abort_message` 仅在调度器处兼容转换为统一原因，
新插件使用 `custom_result.reason`，同时提供时优先使用新字段。
Planner Hook 仍不开放中止；发送完成、桌宠状态等观察通知也不可中止。
