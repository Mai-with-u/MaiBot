# 消息API

消息API提供了强大的消息查询、计数和格式化功能，让你轻松处理聊天消息数据。

## 导入方式

```python
from src.plugin_system.apis import message_api
# 或者
from src.plugin_system import message_api
```

## 功能概述

消息API主要提供三大类功能：
- **消息查询** - 按时间、聊天、用户等条件查询消息
- **消息计数** - 统计新消息数量
- **消息格式化** - 将消息转换为可读格式

## 主要功能

### 1. 按照事件查询消息
```python
def get_messages_by_time(
    start_time: float, end_time: float, limit: int = 0, limit_mode: str = "latest", filter_mai: bool = False
) -> List[Dict[str, Any]]:
```
获取指定时间范围内的消息。

**Args:**
- `start_time` (float): 开始时间戳
- `end_time` (float): 结束时间戳
- `limit` (int): 限制返回消息数量，0为不限制
- `limit_mode` (str): 限制模式，`"earliest"`获取最早记录，`"latest"`获取最新记录
- `filter_mai` (bool): 是否过滤掉机器人的消息，默认False

**Returns:**
- `List[Dict[str, Any]]` - 消息列表

消息列表中包含的键与`Messages`类的属性一致。（位于`src.common.database.database_model`）

### 2. 获取指定聊天中指定时间范围内的信息
```python
def get_messages_by_time_in_chat(
    chat_id: str,
    start_time: float,
    end_time: float,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_mai: bool = False,
) -> List[Dict[str, Any]]:
```
获取指定聊天中指定时间范围内的消息。

**Args:**
- `chat_id` (str): 聊天ID
- `start_time` (float): 开始时间戳
- `end_time` (float): 结束时间戳
- `limit` (int): 限制返回消息数量，0为不限制
- `limit_mode` (str): 限制模式，`"earliest"`获取最早记录，`"latest"`获取最新记录
- `filter_mai` (bool): 是否过滤掉机器人的消息，默认False

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 3. 获取指定聊天中指定时间范围内的信息（包含边界）
```python
def get_messages_by_time_in_chat_inclusive(
    chat_id: str,
    start_time: float,
    end_time: float,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_mai: bool = False,
    filter_command: bool = False,
) -> List[Dict[str, Any]]:
```
获取指定聊天中指定时间范围内的消息（包含边界）。

**Args:**
- `chat_id` (str): 聊天ID
- `start_time` (float): 开始时间戳（包含）
- `end_time` (float): 结束时间戳（包含）
- `limit` (int): 限制返回消息数量，0为不限制
- `limit_mode` (str): 限制模式，`"earliest"`获取最早记录，`"latest"`获取最新记录
- `filter_mai` (bool): 是否过滤掉机器人的消息，默认False
- `filter_command` (bool): 是否过滤命令消息，默认False

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 4. 获取指定聊天中指定用户在指定时间范围内的消息
```python
def get_messages_by_time_in_chat_for_users(
    chat_id: str,
    start_time: float,
    end_time: float,
    person_ids: List[str],
    limit: int = 0,
    limit_mode: str = "latest",
) -> List[Dict[str, Any]]:
```
获取指定聊天中指定用户在指定时间范围内的消息。

**Args:**
- `chat_id` (str): 聊天ID
- `start_time` (float): 开始时间戳
- `end_time` (float): 结束时间戳
- `person_ids` (List[str]): 用户ID列表
- `limit` (int): 限制返回消息数量，0为不限制
- `limit_mode` (str): 限制模式，`"earliest"`获取最早记录，`"latest"`获取最新记录

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 5. 随机选择一个聊天，返回该聊天在指定时间范围内的消息
```python
def get_random_chat_messages(
    start_time: float,
    end_time: float,
    limit: int = 0,
    limit_mode: str = "latest",
    filter_mai: bool = False,
) -> List[Dict[str, Any]]:
```
随机选择一个聊天，返回该聊天在指定时间范围内的消息。

**Args:**
- `start_time` (float): 开始时间戳
- `end_time` (float): 结束时间戳
- `limit` (int): 限制返回消息数量，0为不限制
- `limit_mode` (str): 限制模式，`"earliest"`获取最早记录，`"latest"`获取最新记录
- `filter_mai` (bool): 是否过滤掉机器人的消息，默认False

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 6. 获取指定用户在所有聊天中指定时间范围内的消息
```python
def get_messages_by_time_for_users(
    start_time: float,
    end_time: float,
    person_ids: List[str],
    limit: int = 0,
    limit_mode: str = "latest",
) -> List[Dict[str, Any]]:
```
获取指定用户在所有聊天中指定时间范围内的消息。

**Args:**
- `start_time` (float): 开始时间戳
- `end_time` (float): 结束时间戳
- `person_ids` (List[str]): 用户ID列表
- `limit` (int): 限制返回消息数量，0为不限制
- `limit_mode` (str): 限制模式，`"earliest"`获取最早记录，`"latest"`获取最新记录

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 7. 获取指定时间戳之前的消息
```python
def get_messages_before_time(
    timestamp: float,
    limit: int = 0,
    filter_mai: bool = False,
) -> List[Dict[str, Any]]:
```
获取指定时间戳之前的消息。

**Args:**
- `timestamp` (float): 时间戳
- `limit` (int): 限制返回消息数量，0为不限制
- `filter_mai` (bool): 是否过滤掉机器人的消息，默认False

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 8. 获取指定聊天中指定时间戳之前的消息
```python
def get_messages_before_time_in_chat(
    chat_id: str,
    timestamp: float,
    limit: int = 0,
    filter_mai: bool = False,
) -> List[Dict[str, Any]]:
```
获取指定聊天中指定时间戳之前的消息。

