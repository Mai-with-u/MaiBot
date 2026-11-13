# 群聊与私聊场景边界

`src/maisaka/scenes` 是群聊与私聊行为的归属目录。新增仅适用于一种聊天的规则时，优先修改对应场景，不向 runtime、Planner 或 Replyer 中追加类型判断。

## 目录和职责

```text
scenes/
  base.py                 # ChatScene 行为接口
  factory.py              # 明确类型 / 真实聊天流解析
  prompting.py            # 共享渲染格式与未绑定会话提示词
  mentions.py             # 文本中的回复、昵称、别名识别
  group/
    behavior.py           # 群聊配置、频率、触发与集成策略
    scheduling.py         # 动态门控、强制触发、空闲退避
    mentions.py           # 群聊 @ 标记、组件与概率开关
    prompting.py          # 群聊提示词、群名片、复古模板选择
    quoting.py            # 群聊引用概率公式
    memory.py             # 发言者、@ 对象、回复对象画像候选
  private/
    behavior.py           # 私聊配置、暂停、提及唤醒与集成策略
    scheduling.py         # 私聊 wait 唤醒和消息放行
    mentions.py           # 私聊提及识别
    prompting.py          # 私聊提示词、复古与补充自己发言模板
    quoting.py            # 私聊引用概率公式
    memory.py             # 当前私聊用户画像候选
```

## 运行方式

- `MaisakaHeartFlowChatting.scene` 根据已存在的真实聊天流选择无状态场景；每个运行时单独创建调度器。不要把会话计数器、定时器或历史写进共享场景实例。
- 群聊调度器持有动态门控与空闲退避；私聊调度器不创建这些组件。共享调度基类只处理防抖、静默消费、快速反应续接与队列投递。
- 场景属性每次读取当前配置，不在创建时保存配置值，保留热重载。
- Planner 和 Replyer 调用场景提示词接口；中、英、日记忆检索指引一起维护。人格、历史消息格式和工具说明仍由共享层组装。
- 场景决定引用频率与概率公式；窗口计数、窗口外目标处理、随机抽签、实际发送共用。
- 场景决定人物画像候选、Focus 准入以及跨类型记忆的方向开关；记忆查询、跨会话总开关及真实 session 校验仍在记忆服务入口。

## 共享边界与兼容

消息接收、真实 session 注册、存储、模型调用、工具执行、插件作用域、监控和运行生命周期继续共享。类型字段作为消息/工具上下文元数据传递，不代表业务行为仍混在一起。

Focus 的 group 是显式配置的共享会话组，可能同时包含私聊和群聊，不等于聊天类型的群聊。Focus 的共享编排保留；各成员准入通过其场景策略检查。

现有配置路径、prompt 模板占位符（包括历史名称 `group_chat_attention_block`）、工具作用域和公开的 `is_mentioned_bot_in_message` / `decide_quote` / 引用公式导入路径保留。旧入口只负责选择场景或转发，不再实现两种场景的分支算法。

`get_scene` 不接受未知类型。Replyer 需要通过真实聊天流确定场景，找不到会话时抛出错误，不把未知会话当作私聊。无会话的 CLI/通用 Planner 请求使用 `UnscopedPromptProfile`，不注入任一场景的注意事项或注意力漂移配置。

## Planner 正文动作补全

普通工具式 Planner 和 Focus 在返回正文但没有工具调用时，使用 `fast_model` 对正文中的最终行动意图做一次短判断，只选择 `reply` 或 `wait`。判断器只读取 Planner 本次上下文中最近五条真实聊天消息的可见文本和完整的 Planner 正文，排除通知、工具结果、内部提示和自动参考，不提供工具或图片。输入不额外截断，输出沿用模型配置，不额外设置 token 上限；请求总时限为 10 秒。超时和无效输出直接报错，不自动改成静默结束。

选择 `reply` 时，原始 Planner 正文直接作为 `reply_reference`；选择 `wait` 时补一个不带时间参数的调用。补出的调用与 Planner 输出绑定到同一逻辑回合，进入原有的工具校验、执行和监控路径，来源标记为“轻量判断器补全”。Focus 额外从真实工具声明的会话枚举中选择回复目标，目标不明确时选择 `wait`。已有工具调用、空正文、子代理任务，以及本来就用正文作为回复指导的被动回复和轻量私聊，不经过判断器。

## 实验性轻量私聊

WebUI 的「Bot 配置 → 实验性功能 → 轻量私聊」提供可搜索的下拉菜单，可按私聊名称、昵称或用户 ID 搜索已有私聊，选择后添加，移除后恢复原模式；无需手填平台和用户 ID。

对应配置项为 `experimental.lightweight_private_chats`，默认空列表关闭。如需直接编辑配置，可在 `[experimental]` 下配置：

```toml
lightweight_private_chats = [
    { platform = "qq", item_id = "123456", rule_type = "private" },
]
```

