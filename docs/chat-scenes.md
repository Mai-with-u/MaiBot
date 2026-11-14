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

## 私聊 Planner 模板

普通私聊使用 `maisaka_chat_private`，开启被动式 Replyer 时使用 `maisaka_chat_private_passive`。两套模板围绕当前用户的消息、未完成请求和连续对话做决策，分别沿用 `reply` 工具和自然语言回复指导的输出协议；私聊注意事项、记忆查询规则、注意力漂移与工具权限继续按私聊场景注入。

群聊和未绑定会话继续使用 `maisaka_chat` / `maisaka_chat_passive`。Focus 使用 `maisaka_chat_focus`，共享组中的私聊也遵守 Focus 的统一协议。中英日三套私聊模板均可在 Prompt 管理页独立编辑；已有通用 Planner 自定义模板不会自动应用到新的私聊模板。

## Planner 正文动作补全

普通工具式 Planner 和 Focus 在返回正文但没有工具调用时，使用 `fast_model` 对正文中的最终行动意图做一次短判断，只选择 `reply` 或 `wait`。判断器只读取 Planner 本次上下文中最近五条真实聊天消息的可见文本和完整的 Planner 正文，排除通知、工具结果、内部提示和自动参考，不提供工具或图片。输入不额外截断，输出沿用模型配置，不额外设置 token 上限；请求总时限为 10 秒。超时和无效输出直接报错，不自动改成静默结束。

选择 `reply` 时，原始 Planner 正文直接作为 `chat_anlyze`；选择 `wait` 时补一个不带时间参数的调用。补出的调用与 Planner 输出绑定到同一逻辑回合，进入原有的工具校验、执行和监控路径，来源标记为“轻量判断器补全”。Focus 额外从真实工具声明的会话枚举中选择回复目标，目标不明确时选择 `wait`。已有工具调用、空正文、子代理任务，以及本来就用正文作为回复指导的被动回复，不经过判断器。

## 后续分叉

修改私聊唤醒、上下文容量、回复风格或记忆偏好时，修改 `private/` 对应实现；群聊同理。涉及双方共同的防抖算法、存储或模型执行机制时，修改共享层。只有确实出现不同的推理执行协议时，才增加新的执行器接口，避免复制整个推理引擎。

## 验证

使用项目解释器执行测试，避免复制的虚拟环境启动器指向其他 checkout：

```powershell
uv run --no-sync python -m pytest pytests/maisaka pytests/plugin_runtime/test_reply_generation.py pytests/plugin_runtime/test_hook_reply_abort.py pytests/plugin_runtime/test_reply_extension_platform_scope.py tests/test_reply_supplement.py -q
```

`test_chat_scenes.py` 覆盖配置独立性、热重载、三种语言提示词、模板选择、未知会话、引用公式、方向性记忆权限、画像候选以及群聊/私聊提及差异。

## 聊天单次日程

普通群聊、私聊和 Focus 使用同一个 `schedule` 工具。`wait` 仍只用于当前思考的结束或短暂等待。

```json
{"action": "add", "text": "提醒大家提交作业", "delay_seconds": 600}
```

也可指定未来的 `due_at`（ISO 8601，建议带时区），与 `delay_seconds` 不能同时填写。支持 `list`、`update`、`cancel`、`complete`，修改前先查询真实 `todo_id`。Focus 工具另外填写本组真实 `session_id`，其他模式自动绑定当前聊天。取消保留记录，完成归档；`list(include_completed=true)` 包含已完成、已取消历史。

日程由主程序持久化并每秒检查，不依赖桌宠是否启动。到期向原聊天投递内部任务并唤醒 Planner，包括正在定时等待的 Focus 组；Planner 结合最新上下文执行任务，完成后再归档。不会把任务投给其他群，也不会把跨群事项注入主人聊天的常驻待办上下文。创建日程不暂停当前轮次，也不代表已发送到期消息。

桌宠面板、主人 `desktop_pet_todo` 和 `schedule` 共用原来的事项文件与附件。桌宠是管理入口，主程序是唯一写入方。重启后补投递尚未确认的到期任务；`triggered` 只表示已投递，`failed` 保存错误，重新安排未来时间后才再次尝试。进程在投递成功与保存回执之间退出时可能重复投递，因此不提供严格一次执行保证。

## Prompt 用途分类

Prompt 管理页可筛选「主要 Prompt」「指令 Prompt」或全部。内置文件放在 `prompts/{locale}/main/` 和 `prompts/{locale}/instructions/`，用途直接按目录识别，不增加元信息字段，与高级开关相互独立。

主要 Prompt 定义人格、任务职责、输出方式和场景规则，包括普通、Focus、被动回复以及独立子代理的系统模板。工具用途与参数由工具声明维护，主要模板只保留跨工具协作和消息归属规则。

指令 Prompt 由现有加载器 `load_prompt` 按场景读取，可单独编辑和保存自定义版本：

| 场景 | 模板 |
| --- | --- |
| 每轮行动提醒 | `planner_turn_action`、`planner_turn_passive`、`planner_turn_focus` |
| 记忆与委派策略 | `planner_memory_group`、`planner_memory_private`、`planner_subagent_policy` |
| 等待结束、新消息重规划 | `planner_wait_completed`、`planner_replan` |
| 日程到期 | `schedule_due` |
| 子代理最后一轮、结果返回 | `subagent_final_round`、`subagent_finished` |

模板名称、版本 ID 与旧 API 地址保持原样；文件目录按用途整理，分类不改变消息的 system/user/tool 角色，也不改变触发条件。群聊与私聊的记忆策略保持分离。三种语言的新增模板与变量同步维护。

### 文件发现与自定义 Prompt

内置模板递归扫描 `prompts/{locale}/`，按模板名加载，不依赖嵌套深度。同一语言下模板名必须唯一；重名时明确报错。管理页按文件所在的 `instructions` 目录识别指令，其余归主要模板，不增加分类元信息字段。

自定义覆盖和历史继续使用原路径 `data/custom_prompts/{locale}/{存储名}.prompt` 与 `.versions/{存储名}/`。内置文件挪动目录不影响覆盖匹配，无需迁移用户正文、清单或启用状态；旧模板名、历史别名和 API 地址继续有效。


### 模板目录分类

各语言使用相同的目录结构，`main/` 保留主要模板，`instructions/` 保留补充指令，下面按模块归类：

| 子目录 | 内容 |
| --- | --- |
| `planner/` | 聊天规划、Focus、动作判断与回合控制；私聊主模板放在 `main/planner/private/` |
| `reply/` | 回复生成、缓存、附件和回复补充；群聊、私聊专用模板分别放在 `group/`、`private/` |
| `agent/` | 外接 Agent、任务规划和子代理 |
| `memory/` | 长期记忆总结、去重、修改与召回规则 |
| `context/` | 普通及 Focus 上下文压缩 |
| `learning/` | 表达、黑话与长文学习 |
| `media/` | 图片描述及表情分析、筛选、标签与替换 |
| `desktop_pet/` | 桌宠探索、唤醒、屏幕和待办上下文 |
| `webui/` | WebUI AI 搜索与操作指导 |
| `schedule/` | 日程到期指令 |

各分类的 `.meta.toml` 与模板放在同一目录，保留显示名称、说明和高级开关。模板名称、正文、调用方式和自定义覆盖路径保持不变。
