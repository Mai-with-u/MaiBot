# ⚡ Action组件详解

## 📖 什么是Action

Action是给麦麦在回复之外提供额外功能的智能组件，**由麦麦的决策系统自主选择是否使用**，具有随机性和拟人化的调用特点。Action不是直接响应用户命令，而是让麦麦根据聊天情境智能地选择合适的动作，使其行为更加自然和真实。

### Action的特点

- 🧠 **智能激活**：麦麦根据多种条件智能判断是否使用
- 🎲 **可随机性**：可以使用随机数激活，增加行为的不可预测性，更接近真人交流
- 🤖 **拟人化**：让麦麦的回应更自然、更有个性
- 🔄 **情境感知**：基于聊天上下文做出合适的反应

---

## 🎯 Action组件的基本结构
首先，所有的Action都应该继承`BaseAction`类。

其次，每个Action组件都应该实现以下基本信息：
```python
class ExampleAction(BaseAction):
    action_name = "example_action" # 动作的唯一标识符
    action_description = "这是一个示例动作" # 动作描述
    activation_type = ActionActivationType.ALWAYS # 这里以 ALWAYS 为例
    associated_types = ["text", "emoji", ...] # 关联类型
    parallel_action = False # 是否允许与其他Action并行执行
    action_parameters = {"param1": "参数1的说明", "param2": "参数2的说明", ...}
    # Action使用场景描述 - 帮助LLM判断何时"选择"使用
    action_require = ["使用场景描述1", "使用场景描述2", ...]

    async def execute(self) -> Tuple[bool, str]:
        """
        执行Action的主要逻辑
        
        Returns:
            Tuple[bool, str]: (是否成功, 执行结果描述)
        """
        # ---- 执行动作的逻辑 ----
        return True, "执行成功"
```
#### associated_types: 该Action会发送的消息类型，例如文本、表情等。

这部分由Adapter传递给处理器。

以 MaiBot-Napcat-Adapter 为例，可选项目如下：
| 类型 | 说明 | 格式 |
| --- | --- | --- |
| text | 文本消息 | str |
| emoji | 表情消息 | str: 表情包的无头base64|
| image | 图片消息 | str: 图片的无头base64 |
| reply | 回复消息 | str: 回复的消息ID |
| voice | 语音消息 | str: wav格式语音的无头base64 |
| command | 命令消息 | 参见Adapter文档 |
| voiceurl | 语音URL消息 | str: wav格式语音的URL |
| music | 音乐消息 | str: 这首歌在网易云音乐的音乐id |
| videourl | 视频URL消息 | str: 视频的URL |
| file | 文件消息 | str: 文件的路径 |

**请知悉，对于不同的处理器，其支持的消息类型可能会有所不同。在开发时请注意。**

#### action_parameters: 该Action的参数说明。
这是一个字典，键为参数名，值为参数说明。这个字段可以帮助LLM理解如何使用这个Action，并由LLM返回对应的参数，最后传递到 Action 的 **`action_data`** 属性中。其格式与你定义的格式完全相同 **（除非LLM哈气了，返回了错误的内容）**。

---

## 🎯 Action 调用的决策机制

Action采用**两层决策机制**来优化性能和决策质量：

> 设计目的：在加载许多插件的时候降低LLM决策压力，避免让麦麦在过多的选项中纠结。

**第一层：激活控制（Activation Control）**

激活决定麦麦是否 **“知道”** 这个Action的存在，即这个Action是否进入决策候选池。不被激活的Action麦麦永远不会选择。

**第二层：使用决策（Usage Decision）**

在Action被激活后，使用条件决定麦麦什么时候会 **“选择”** 使用这个Action。

### 决策参数详解 🔧

#### 第一层：ActivationType 激活类型说明