**Args:**
- `chat_id` (str): 聊天ID
- `timestamp` (float): 时间戳
- `limit` (int): 限制返回消息数量，0为不限制
- `filter_mai` (bool): 是否过滤掉机器人的消息，默认False

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 9. 获取指定用户在指定时间戳之前的消息
```python
def get_messages_before_time_for_users(
    timestamp: float,
    person_ids: List[str],
    limit: int = 0,
) -> List[Dict[str, Any]]:
```
获取指定用户在指定时间戳之前的消息。

**Args:**
- `timestamp` (float): 时间戳
- `person_ids` (List[str]): 用户ID列表
- `limit` (int): 限制返回消息数量，0为不限制

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 10. 获取指定聊天中最近一段时间的消息
```python
def get_recent_messages(
    chat_id: str,
    hours: float = 24.0,
    limit: int = 100,
    limit_mode: str = "latest",
    filter_mai: bool = False,
) -> List[Dict[str, Any]]:
```
获取指定聊天中最近一段时间的消息。

**Args:**
- `chat_id` (str): 聊天ID
- `hours` (float): 最近多少小时，默认24小时
- `limit` (int): 限制返回消息数量，默认100条
- `limit_mode` (str): 限制模式，`"earliest"`获取最早记录，`"latest"`获取最新记录
- `filter_mai` (bool): 是否过滤掉机器人的消息，默认False

**Returns:**
- `List[Dict[str, Any]]` - 消息列表


### 11. 计算指定聊天中从开始时间到结束时间的新消息数量
```python
def count_new_messages(
    chat_id: str,
    start_time: float = 0.0,
    end_time: Optional[float] = None,
) -> int:
```
计算指定聊天中从开始时间到结束时间的新消息数量。

**Args:**
- `chat_id` (str): 聊天ID
- `start_time` (float): 开始时间戳
- `end_time` (Optional[float]): 结束时间戳，如果为None则使用当前时间

**Returns:**
- `int` - 新消息数量


### 12. 计算指定聊天中指定用户从开始时间到结束时间的新消息数量
```python
def count_new_messages_for_users(
    chat_id: str,
    start_time: float,
    end_time: float,
    person_ids: List[str],
) -> int:
```
计算指定聊天中指定用户从开始时间到结束时间的新消息数量。

**Args:**
- `chat_id` (str): 聊天ID
- `start_time` (float): 开始时间戳
- `end_time` (float): 结束时间戳
- `person_ids` (List[str]): 用户ID列表

**Returns:**
- `int` - 新消息数量


### 13. 将消息列表构建成可读的字符串
```python
def build_readable_messages_to_str(
    messages: List[Dict[str, Any]],
    replace_bot_name: bool = True,
    merge_messages: bool = False,
    timestamp_mode: str = "relative",
    read_mark: float = 0.0,
    truncate: bool = False,
    show_actions: bool = False,
) -> str:
```
将消息列表构建成可读的字符串。

**Args:**
- `messages` (List[Dict[str, Any]]): 消息列表
- `replace_bot_name` (bool): 是否将机器人的名称替换为"你"
- `merge_messages` (bool): 是否合并连续消息
- `timestamp_mode` (str): 时间戳显示模式，`"relative"`或`"absolute"`
- `read_mark` (float): 已读标记时间戳，用于分割已读和未读消息
- `truncate` (bool): 是否截断长消息
- `show_actions` (bool): 是否显示动作记录

**Returns:**
- `str` - 格式化后的可读字符串


### 14. 将消息列表构建成可读的字符串，并返回详细信息
```python
async def build_readable_messages_with_details(
    messages: List[Dict[str, Any]],
    replace_bot_name: bool = True,
    merge_messages: bool = False,
    timestamp_mode: str = "relative",
    truncate: bool = False,
) -> Tuple[str, List[Tuple[float, str, str]]]:
```
将消息列表构建成可读的字符串，并返回详细信息。

**Args:**
- `messages` (List[Dict[str, Any]]): 消息列表
- `replace_bot_name` (bool): 是否将机器人的名称替换为"你"
- `merge_messages` (bool): 是否合并连续消息
- `timestamp_mode` (str): 时间戳显示模式，`"relative"`或`"absolute"`
- `truncate` (bool): 是否截断长消息

**Returns:**
- `Tuple[str, List[Tuple[float, str, str]]]` - 格式化后的可读字符串和详细信息元组列表(时间戳, 昵称, 内容)


### 15. 从消息列表中提取不重复的用户ID列表
```python
async def get_person_ids_from_messages(
    messages: List[Dict[str, Any]],
) -> List[str]:
```
从消息列表中提取不重复的用户ID列表。

**Args:**
- `messages` (List[Dict[str, Any]]): 消息列表

**Returns:**
- `List[str]` - 用户ID列表


### 16. 从消息列表中移除机器人的消息
```python
def filter_mai_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
```
从消息列表中移除机器人的消息。

**Args:**
- `messages` (List[Dict[str, Any]]): 消息列表，每个元素是消息字典

**Returns:**
- `List[Dict[str, Any]]` - 过滤后的消息列表

## 注意事项

1. **时间戳格式**：所有时间参数都使用Unix时间戳（float类型）
2. **异步函数**：部分函数是异步函数，需要使用 `await`
3. **性能考虑**：查询大量消息时建议设置合理的 `limit` 参数
4. **消息格式**：返回的消息是字典格式，包含时间戳、发送者、内容等信息
5. **用户ID**：`person_ids` 参数接受字符串列表，用于筛选特定用户的消息 