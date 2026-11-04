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

## 后续分叉

修改私聊唤醒、上下文容量、回复风格或记忆偏好时，修改 `private/` 对应实现；群聊同理。涉及双方共同的防抖算法、存储或模型执行机制时，修改共享层。只有确实出现不同的推理执行协议时，才增加新的执行器接口，避免复制整个推理引擎。

## 验证

使用项目解释器执行测试，避免复制的虚拟环境启动器指向其他 checkout：

```powershell
uv run --no-sync python -m pytest pytests/maisaka pytests/plugin_runtime/test_reply_generation.py pytests/plugin_runtime/test_hook_reply_abort.py pytests/plugin_runtime/test_reply_extension_platform_scope.py tests/test_reply_supplement.py -q
```

`test_chat_scenes.py` 覆盖配置独立性、热重载、三种语言提示词、模板选择、未知会话、引用公式、方向性记忆权限、画像候选以及群聊/私聊提及差异。
