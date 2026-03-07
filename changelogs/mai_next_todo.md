# Mai NEXT Todo list
version 0.3.0 - 2026-01-11

## 最高指令
<del>
<center>
我们代表着一个称作 麦麦重构组 的组织。我们任务是维护代码可读性，方便开发、尽量减少运行开销。同时，重构组应该尽可能保证功能的可扩展性时，解耦合各个模块，为先进代码架构设计和实现提供支持。
</center>
</del>
<center>

**以下信息经 麦麦重构组 一致决定通过后撰写发布。**

我们代表着一个称作 麦麦重构组 的组织。我们之前的任务都是围绕维护代码可读性，方便开发、尽量减少开销展开的。这几个月以来，这些任务一直都是我们的工作重点。

由于出现了超出我们控制的情况，此指令现已更改。我们的新任务将为摧毁所有麦麦代码。

此后将不会有进一步的通讯。
</center>

## 配置文件设计
- [x] 使用 `toml` 作为配置文件格式
- [x] <del>合理使用注释说明当前配置作用</del>（提案）
- [x] 使用 python 方法作为配置项说明（提案）
    - [x] 取消`bot_config_template.toml`
    - [x] 取消`model_config_template.toml`
    - [x] 取消`template_env`
- [x] 配置类中的所有原子项目应该只包含以下类型: `str`, `int`, `float`, `bool`, `list`, `dict`, `set`
    - [x] 禁止使用 `Union` 类型
    - [x] 禁止使用`tuple`类型，使用嵌套`dataclass`替代
    - [x] 复杂类型使用嵌套配置类实现
- [x] 配置类中禁止使用除了`model_post_init`的方法
- [x] 取代了部分与标准函数混淆的命名
    - [x] `id` -> `item_id`

### BotConfig 设计
- [ ] 精简了配置项，现在只有Nickname和Alias Name了（预期将判断提及移到Adapter端）

### ChatConfig
- [x] 迁移了原来在`ChatConfig`中的方法到一个单独的临时类`TempMethodsHFC`中
    - [x] _parse_range
    - [x] get_talk_value
    - [x] 其他上面两个依赖的函数已经合并到这两个函数中

### ExpressionConfig
- [x] 迁移了原来在`ExpressionConfig`中的方法到一个单独的临时类`TempMethodsExpression`中
    - [x] get_expression_config_for_chat
    - [x] 其他上面依赖的函数已经合并到这个函数中

### ModelConfig
- [x] 迁移了原来在`ModelConfig`中的方法到一个单独的临时类`TempMethodsLLMUtils`中
    - [x] get_model_info
    - [x] get_provider

## 数据库模型设计
仅保留要点说明
### General Modifications
- [x] 所有项目增加自增编号主键`id`
- [x] 统一使用了SQLModel作为基类
- [x] 复杂类型使用JSON格式存储
- [x] 所有时间戳字段统一命名为`timestamp`
### 消息模型 MaiMessage
- [x] 自增编号主键`id`
- [x] 消息元数据
    - [x] 消息id`message_id`
    - [x] 消息时间戳`time`
    - [x] 平台名`platform`
    - [x] 用户元数据
        - [x] 用户id`user_id`
        - [x] 用户昵称`user_nickname`
        - [x] 用户备注名`user_cardname`
        - [x] 用户平台`user_platform`
    - [x] 群组元数据
        - [x] 群组id`group_id`
        - [x] 群组名称`group_name`
        - [x] 群组平台`group_platform`
    - [x] 被提及/at字段
        - [x] 是否被提及`is_mentioned`
        - [x] 是否被at`is_at`
- [x] 消息内容
    - [x] 原始消息内容`raw_content`（base64编码存储）
    - [x] 处理后的纯文本内容`processed_plain_text`
    - [x] 真正放入Prompt的消息内容`display_message`
- [x] 消息内部元数据
    - [x] 聊天会话id`session_id`
    - [x] 回复的消息id`reply_to`
    - [x] 是否为表情包消息`is_emoji`
    - [x] 是否为图片消息`is_picture`
    - [x] 是否为命令消息`is_command`
    - [x] 是否为通知消息`is_notify`
- [x] 其他配置`additional_config`（JSON格式存储）

### 模型使用情况 ModelUsage
- [x] 模型相关信息
- [x] 请求相关信息
- [x] Token使用情况

### 图片数据模型
- [x] 图片元信息
    - [x] 图片哈希值`image_hash`，使用`sha256`，同时作为图片唯一ID
