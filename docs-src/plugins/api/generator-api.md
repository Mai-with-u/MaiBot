# 回复生成器API

回复生成器API模块提供智能回复生成功能，让插件能够使用系统的回复生成器来产生自然的聊天回复。

## 导入方式

```python
from src.plugin_system.apis import generator_api
# 或者
from src.plugin_system import generator_api
```

## 主要功能

### 1. 回复器获取
```python
def get_replyer(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    model_set_with_weight: Optional[List[Tuple[TaskConfig, float]]] = None,
    request_type: str = "replyer",
) -> Optional[DefaultReplyer]:
```
获取回复器对象

优先使用chat_stream，如果没有则使用chat_id直接查找。

使用 ReplyerManager 来管理实例，避免重复创建。

**Args:**
- `chat_stream`: 聊天流对象
- `chat_id`: 聊天ID（实际上就是`stream_id`）
- `model_set_with_weight`: 模型配置列表，每个元素为 `(TaskConfig, weight)` 元组
- `request_type`: 请求类型，用于记录LLM使用情况，可以不写

**Returns:**
- `DefaultReplyer`: 回复器对象，如果获取失败则返回None

#### 示例
```python
# 使用聊天流获取回复器
replyer = generator_api.get_replyer(chat_stream=chat_stream)

# 使用平台和ID获取回复器
replyer = generator_api.get_replyer(chat_id="123456789")
```

### 2. 回复生成
```python
async def generate_reply(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    action_data: Optional[Dict[str, Any]] = None,
    reply_to: str = "",
    extra_info: str = "",
    available_actions: Optional[Dict[str, ActionInfo]] = None,
    enable_tool: bool = False,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    return_prompt: bool = False,
    model_set_with_weight: Optional[List[Tuple[TaskConfig, float]]] = None,
    request_type: str = "generator_api",
) -> Tuple[bool, List[Tuple[str, Any]], Optional[str]]:
```
生成回复

优先使用chat_stream，如果没有则使用chat_id直接查找。

**Args:**
- `chat_stream`: 聊天流对象
- `chat_id`: 聊天ID（实际上就是`stream_id`）
- `action_data`: 动作数据（向下兼容，包含`reply_to`和`extra_info`）
- `reply_to`: 回复目标，格式为 `{发送者的person_name:消息内容}`
- `extra_info`: 附加信息
- `available_actions`: 可用动作字典，格式为 `{"action_name": ActionInfo}`
- `enable_tool`: 是否启用工具
- `enable_splitter`: 是否启用分割器
- `enable_chinese_typo`: 是否启用中文错别字
- `return_prompt`: 是否返回提示词
- `model_set_with_weight`: 模型配置列表，每个元素为 `(TaskConfig, weight)` 元组
- `request_type`: 请求类型（可选，记录LLM使用）
- `request_type`: 请求类型，用于记录LLM使用情况

**Returns:**
- `Tuple[bool, List[Tuple[str, Any]], Optional[str]]`: (是否成功, 回复集合, 提示词)

#### 示例
```python
success, reply_set, prompt = await generator_api.generate_reply(
    chat_stream=chat_stream,
    action_data=action_data,
    reply_to="麦麦:你好",
    available_actions=action_info,
    enable_tool=True,
    return_prompt=True
)
if success:
    for reply_type, reply_content in reply_set:
        print(f"回复类型: {reply_type}, 内容: {reply_content}")
    if prompt:
        print(f"使用的提示词: {prompt}")
```

### 3. 回复重写
```python
async def rewrite_reply(
    chat_stream: Optional[ChatStream] = None,
    reply_data: Optional[Dict[str, Any]] = None,
    chat_id: Optional[str] = None,
    enable_splitter: bool = True,
    enable_chinese_typo: bool = True,
    model_set_with_weight: Optional[List[Tuple[TaskConfig, float]]] = None,
    raw_reply: str = "",
    reason: str = "",
    reply_to: str = "",
    return_prompt: bool = False,
) -> Tuple[bool, List[Tuple[str, Any]], Optional[str]]:
```
重写回复，使用新的内容替换旧的回复内容。

优先使用chat_stream，如果没有则使用chat_id直接查找。

**Args:**
- `chat_stream`: 聊天流对象
- `reply_data`: 回复数据，包含`raw_reply`, `reason`和`reply_to`，**（向下兼容备用，当其他参数缺失时从此获取）**
- `chat_id`: 聊天ID（实际上就是`stream_id`）
- `enable_splitter`: 是否启用分割器
- `enable_chinese_typo`: 是否启用中文错别字
- `model_set_with_weight`: 模型配置列表，每个元素为 (TaskConfig, weight) 元组
- `raw_reply`: 原始回复内容
- `reason`: 重写原因
- `reply_to`: 回复目标，格式为 `{发送者的person_name:消息内容}`

**Returns:**
- `Tuple[bool, List[Tuple[str, Any]], Optional[str]]`: (是否成功, 回复集合, 提示词)

#### 示例
```python
success, reply_set, prompt = await generator_api.rewrite_reply(
    chat_stream=chat_stream,
    raw_reply="原始回复内容",
    reason="重写原因",
    reply_to="麦麦:你好",
    return_prompt=True
)
if success:
    for reply_type, reply_content in reply_set:
        print(f"回复类型: {reply_type}, 内容: {reply_content}")
    if prompt:
        print(f"使用的提示词: {prompt}")
```

## 回复集合`reply_set`格式

### 回复类型
生成的回复集合包含多种类型的回复：

- `"text"`：纯文本回复
- `"emoji"`：表情包回复
- `"image"`：图片回复
- `"mixed"`：混合类型回复

### 回复集合结构
```python
# 示例回复集合
reply_set = [
    ("text", "很高兴见到你！"),
    ("emoji", "emoji_base64_data"),
    ("text", "有什么可以帮助你的吗？")
]
```

### 4. 自定义提示词回复
```python
async def generate_response_custom(
    chat_stream: Optional[ChatStream] = None,
    chat_id: Optional[str] = None,
    model_set_with_weight: Optional[List[Tuple[TaskConfig, float]]] = None,
    prompt: str = "",
) -> Optional[str]:
```
生成自定义提示词回复

优先使用chat_stream，如果没有则使用chat_id直接查找。

**Args:**
- `chat_stream`: 聊天流对象
- `chat_id`: 聊天ID（备用）
- `model_set_with_weight`: 模型集合配置列表
- `prompt`: 自定义提示词

**Returns:**
- `Optional[str]`: 生成的自定义回复内容，如果生成失败则返回None

## 注意事项

1. **异步操作**：部分函数是异步的，须使用`await`
2. **聊天流依赖**：需要有效的聊天流对象才能正常工作
3. **性能考虑**：回复生成可能需要一些时间，特别是使用LLM时
4. **回复格式**：返回的回复集合是元组列表，包含类型和内容
5. **上下文感知**：生成器会考虑聊天上下文和历史消息，除非你用的是自定义提示词。