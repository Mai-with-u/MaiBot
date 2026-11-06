# 聊天 Replyer 的创意模式与插件兼容

“聊天 → 聊天设置 → 创意模式”对应 `chat.creative_mode`，默认开启。普通模型关闭时继续使用常规多消息模板，开启时使用通用复古模板：人设、群聊注意事项和注意力提示放在 system，聊天记录和回复要求组织成一条 user 消息。表达习惯、关键词反应、附件参考、重复回复提醒和插件要求均保留。

每次实际选择模型后，名称或模型标识包含 `mai_replier` / `mai-replier`，或等于 `v2p1-contract` / `v2p1-raw` 的模型，使用 Mai Replier 专用适配，不受创意模式及超级创意模式开关影响，不切换生成语言，也不追加翻译请求。专用适配保持 persona-only system，不注入表达习惯、关键词反应等训练外内容，群聊固定使用标准复古模板。插件临时指定模型、候选模型切换和重新生成都会重新判断，不会改写全局配置。

原 `debug.replyer_retro_prompt` 已移除，不再参与决策。此前手动使用复古提示词的普通模型，由创意模式控制；新配置默认开启，已有配置保留原开关值。插件长文能力 `maisaka.reply.generate` 使用独立写作模板，不受上述聊天模式影响。

## 实验性超级创意模式

实验性功能中的 `experimental.super_creative_mode` 默认关闭。生成语言 `experimental.super_creative_language` 可选 `ja-JP`（日语，默认）、`ru-RU`（俄语）、`fr-FR`（法语）、`zh-classical`（中文文言文）。复用现有模板结构、段落及占位符。日语使用已有译本；俄语、法语只在 `src/chat/replyer/creative_language.py` 中保存固定文案差异，先替换模板固定行，再填入中文材料，不另存整套模板。生成语言要求和翻译指令也集中在该文件。

除 Mai Replier 外，翻译统一使用本次实际 Replyer 模型，将所选语言的正文翻译为中文。请求只有一条 user 消息，将翻译说明和“待翻译内容：{text}”合并发送，不设置 system、不拆分消息；已经是中文的内容按提示保留。翻译错误或空译文会明确失败，不发送原文。每次正常回复包含生成与翻译两次模型请求。

译文进入 after_response Hook、校验和发送；重生成会重新执行两个阶段。生成语言在一次生成流程内固定。超级创意模式优先于回复缓存模式，不复用 Planner 模型。普通创意模式和实际模型适配仍决定模板结构。

## 实验性回复缓存模式

`experimental.reply_cache`（“实验性功能 → 回复缓存模式”）默认关闭，独立于被动式 replyer。开启后，普通聊天与 Focus 的回复复用本轮实际成功的 Planner 请求快照及其模型，在原上下文后以 assistant 消息追加最新的 Planner 回复指导，再追加一条合并消息（不重复包含该指导），包含创意模式 Replyer 的完整 system/user 内容及图片。原 Planner 消息、工具定义及模型参数保持不变；不使用 Replyer 模型配置，关闭创意模式或选中 Mai Replier 模型也仍使用通用创意模板。长文生成不受此开关影响。

Replyer Hook 仍可改写回复要求和创意模板内容，重生成时仍基于同一份 Planner 前缀重新构造尾部；缓存模式不接受 Hook 切换任务模型。缺少 Planner 快照或模型返回工具调用时明确失败，不回退到独立 Replyer。实际缓存命中取决于服务端，沿用回复日志中的缓存统计观察。已有插件准备、发送前处理及 stop 行为不变。

## Hook 契约

- `maisaka.replyer.before_request` 保留原参数，可以修改 `task_name`、`model_name`、`extra_prompt` 和 `reply_tool_args`。此时尚未选定实际模型，因此首次调用中的 `selected_expression_ids` 仅包含调用方显式传入的编号；自动选择的结果在模型请求前 Hook 中读取。重试时该字段可能反映上一次尝试。
- `maisaka.replyer.before_model_request` 保留顶层参数和 Context Item 协议。实际模板已构建完成，插件可以继续修改 `items`。`selected_expression_ids` 对应实际尝试，Mai Replier 为 `[]`。
- 模型请求前 Hook 的 `reply_tool_args["_replyer_prompt_profile"]` 提供模式标识：`standard`、`retro`、`mai_replier` 或 `document`。该字段只供读取，由主程序覆盖填充，不能用于强制更换模板；不会作为额外顶层参数传给旧 Hook 处理函数。
- `maisaka.replyer.after_response` 的正文改写、输出 Item 改写、重试和重试原因保持原有协议。插件追加要求和重生成约束在两种复古模板中都保留。

插件应优先通过 `extra_prompt` 追加回复要求。直接处理 Context Items 的插件需要识别模式：复古模板中的聊天历史已经合并到 user 消息中，不能假定每一条历史发言都是独立 Item，也不能依赖某段固定提示词的位置。插件直接改写最终 Items 时仍拥有原有控制权，主程序不会在 Hook 后重新删减插件内容。


## 回复生成依赖调度

“实验性功能 → 被动式 replyer”（`experimental.passive_replyer`）默认关闭。关闭时保留原有 `reply` 工具流程，插件 `prepare` 在正文生成前执行，支持返回 `extra_prompt`。开启后，普通聊天由 Planner 的最终发言指导启动回复，正文与回复补充器并行生成。补充器根据指导、上下文资源和插件 schema 选择目标、附件、@、表情及插件参数；不生成正文、不调用工具。补充结果无效时携带错误重生成一次，再失败则终止本次回复。

回复扩展的 `description` 向 Planner 展示当前会话可用的能力，参数 schema 提供给补充器。平台、会话范围和组件开关实时生效，不写死特定插件能力。专注模式继续使用 reply 工具。

`generation_dependencies(model_info)` 按实际模型声明依赖：Mai Replier 在构造上下文前等待补充器确定回复目标，其他模型的正文不等待目标。补充器选择目标不强制引用，引用仍遵守发送规则。

扩展通过 `generation_prompt` 声明不依赖本轮参数的条件式正文要求。汇合后 `prepare` 校验选中插件参数，只返回空对象；正文生成 Hook 无法读取尚未生成的插件参数。发送前执行消息转换；失败或取消时回收补充任务，不让后台任务越过发送边界。

监控保留回复正文、补充器编排和插件参数，成功发送计数沿用统一执行入口。

开关从下一轮思考生效，一轮内提示词、工具可见性和输出处理保持一致；Focus 始终使用原有工具流程。stop 的直接终止与上下文隔离行为对两种普通聊天模式一致。

文言文模式复用中文原模板并追加文言文输出要求，再由同一 Replyer 模型翻译成现代白话中文；仍使用单条 user 翻译请求，Mai Replier 不受影响。