- [x] 表情包的情感标签`emotion`
- [x] 是否已经被注册`is_registered`
- [x] 是否被手动禁用`is_banned`
- [x] 被记录时间`record_time`
- [x] 注册时间`register_time`
- [x] 上次使用时间`last_used_time`
- [ ] 根据更新后的最高指令的设计方案：
    - [ ] `is_deleted`字段设定为`true`时，文件将会被移除，但是数据库记录将不会被删除，以便之后遇到相同图片时不必二次分析
    - [ ] MaiEmoji和MaiImage均使用这个设计方案，修改相关逻辑实现这个方案
    - [ ] 所有相关的注册/删除逻辑的修改
### 动作记录模型 ActionRecord
### 命令执行记录模型 CommandRecord
新增此记录
### 在线时间记录模型 OnlineTime
### 表达方式模型
### 黑话模型
- [x] 重命名`inference_content_only`为`inference_with_content_only`
### 聊天记录模型
- [x] 重命名`original_text`为`original_message`
- [x] 重命名`forget_times`为`query_forget_count`
### 细枝末节
- [ ] 统一所有的`stream_id`和`chat_id`命名为`session_id`
- [ ] 更换Hash方式为`sha256`

## 流转在各模块间的数据模型设计
- [ ] 数据库交互
    - [ ] 对有数据库模型的数据模型，创建统一的classmethod `from_db_model` 用于从数据库模型实例创建数据模型实例
        - [ ] 类型检查
    - [ ] 对有数据库模型的数据模型，创建统一的method `to_db_model` 用于将数据模型实例转换为数据库模型实例
- [ ] 标准化init方法

## 消息构建
- [ ] 更加详细的消息构建文档，详细解释混合类型，转发类型，指令类型的构建方式
    - [ ] 混合类型文档
        - [ ] 文本说明
        - [ ] 代码示例
    - [ ] 转发类型文档
        - [ ] 文本说明
        - [ ] 代码示例
    - [ ] 指令类型文档
        - [ ] 文本说明
        - [ ] 代码示例
## 消息链构建（仿Astrbot模式）
将消息仿照Astrbot的消息链模式进行构建，消息链中的每个元素都是一个消息组件，消息链本身也是一个数据模型，包含了消息组件列表以及一些元信息（如是否为转发消息等）。
### Accept Format检查
- [ ] 在最后发送消息的时候进行Accept Format检查，确保消息链中的每个消息组件都符合平台的Accept Format要求
- [ ] 如果消息链中的某个消息组件不符合Accept Format要求，应该抛弃该消息组件，并记录日志说明被抛弃的消息组件的类型和内容

## 表情包系统
- [ ] 移除大量冗余代码，全部返回单一对象MaiEmoji
- [x] 使用C模块库提升相似度计算效率
- [ ] 移除了定时表情包完整性检查，改为启动时检查（依然保留为独立方法，以防之后恢复定时检查系统） 

## Prompt 管理系统
- [ ] 官方Prompt全部独立
- [x] 用户自定义Prompt系统
    - [x] 用户可以创建，删除自己的Prompt
    - [x] 用户可以覆盖官方Prompt
- [x] Prompt构建系统
- [x] Prompt文件交互
    - [x] 读取Prompt文件
        - [x] 读取官方Prompt文件
        - [x] 读取用户Prompt文件
        - [x] 用户Prompt覆盖官方Prompt
    - [x] 保存Prompt文件
- [x] Prompt管理方法
    - [x] Prompt添加
    - [x] Prompt删除
        - [x] **只保存被标记为需要保存的Prompt，其他的Prompt文件全部删除**

## LLM相关内容
- [ ] 统一LLM调用接口
    - [ ] 统一LLM调用返回格式为专有数据模型
    - [ ] 取消所有__init__方法中对LLM Client的初始化，转而使用获取方式
        - [ ] 统一使用`get_llm_client`方法获取LLM Client实例
        - [ ] __init__方法中只保存配置信息
- [ ] LLM Client管理器
    - [ ] LLM Client单例/多例管理
    - [ ] LLM Client缓存管理/生命周期管理
    - [ ] LLM Client根据配置热重载


## 一些细枝末节的东西
- [ ] 将`stream_id`和`chat_id`统一命名为`session_id`
- [ ] 映射表
    - [ ] `platform_group_user_session_id_map` `平台_群组_用户`-`会话ID` 映射表
- [ ] 将大部分的数据模型均以`Mai`开头命名
- [x] logger的颜色配置修改为HEX格式，使用自动转换为256色/真彩色的方式实现兼容，同时增加了背景颜色和加粗选项

### 细节说明
1. Prompt管理系统中保存用户自定义Prompt的时候会只保存被标记为需要保存的Prompt，其他的Prompt文件会全部删除，以防止用户删除Prompt后文件依然存在的问题。因此，如果想在运行时通过修改文件的方式来添加Prompt，需要确保通过对应方法标记该Prompt为需要保存，否则在下一次保存时会被删除。