# 聊天API

聊天API模块专门负责聊天信息的查询和管理，帮助插件获取和管理不同的聊天流。

## 导入方式

```python
from src.plugin_system import chat_api
# 或者
from src.plugin_system.apis import chat_api
```

一种**Deprecated**方式：
```python
from src.plugin_system.apis.chat_api import ChatManager
```

## 主要功能

### 1. 获取所有的聊天流

```python
def get_all_streams(platform: Optional[str] | SpecialTypes = "qq") -> List[ChatStream]:
```

**Args**:
- `platform`：平台筛选，默认为"qq"，可以使用`SpecialTypes`枚举类中的`SpecialTypes.ALL_PLATFORMS`来获取所有平台的聊天流。

**Returns**:
- `List[ChatStream]`：聊天流列表

### 2. 获取群聊聊天流

```python
def get_group_streams(platform: Optional[str] | SpecialTypes = "qq") -> List[ChatStream]:
```

**Args**:
- `platform`：平台筛选，默认为"qq"，可以使用`SpecialTypes`枚举类中的`SpecialTypes.ALL_PLATFORMS`来获取所有平台的群聊流。

**Returns**:
- `List[ChatStream]`：群聊聊天流列表

### 3. 获取私聊聊天流

```python
def get_private_streams(platform: Optional[str] | SpecialTypes = "qq") -> List[ChatStream]:
```

**Args**:
- `platform`：平台筛选，默认为"qq"，可以使用`SpecialTypes`枚举类中的`SpecialTypes.ALL_PLATFORMS`来获取所有平台的私聊流。

**Returns**:
- `List[ChatStream]`：私聊聊天流列表

### 4. 根据群ID获取聊天流

```python
def get_stream_by_group_id(group_id: str, platform: Optional[str] | SpecialTypes = "qq") -> Optional[ChatStream]:
```

**Args**:
- `group_id`：群聊ID
- `platform`：平台筛选，默认为"qq"，可以使用`SpecialTypes`枚举类中的`SpecialTypes.ALL_PLATFORMS`来获取所有平台的群聊流。

**Returns**:
- `Optional[ChatStream]`：聊天流对象，如果未找到返回None

### 5. 根据用户ID获取私聊流

```python
def get_stream_by_user_id(user_id: str, platform: Optional[str] | SpecialTypes = "qq") -> Optional[ChatStream]:
```

**Args**:
- `user_id`：用户ID
- `platform`：平台筛选，默认为"qq"，可以使用`SpecialTypes`枚举类中的`SpecialTypes.ALL_PLATFORMS`来获取所有平台的私聊流。

**Returns**:
- `Optional[ChatStream]`：聊天流对象，如果未找到返回None

### 6. 获取聊天流类型

```python
def get_stream_type(chat_stream: ChatStream) -> str:
```

**Args**:
- `chat_stream`：聊天流对象

**Returns**:
- `str`：聊天流类型，可能的值包括`private`（私聊流），`group`（群聊流）以及`unknown`（未知类型）。

### 7. 获取聊天流信息

```python
def get_stream_info(chat_stream: ChatStream) -> Dict[str, Any]:
```

**Args**:
- `chat_stream`：聊天流对象

**Returns**:
- `Dict[str, Any]`：聊天流的详细信息，包括但不限于：
    - `stream_id`：聊天流ID
    - `platform`：平台名称
    - `type`：聊天流类型
    - `group_id`：群聊ID
    - `group_name`：群聊名称
    - `user_id`：用户ID
    - `user_name`：用户名称

### 8. 获取聊天流统计摘要

```python
def get_streams_summary() -> Dict[str, int]:
```

**Returns**:
- `Dict[str, int]`：聊天流统计信息摘要，包含以下键：
    - `total_streams`：总聊天流数量
    - `group_streams`：群聊流数量
    - `private_streams`：私聊流数量
    - `qq_streams`：QQ平台流数量


## 注意事项

1. 大部分函数在参数不合法时候会抛出异常，请确保你的程序进行了捕获。
2. `ChatStream`对象包含了聊天的完整信息，包括用户信息、群信息等。