`item_id` 填对方用户 ID，平台与用户 ID 支持 `*`；仅匹配已注册的真实私聊。命中的私聊优先采用轻量模式，不进入 Focus 共享上下文，其他聊天继续使用原模式。每轮开始时固定模式，配置修改不改变进行中的 Planner/Replyer 协议。

轻量 Planner 直接输出自然语言回复指导，交给现有 Replyer；不需要调用 `reply`，默认回复完成后结束本轮。常驻 `query_memory(query)`、`query_person_profile(person_name)`、`view_forward_message(msg_id, path)`、`tool_search(query)` 和 `wait()`，仍遵守工具原有的启用条件。无需回复时可单独调用无参数的 `wait()`，静默结束本轮并等待新消息；不提供 Maisaka 子 Agent 工具。不额外限制工具查询轮数或每轮工具数量，沿用普通 Maisaka 的内部循环上限（当前为 16 轮）。空输出或执行失败保留原有错误记录，不伪造回复。

扩展工具（插件、MCP、联网等）先进入工具目录，Planner 通过 `tool_search` 按名称或关键词搜索，后续轮次再按完整定义调用，不预先展开全部参数。沿用 `experimental.tool_search_tail_loading`：关闭时将发现的工具加入后续请求的工具列表，开启时通过搜索结果加载完整定义。已发现工具的执行权限跟随当前上下文中的搜索记录，记录被裁切或工具不再可用时，需要重新搜索；未加载的扩展工具不会执行。`read_tool_result` 也可按需搜索，用于读取联网等工具返回的原始结果编号。工具目录仍受各 Provider 的启用条件、聊天范围和权限控制。

该模式跳过 Planner 的自动黑话、启发式记忆、人物画像和图片记忆注入，由显式查询补充必要信息。这里的自动注入指程序在模型调用前主动检索并加入参考材料，不是删除聊天历史或关闭记忆存储；图片记忆检索工具也未开放，因此轻量 Planner 不具备图片记忆召回能力。工具长结果的自动模型整理仍跳过。

回复补充器保留，负责引用、图片、表情和插件扩展参数的编排；Planner 只需在回复指导中用自然语言描述需求。插件工具调用与插件回复扩展是不同能力：前者搜索加载后调用，后者沿用被动回复链路。Replyer 的人设、发送、记录和既有生成流程继续复用。模型仍使用现有 Planner/Replyer 配置；本模式不自动更换模型，也不保证完全不支持工具调用的模型能执行查询。

私聊本来就不使用群聊频率额度、动态频控或空闲退避。轻量模式进一步移除 `wait`，回复后结束，等待新消息；保留私聊消息防抖和暂停开关。防抖用于合并连续短句，可通过 `chat.reply_timing.private_message_debounce_mode = "off"` 关闭。

## 后续分叉

修改私聊唤醒、上下文容量、回复风格或记忆偏好时，修改 `private/` 对应实现；群聊同理。涉及双方共同的防抖算法、存储或模型执行机制时，修改共享层。只有确实出现不同的推理执行协议时，才增加新的执行器接口，避免复制整个推理引擎。

## 验证

使用项目解释器执行测试，避免复制的虚拟环境启动器指向其他 checkout：

```powershell
uv run --no-sync python -m pytest pytests/maisaka pytests/plugin_runtime/test_reply_generation.py pytests/plugin_runtime/test_hook_reply_abort.py pytests/plugin_runtime/test_reply_extension_platform_scope.py tests/test_reply_supplement.py -q
```

`test_chat_scenes.py` 覆盖配置独立性、热重载、三种语言提示词、模板选择、未知会话、引用公式、方向性记忆权限、画像候选以及群聊/私聊提及差异。

## 聊天单次日程

普通群聊、私聊和 Focus 使用同一个 `schedule` 工具。轻量私聊也可登记日程；`wait` 仍只用于当前思考的结束或短暂等待。

```json
{"action": "add", "text": "提醒大家提交作业", "delay_seconds": 600}
```

也可指定未来的 `due_at`（ISO 8601，建议带时区），与 `delay_seconds` 不能同时填写。支持 `list`、`update`、`cancel`、`complete`，修改前先查询真实 `todo_id`。Focus 工具另外填写本组真实 `session_id`，其他模式自动绑定当前聊天。取消保留记录，完成归档；`list(include_completed=true)` 包含已完成、已取消历史。

日程由主程序持久化并每秒检查，不依赖桌宠是否启动。到期向原聊天投递内部任务并唤醒 Planner，包括正在定时等待的 Focus 组；Planner 结合最新上下文执行任务，完成后再归档。不会把任务投给其他群，也不会把跨群事项注入主人聊天的常驻待办上下文。创建日程不暂停当前轮次，也不代表已发送到期消息。

桌宠面板、主人 `desktop_pet_todo` 和 `schedule` 共用原来的事项文件与附件。桌宠是管理入口，主程序是唯一写入方。重启后补投递尚未确认的到期任务；`triggered` 只表示已投递，`failed` 保存错误，重新安排未来时间后才再次尝试。进程在投递成功与保存回执之间退出时可能重复投递，因此不提供严格一次执行保证。
