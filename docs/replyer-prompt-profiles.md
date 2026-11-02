# 聊天 Replyer 的创意模式与插件兼容

“聊天 → 聊天设置 → 创意模式”对应 `chat.creative_mode`，默认关闭。普通模型关闭时继续使用常规多消息模板，开启时使用通用复古模板：人设、群聊注意事项和注意力提示放在 system，聊天记录和回复要求组织成一条 user 消息。表达习惯、关键词反应、附件参考、重复回复提醒和插件要求均保留。

每次实际选择模型后，名称或模型标识包含 `mai_replier` / `mai-replier`，或等于 `v2p1-contract` / `v2p1-raw` 的模型，使用 Mai Replier 专用适配，不受创意模式开关影响。专用适配保持 persona-only system，不注入表达习惯、关键词反应等训练外内容，群聊固定使用标准复古模板。插件临时指定模型、候选模型切换和重新生成都会重新判断，不会改写全局配置。

原 `debug.replyer_retro_prompt` 已移除，不再参与决策。此前手动使用复古提示词的普通模型，需要在聊天设置中开启创意模式。插件长文能力 `maisaka.reply.generate` 使用独立写作模板，不受上述聊天模式影响。

## Hook 契约

- `maisaka.replyer.before_request` 保留原参数，可以修改 `task_name`、`model_name`、`extra_prompt` 和 `reply_tool_args`。此时尚未选定实际模型，因此首次调用中的 `selected_expression_ids` 仅包含调用方显式传入的编号；自动选择的结果在模型请求前 Hook 中读取。重试时该字段可能反映上一次尝试。
- `maisaka.replyer.before_model_request` 保留顶层参数和 Context Item 协议。实际模板已构建完成，插件可以继续修改 `items`。`selected_expression_ids` 对应实际尝试，Mai Replier 为 `[]`。
- 模型请求前 Hook 的 `reply_tool_args["_replyer_prompt_profile"]` 提供模式标识：`standard`、`retro`、`mai_replier` 或 `document`。该字段只供读取，由主程序覆盖填充，不能用于强制更换模板；不会作为额外顶层参数传给旧 Hook 处理函数。
- `maisaka.replyer.after_response` 的正文改写、输出 Item 改写、重试和重试原因保持原有协议。插件追加要求和重生成约束在两种复古模板中都保留。

插件应优先通过 `extra_prompt` 追加回复要求。直接处理 Context Items 的插件需要识别模式：复古模板中的聊天历史已经合并到 user 消息中，不能假定每一条历史发言都是独立 Item，也不能依赖某段固定提示词的位置。插件直接改写最终 Items 时仍拥有原有控制权，主程序不会在 Hook 后重新删减插件内容。