| 激活类型 | 说明 | 使用场景 |
| ----------- | ---------------------------------------- | ---------------------- |
| [`NEVER`](#never-激活)     | 从不激活，Action对麦麦不可见               | 临时禁用某个Action      |
| [`ALWAYS`](#always-激活)    | 永远激活，Action总是在麦麦的候选池中        | 核心功能，如回复、不回复 |
| `RANDOM`    | 基于随机概率决定是否激活                   | 增加行为随机性的功能     |
| `KEYWORD`   | 当检测到特定关键词时激活                   | 明确触发条件的功能       |

#### `NEVER` 激活

`ActionActivationType.NEVER` 会使得 Action 永远不会被激活

```python
class DisabledAction(BaseAction):
    activation_type = ActionActivationType.NEVER  # 永远不激活
    
    async def execute(self) -> Tuple[bool, str]:
        # 这个Action永远不会被执行
        return False, "这个Action被禁用"
```

#### `ALWAYS` 激活

`ActionActivationType.ALWAYS` 会使得 Action 永远会被激活，即一直在 Action 候选池中

这种激活方式常用于核心功能，如回复或不回复。

```python
class AlwaysActivatedAction(BaseAction):
    activation_type = ActionActivationType.ALWAYS  # 永远激活
    
    async def execute(self) -> Tuple[bool, str]:
        # 执行核心功能
        return True, "执行了核心功能"
```

#### `RANDOM` 激活

`ActionActivationType.RANDOM`会使得这个 Action 根据随机概率决定是否加入候选池。

概率则由代码中的`random_activation_probability`控制。在内部实现中我们使用了`random.random()`来生成一个0到1之间的随机数，并与这个概率进行比较。

因此使用这个方法需要实现`random_activation_probability`属性。

```python
class SurpriseAction(BaseAction):
    activation_type = ActionActivationType.RANDOM  # 基于随机概率激活
    # 随机激活概率
    random_activation_probability = 0.1  # 10%概率激活
  
    async def execute(self) -> Tuple[bool, str]:
        # 执行惊喜动作
        return True, "发送了惊喜内容"
```

#### `KEYWORD` 激活

`ActionActivationType.KEYWORD`会使得这个 Action 在检测到特定关键词时激活。

关键词由代码中的`activation_keywords`定义，而`keyword_case_sensitive`则控制关键词匹配时是否区分大小写。在内部实现中，我们使用了`in`操作符来检查消息内容是否包含这些关键词。

因此，使用此种方法需要实现`activation_keywords`和`keyword_case_sensitive`属性。

```python
class GreetingAction(BaseAction):
    activation_type = ActionActivationType.KEYWORD  # 关键词激活
    activation_keywords = ["你好", "hello", "hi", "嗨"] # 关键词配置
    keyword_case_sensitive = False  # 不区分大小写
  
    async def execute(self) -> Tuple[bool, str]:
        # 执行问候逻辑
        return True, "发送了问候"
```

一个完整的使用`ActionActivationType.KEYWORD`的例子请参考`plugins/hello_world_plugin`中的`ByeAction`。

#### 第二层：使用决策

**在Action被激活后，使用条件决定麦麦什么时候会"选择"使用这个Action**。

这一层由以下因素综合决定：

- `action_require`：使用场景描述，帮助LLM判断何时选择
- `action_parameters`：所需参数，影响Action的可执行性
- 当前聊天上下文和麦麦的决策逻辑

---

### 决策流程示例

```python
class EmojiAction(BaseAction):
    # 第一层：激活控制
    activation_type = ActionActivationType.RANDOM  # 随机激活
    random_activation_probability = 0.1  # 10%概率激活

    # 第二层：使用决策
    action_require = [
        "表达情绪时可以选择使用",
        "增加聊天趣味性",
        "不要连续发送多个表情"
    ]
```

**决策流程**：

1. **第一层激活判断**：

    - 使用随机数进行决策，当`random.random() < self.random_activation_probability`时，麦麦才"知道"可以使用这个Action
2. **第二层使用决策**：

   - 即使Action被激活，麦麦还会根据 `action_require` 中的条件判断是否真正选择使用
   - 例如：如果刚刚已经发过表情，根据"不要连续发送多个表情"的要求，麦麦可能不会选择这个Action

---

## Action 内置属性说明
```python
class BaseAction:
    def __init__(self):
        # 消息相关属性
        self.log_prefix: str          # 日志前缀
        self.group_id: str            # 群组ID
        self.group_name: str          # 群组名称
        self.user_id: str             # 用户ID
        self.user_nickname: str       # 用户昵称
        self.platform: str            # 平台类型 (qq, telegram等)
        self.chat_id: str             # 聊天ID
        self.chat_stream: ChatStream  # 聊天流对象
        self.is_group: bool           # 是否群聊

        # 消息体
        self.action_message: dict     # 消息数据

        # Action相关属性
        self.action_data: dict        # Action执行时的数据
        self.thinking_id: str         # 思考ID
```
action_message为一个字典，包含的键值对如下（省略了不必要的键值对）

```python
{
    "message_id": "1234567890",  # 消息id，str
    "time": 1627545600.0,  # 时间戳，float
    "chat_id": "abcdef123456",  # 聊天ID，str
    "reply_to": None,  # 回复消息id，str或None
    "interest_value": 0.85,  # 兴趣值，float
    "is_mentioned": True,  # 是否被提及，bool
    "chat_info_last_active_time": 1627548600.0,  # 最后活跃时间，float
    "processed_plain_text": None,  # 处理后的文本，str或None
    "additional_config": None,  # Adapter传来的additional_config，dict或None
    "is_emoji": False,  # 是否为表情，bool
    "is_picid": False,  # 是否为图片ID，bool
    "is_command": False  # 是否为命令，bool
}
```

部分值的格式请自行查询数据库。

---

## Action 内置方法说明
```python
class BaseAction:
    def get_config(self, key: str, default=None):
        """获取插件配置值，使用嵌套键访问"""
    
    async def wait_for_new_message(self, timeout: int = 1200) -> Tuple[bool, str]:
        """等待新消息或超时"""

    async def send_text(self, content: str, reply_to: str = "", reply_to_platform_id: str = "", typing: bool = False) -> bool:
        """发送文本消息"""

    async def send_emoji(self, emoji_base64: str) -> bool:
        """发送表情包"""

    async def send_image(self, image_base64: str) -> bool:
        """发送图片"""

    async def send_custom(self, message_type: str, content: str, typing: bool = False, reply_to: str = "") -> bool:
        """发送自定义类型消息"""

    async def store_action_info(self, action_build_into_prompt: bool = False, action_prompt_display: str = "", action_done: bool = True) -> None:
        """存储动作信息到数据库"""

    async def send_command(self, command_name: str, args: Optional[dict] = None, display_message: str = "", storage_message: bool = True) -> bool:
        """发送命令消息"""
```
具体参数与用法参见`BaseAction`基类的定义。