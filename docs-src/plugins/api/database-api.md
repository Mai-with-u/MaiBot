# 数据库API

数据库API模块提供通用的数据库操作功能，支持查询、创建、更新和删除记录，采用Peewee ORM模型。

## 导入方式

```python
from src.plugin_system.apis import database_api
# 或者
from src.plugin_system import database_api
```

## 主要功能

### 1. 通用数据库操作

```python
async def db_query(
    model_class: Type[Model],
    data: Optional[Dict[str, Any]] = None,
    query_type: Optional[str] = "get",
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by: Optional[List[str]] = None,
    single_result: Optional[bool] = False,
) -> Union[List[Dict[str, Any]], Dict[str, Any], None]:
```
执行数据库查询操作的通用接口。

**Args:**
- `model_class`: Peewee模型类。
    - Peewee模型类可以在`src.common.database.database_model`模块中找到，如`ActionRecords`、`Messages`等。
- `data`: 用于创建或更新的数据
- `query_type`: 查询类型
    - 可选值: `get`, `create`, `update`, `delete`, `count`。
- `filters`: 过滤条件字典，键为字段名，值为要匹配的值。
- `limit`: 限制结果数量。
- `order_by`: 排序字段列表，使用字段名，前缀'-'表示降序。
    - 排序字段，前缀`-`表示降序，例如`-time`表示按时间字段（即`time`字段）降序
- `single_result`: 是否只返回单个结果。

**Returns:**
- 根据查询类型返回不同的结果：
    - `get`: 返回查询结果列表或单个结果。（如果 `single_result=True`）
    - `create`: 返回创建的记录。
    - `update`: 返回受影响的行数。
    - `delete`: 返回受影响的行数。
    - `count`: 返回记录数量。

#### 示例

1. 查询最近10条消息
```python
messages = await database_api.db_query(
    Messages,
    query_type="get",
    filters={"chat_id": chat_stream.stream_id},
    limit=10,
    order_by=["-time"]
)
```
2. 创建一条记录
```python
new_record = await database_api.db_query(
    ActionRecords,
    data={"action_id": "123", "time": time.time(), "action_name": "TestAction"},
    query_type="create",
)
```
3. 更新记录
```python
updated_count = await database_api.db_query(
    ActionRecords,
    data={"action_done": True},
    query_type="update",
    filters={"action_id": "123"},
)
```
4. 删除记录
```python
deleted_count = await database_api.db_query(
    ActionRecords,
    query_type="delete",
    filters={"action_id": "123"}
)
```
5. 计数
```python
count = await database_api.db_query(
    Messages,
    query_type="count",
    filters={"chat_id": chat_stream.stream_id}
)
```

### 2. 数据库保存
```python
async def db_save(
    model_class: Type[Model], data: Dict[str, Any], key_field: Optional[str] = None, key_value: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
```
保存数据到数据库（创建或更新）

如果提供了key_field和key_value，会先尝试查找匹配的记录进行更新；

如果没有找到匹配记录，或未提供key_field和key_value，则创建新记录。

**Args:**
- `model_class`: Peewee模型类。
- `data`: 要保存的数据字典。
- `key_field`: 用于查找现有记录的字段名，例如"action_id"。
- `key_value`: 用于查找现有记录的字段值。

**Returns:**
- `Optional[Dict[str, Any]]`: 保存后的记录数据，失败时返回None。

#### 示例
创建或更新一条记录
```python
record = await database_api.db_save(
    ActionRecords,
    {
        "action_id": "123",
        "time": time.time(),
        "action_name": "TestAction",
        "action_done": True
    },
    key_field="action_id",
    key_value="123"
)
```

### 3. 数据库获取
```python
async def db_get(
    model_class: Type[Model],
    filters: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    order_by: Optional[str] = None,
    single_result: Optional[bool] = False,
) -> Union[List[Dict[str, Any]], Dict[str, Any], None]:
```

从数据库获取记录

这是db_query方法的简化版本，专注于数据检索操作。

**Args:**
- `model_class`: Peewee模型类。
- `filters`: 过滤条件字典，键为字段名，值为要匹配的值。
- `limit`: 限制结果数量。
- `order_by`: 排序字段，使用字段名，前缀'-'表示降序。
- `single_result`: 是否只返回单个结果，如果为True，则返回单个记录字典或None；否则返回记录字典列表或空列表

**Returns:**
- `Union[List[Dict], Dict, None]`: 查询结果列表或单个结果（如果`single_result=True`），失败时返回None。

#### 示例
1. 获取单个记录
```python
record = await database_api.db_get(
    ActionRecords,
    filters={"action_id": "123"},
    limit=1
)
```
2. 获取最近10条记录
```python
records = await database_api.db_get(
    Messages,
    filters={"chat_id": chat_stream.stream_id},
    limit=10,
    order_by="-time",
)
```

### 4. 动作信息存储
```python
async def store_action_info(
    chat_stream=None,
    action_build_into_prompt: bool = False,
    action_prompt_display: str = "",
    action_done: bool = True,
    thinking_id: str = "",
    action_data: Optional[dict] = None,
    action_name: str = "",
) -> Optional[Dict[str, Any]]:
```
存储动作信息到数据库，是一种针对 Action 的 `db_save()` 的封装函数。

将Action执行的相关信息保存到ActionRecords表中，用于后续的记忆和上下文构建。

**Args:**
- `chat_stream`: 聊天流对象，包含聊天ID等信息。
- `action_build_into_prompt`: 是否将动作信息构建到提示中。
- `action_prompt_display`: 动作提示的显示文本。
- `action_done`: 动作是否完成。
- `thinking_id`: 思考过程的ID。
- `action_data`: 动作的数据字典。
- `action_name`: 动作的名称。

**Returns:**
- `Optional[Dict[str, Any]]`: 存储后的记录数据，失败时返回None。

#### 示例
```python
record = await database_api.store_action_info(
    chat_stream=chat_stream,
    action_build_into_prompt=True,
    action_prompt_display="执行了回复动作",
    action_done=True,
    thinking_id="thinking_123",
    action_data={"content": "Hello"},
    action_name="reply_action"
)
```