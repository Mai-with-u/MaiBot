# Responses Item 上下文化重构方案

> 状态：已按扁平 Item 时间线和 schema v6 诊断契约实施。
>
> 本文记录当前运行时契约。旧日志和旧插件载荷中的废弃字段只在读取兼容入口忽略，
> 不进入运行时数据模型。

## 1. 背景与目标

旧实现以 Chat Completions 的 assistant message 为中心，把正文、reasoning、function call
和 Provider 状态聚合到一条消息中。Responses API 的真实输出却是有序的独立 Items，例如
`reasoning`、`message`、`function_call` 和 Provider 内置工具活动。两种表示并存会导致：

- 业务层看到的消息与实际回放给 Responses 的内容不一致；
- 修改、Hook、裁切和缓存统计需要同步两份状态；
- 多个 reasoning、多个 message 或未知 Provider Item 被错误压缩；
- 一条 assistant 消息无法准确表达完整“模型 → 工具 → 模型”循环。

本次重构采用 Item-first 模型：

1. `ContextItem[]` 是上下文内容、顺序、修改和展示的唯一事实来源。
2. 每个 Provider 输出 Item 独立进入历史，列表位置是唯一顺序。
3. 只保留协议真正需要的关系：`call_id` 与 `logical_turn_id`。
4. Provider 原生回放片段附着在所属 Item，不建立响应级上下文实体。
5. 一次 API 请求的 Provider、模型、响应 ID 和 token 等信息进入独立诊断记录。
6. Chat message 只是发送边界的临时投影，不反向定义内部上下文模型。

参考：

- [Messages vs. Items](https://developers.openai.com/api/docs/guides/migrate-to-responses#messages-vs-items)
- [Manually manage conversation state](https://developers.openai.com/api/docs/guides/conversation-state#manually-manage-conversation-state)
- [Preserve reasoning across calls](https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls)

## 2. 已确认边界

- 一次性切换到 Item，不保留 `Message | ContextItem` 运行时双轨结构。
- 不以 assistant 输出对象或一次 API 响应作为上下文基本单位。
- 不把 reasoning 表述为 assistant message 的固有字段。
- 不为每一种 Provider 内置工具建立业务类型。
- 不在本阶段引入 `previous_response_id`；Responses 继续使用本地 Item 回放。
- Chat 请求统一不传 reasoning，后续可另行研究能力自动检测。
- Replyer 的 reasoning-only 续写只支持 Responses，不下沉到通用模型请求层。
- replay fragment 只存在于内存，不进入持久化历史和请求快照。
- 不设置独立的 16 MB replay 上限；生命周期跟随既有历史裁切。
- 明文 reasoning 可进入推理过程诊断页面，不按隐私字段处理。

目标数据流：

```text
Provider response.output[]
  -> ContextItem[]（保持原顺序）
  -> APIResponse / LLMResponseResult
  -> MaiSaka 单 Item 历史 envelope
  -> Item 选择 + 工具协议闭包
  -> Provider adapter
  -> Responses Items / Chat Messages / Gemini Contents

Provider 调用链
  -> GenerationAttempt[] -> GenerationTrace
  -> 日志与 Dashboard（不参与上下文）
```

## 3. Context Item 模型

### 3.1 Item 元数据

```python
@dataclass(frozen=True, slots=True)
class ContextItemMeta:
    item_id: str
    logical_turn_id: str | None
    timestamp: datetime
```

字段语义：

- `item_id`：MaiSaka 内部稳定 ID，不要求等于 Provider Item ID；
- `logical_turn_id`：一次完整“模型 → 工具 → 模型”循环的 ID；
- `timestamp`：用于历史选择、日期边界和诊断展示；
- `ContextItem[]` 的列表位置：唯一顺序来源。

移动或重排 Item 只改变列表位置，不需要批量改写元数据，也不会因为位置变化使 replay
fragment 失效。

旧日志或旧插件载荷可能包含 `response_group_id`、`ordinal`。兼容入口读取时直接忽略这些
字段，不报错、不回填，也不在内存中建立对应属性。

### 3.2 规范 Item 类型

```python
ContextItem = (
    SystemMessageItem
    | UserMessageItem
    | AssistantMessageItem
    | ReasoningItem
    | FunctionCallItem
    | FunctionCallOutputItem
    | ProviderActivityItem
    | ProviderOpaqueItem
)
```

关键规则：

- `ReasoningItem`、`AssistantMessageItem`、`FunctionCallItem` 始终独立；
- Responses 未知类型保留为 `ProviderOpaqueItem`；
- Provider 内置工具统一表示为 `ProviderActivityItem`；
- `FunctionCallOutputItem` 是应用工具结果，不伪装成 user message；
- content part、工具参数和 replay payload 都使用不可变表示；
- `content`、`reasoning`、`tool_calls`、`native_tool_calls` 只能从 Items 只读派生，
  不能作为第二份可写事实来源。

MaiSaka 可以继续保存 Session、Reference、剩余使用次数等应用元数据，但历史 envelope
必须与 Context Item 保持零或一关系：每条 `ModelOutputContextMessage` 精确持有一个输出
Item，不能重新聚合正文、reasoning 或工具调用。

## 4. 必要关系

### 4.1 function call 关系

```text
FunctionCallItem.tool_call.call_id
                ↕
FunctionCallOutputItem.call_id
```

约束：

- `call_id` 在当前时间线内唯一；
- output 必须有对应 call；
- output 必须位于 call 之后；
- call 和 output 必须具有同一个非空 `logical_turn_id`；
- 一个 call 最多有一个应用侧 output。

Provider 内置工具通常没有应用侧 output，因此不参与此配对关系。

### 4.2 logical turn 关系

一次逻辑轮次覆盖完整工具循环，而不是单次 API 响应：

```text
logical_turn_1
  ReasoningItem
  FunctionCallItem A
  FunctionCallOutputItem A
  ReasoningItem
  FunctionCallItem B
  FunctionCallOutputItem B
  AssistantMessageItem
```

Planner 开始处理一次触发时创建 `logical_turn_id`，同一次内部工具循环中的后续模型请求、
工具结果以及最终正文都继承它。`wait` 暂停恢复和跨聊天流工具也必须保存并恢复该 ID。

这修复了工具结果以前没有逻辑轮次、因而裁切时无法识别完整工具循环的问题。

### 4.3 共享关系内核

关系规则由无状态纯函数直接分析 `Sequence[ContextItem]`，不再建立 `ContextTimeline` 或其他
包装容器。不同生命周期边界采用三种模式：

- `REQUEST_CONTEXT`：发送给模型前必须具有完整 call/output 闭包；
- `MODEL_OUTPUT`：只允许模型输出 Item，并允许尚待执行的 function call；
- `HISTORY`：允许已登记的 pending wait call，其余非法工具轮次整体移除。

关系内核统一检查重复 Item ID、重复 call/output、孤儿 output、未回答 call、错序以及 turn
错配。裁切、Hook、Replay、Provider 输入和历史清理复用同一套规则。

## 5. Responses 输出与诊断

### 5.1 逐 Item 接收

Responses 客户端按 `response.output[]` 的原始顺序逐项构造 Items：

```text
response.output[0] -> ContextItem[0]
response.output[1] -> ContextItem[1]
response.output[2] -> ContextItem[2]
```

每个原始输出只对应一个 Item 和一个可选 replay fragment。输出结束后，上层把本次请求的
`logical_turn_id` 绑定到所有输出 Items，再按列表顺序追加到历史。

### 5.2 GenerationTrace

API 响应归属信息不参与上下文，统一进入诊断对象：

```python
@dataclass(frozen=True, slots=True)
class GenerationTrace:
    provider: str
    endpoint: str
    model: str
    response_id: str | None
    status: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    output_item_ids: tuple[str, ...]
```

`output_item_ids` 记录 Provider 原始 API 响应产生的 Items。Hook 随后即使修改或删除 Item，
诊断记录仍描述原始响应，不反向修改上下文，也不参与裁切和 replay scope 判断。

Dashboard 可以据此展示一次 API 调用产生的 Item 集合，以及 Provider、endpoint、模型、
状态和 token 使用情况。

### 5.3 GenerationAttempt 调用链

`GenerationTrace` 只描述一次成功 Provider 响应；完整逻辑请求使用有序
`GenerationAttempt[]` 记录每一次真实 Provider 调用，包括失败、重试、模型切换、Hook
重新生成和 Replyer reasoning continuation。每个 Attempt 保存：

- workflow purpose/attempt、Provider attempt 和模型内 attempt；
- 状态、开始时间、耗时、Provider、endpoint、model 和 wire protocol；
- 实际请求 Items、工具定义与请求参数；
- 统一脱敏后的 wire request/response；
- Provider 原始输出 Items；
- 成功 trace 或结构化错误。

顶层 `request_items/output_items` 始终表示 Hook 后最终采用的输入和输出；Attempt 始终表示
实际发生的原始 Provider 调用。trace 的 `output_item_ids` 因而只关联所属 Attempt 的原始
输出，Hook 不会改写它。

## 6. Provider 原生回放

### 6.1 Provider scope

```python
@dataclass(frozen=True, slots=True)
class ProviderScope:
    schema_version: int
    client_type: str
    provider_name: str
    endpoint_fingerprint: str
    model_identifier: str
```

只有 client、Provider、endpoint、model 和 schema 均兼容时，才允许原样 materialize。

### 6.2 单 Item fragment

```python
@dataclass(frozen=True, slots=True)
class ProviderReplayFragment:
    scope: ProviderScope
    payload_json: bytes
    payload_sha256: str
```

规则：

- fragment 只属于一个 Item；
- 同 scope 时逐 Item 原样回放；
- scope 不匹配时尝试可移植投影；
- 无可移植投影的 reasoning、Provider activity 或 opaque Item 省略；
- 修改 Item 内容时只清除该 Item 的 fragment；
- 只重排列表时保留 fragment；
- fragment 不进入 Hook、快照、持久化历史或普通日志。

使用 JSON bytes 而不是公开字典，确保嵌套结构不可变；`materialize()` 每次返回新对象。

## 7. Provider 投影

### 7.1 Responses

同 scope 且 fragment 有效时，按当前列表顺序 materialize；否则：

- assistant message 投影为普通 message input Item；
- function call 投影为通用 function call；
- function output 投影为 `function_call_output`；
- 不把 reasoning summary 伪造成原始 reasoning；
- 不把 Provider activity 或未知 Item 伪造成普通文本。

### 7.2 Chat Completions

Chat 行为保持不变：规范 Items 只在发送边界投影为普通 messages。

- 所有 reasoning 表示都不传入 Chat；
- Chat 不读取 replay fragment 或 GenerationTrace；
- 相邻模型输出 Items 临时折叠为 assistant message；
- tool result、user 或 system Item 会结束当前 assistant 折叠段；
- 多个 assistant message Item 的文本按原列表顺序合并；
- function calls 进入同一 assistant message 的 `tool_calls`；
- Chat 返回没有原生 Item 数组时，按确定性的通用顺序构造 Items。

Chat 默认不会出现 Responses 的 reasoning-only 无正文问题，因此不实现对应续写。

### 7.3 Gemini 与插件客户端

- 消费同一套通用 Items；
- 文本、图片和 function call 使用各自 wire 投影；
- 不支持 reasoning 输入时直接省略，不转成 assistant 正文；
- 未实现原生 replay 时不创建伪造 fragment；
- 插件请求显式携带 `item_schema_version`；
- 插件响应以版本化 `output_items` Item body 为主，Host 生成 `item_id/timestamp` 并绑定请求
  turn；
- 非模型输出 Item、版本不匹配或非法关系直接判定 Provider 调用失败；
- 旧 `content/reasoning/tool_calls` 标量协议只兼容一个 Item schema 版本并输出弃用警告，
  下个版本删除。

## 8. 裁切与协议闭包

### 8.1 基本原则

- 普通 Item 可以独立保留或删除；
- function call 与 output 必须成对处理；
- 只要一个逻辑轮次包含应用工具调用，该工具轮次就整体保留或整体删除；
- 裁切完成后必须统一验证工具链闭包；
- 无法形成合法闭包时删除整个无效工具轮次，不留下孤儿 Item。

普通 reasoning、assistant message 或 Provider activity 不因为来自同一次 API 响应而被强制
绑定。只有实际工具关系会提升裁切原子性。

### 8.2 处理顺序

```python
selected_item_ids = select_items_by_budget(timeline.items)
selected_item_ids = expand_selected_tool_turns(timeline, selected_item_ids)
selected_items = preserve_original_list_order(timeline, selected_item_ids)
selected_items = validate_or_drop_invalid_tool_turns(selected_items)
```

补充规则：

- 保留 call 时必须保留匹配 output；
- 保留 output 时必须保留对应 call；
- 工具结果关联媒体随 call relation 一起处理；
- 日期边界和系统提醒不能插入 call/output 协议段；
- 并行 calls 先保持调用顺序，再按 call 顺序排列结果；
- 工具历史折叠以完整逻辑轮次为输入；
- 历史中发现未回答 call 或孤儿 output 时，删除其整个工具轮次并记录诊断计数。

## 9. Hook 契约

Hook 始终操作扁平 Item 列表：

- `before_request` 接收 `items`；
- `after_response` 接收 `output_items`；
- Hook 可以修改、增删和重排 Item；
- Host 直接采用返回列表顺序，不重建任何顺序字段；
- 原样返回的 Item 恢复原对象并保留 fragment；
- 内容或必要关系元数据变化的 Item 清除自身 fragment；
- 新增 Item 没有 fragment；删除 Item 不影响无关 Item。

Hook 修改按事务处理。Host 在接受整份结果前校验：

- Item ID 不重复；
- call ID 和 output 不重复；
- call/output 都有逻辑轮次；
- output 不早于 call；
- call/output 的逻辑轮次一致；
- 不存在孤儿 output。

任一工具关系非法时，忽略整次 Hook 修改并继续使用原列表，避免部分接受导致工具协议损坏。
未知 Item 类型或缺失必要字段同样拒绝整次修改。旧插件返回的废弃分组和顺序字段在反序列化
入口忽略，不作为错误，也不会被写回。

Hook 看不到 replay bytes、`encrypted_content` 或 Provider opaque payload；可读 reasoning
通过普通 `ReasoningItem` 字段提供。

## 10. Replyer 专用 reasoning-only 续写

该功能只在 `BaseMaisakaReplyGenerator` 编排，不进入 LLM Service、Provider 客户端或其他
模型请求模块。

触发条件：

- 当前是 Replyer；
- 当前端点为 Responses；
- 响应状态为 completed；
- 至少存在一个 `ReasoningItem`；
- 没有非空 `AssistantMessageItem`；
- 当前逻辑请求尚未续写过。

续写把第一次响应的 Items 追加到原 Context Items 后，再用同一 client、model、工具定义、
response format、request overrides 和生成选项请求一次。不能追加任何额外 user 消息。

续写前只执行一次 `before_model_request`；第二次响应完成后再执行 `after_response`。第二次
仍没有正文时进入现有空回复失败逻辑，不继续循环。两次调用的 token、耗时和尝试次数分别
记录并汇总。

## 11. 日志、Dashboard 与重放

Prompt 结构化记录使用 schema v6：

- `request_items`：实际模型请求的规范 Items；
- `output_items`：Hook 后最终采用的规范 Items；
- `generation_attempts`：按真实调用顺序保存的完整 Provider 调用链。

新记录不再双写顶层 `generation_trace`、`provider_request` 或 `provider_response`。Attempt
诊断不参与后续请求。

Dashboard：

- request/output 都按扁平列表逐 Item 展示；
- 显示 Item ID、logical turn 和时间戳；
- Attempt 按调用顺序折叠展示，最终成功调用默认展开，失败调用显示错误、模型切换和可用响应；
- 重放编辑器提交完整 `request_items`，结果返回完整 `output_items` 和 v6 Attempts；
- 工具定义显式传空数组时保持为空，只有字段缺失时才从源记录继承；
- 非法 JSON 保留编辑内容并显示错误，不静默恢复旧值。

Dashboard 读取入口把 v1-v5 旧日志迁移为 v6 内存模型，磁盘文件不改写，后端不维护第二套
迁移。迁移后组件、复制、匿名导出和重放统一消费 Items 与 Attempts，不保留双轨分支。

Dashboard 路由拆分为 schema/旧日志迁移、Context Item 展示、Generation Attempt 时间线、
Replay 编辑器以及页面状态与路由编排五个职责模块。

默认从诊断日志省略或脱敏：

- `encrypted_content` 和 thought signature；
- 大型 base64、二进制和文件内容；
- Provider opaque payload；
- 可能包含凭证的未知字段。

明文 reasoning 和 reasoning summary 默认可展示。Prompt Cache 统计基于最终 wire payload，
但缓存统计记录只保存摘要、hash 和 token 指标，不重新保存完整 wire payload。

## 12. 内存与持久化

- replay fragment 生命周期跟随所属历史 Item；
- 既有历史裁切删除 Item 时自然释放 fragment；
- 不维护独立 replay 字节预算或固定 16 MB 限制；
- Item 元数据和可移植内容可以持久化；
- replay fragment、encrypted content 和 opaque wire payload 不持久化；
- 进程重启后只恢复可移植 Items，不能继续原生 replay；
- 未知持久化 Item 类型应明确报错，不能静默伪造内容。

## 13. 单次切换与兼容边界

运行时只保留新结构：

- `APIResponse.output_items` 和 `LLMResponseResult.output_items` 是输出事实来源；
- `ChatResponse` 的正文、reasoning、工具调用和历史 envelope 全部从 Items 派生；
- 所有 Provider 客户端只消费 Context Items；
- MaiSaka 工具结果写入正确的 `logical_turn_id`；
- Hook 和 Dashboard 使用版本化 Item schema；
- `GenerationAttempt[]` 与 `GenerationTrace` 独立于上下文。

兼容只允许发生在边界：

- 旧 Chat 风格请求快照读取后一次性迁移为 Items；
- 旧日志的废弃元数据被忽略；
- 旧 Hook Item 载荷的废弃字段被忽略；
- 运行时不双写、不保留旧属性、不根据废弃字段重建关系。

## 14. 验收清单

### Item 与 Responses

- 一个响应可包含零个、一个或多个 message/reasoning/provider/tool Items；
- `response.output[]` 与输出 Items 保持一一对应和原列表顺序；
- 多个 reasoning 和多个 content parts 不丢失；
- 未知 Provider Item 在同 scope 可原样回放；
- 切 Provider、端点或模型时仅使用可移植投影。

### 关系与裁切

- call/output 通过 `call_id` 正确配对；
- 工具结果继承 call 的 `logical_turn_id`；
- 并行 calls 和乱序 results 能规范化；
- 普通 Item 能独立裁切；
- 工具循环只能按完整 logical turn 保留或删除；
- 裁切后没有孤儿 output、未回答 call 或轮次错配；
- wait 恢复和跨聊天流工具不丢失逻辑轮次。

### Chat 与其他 Provider

- Chat 请求中不出现 reasoning 输入字段；
- 相邻模型 Items 稳定折叠为普通 assistant messages；
- tool result 正确切断 assistant 折叠段；
- Chat、Gemini 和插件既有正文、图片与工具行为不回归；
- 所有端点切换都执行 fragment scope 校验。

### Hook、诊断与 Dashboard

- Hook 可增删改和重排扁平 Items；
- 非法工具关系使整次 Hook 修改失效；
- 旧废弃字段只在入口忽略；
- 每次成功 trace 只关联所属 Attempt 的原始 Item IDs，完整失败/重试/切换链不丢失；
- Dashboard 完整展示、编辑和重放各 Item 类型；
- 新记录不存在 Chat message 与 Item 并行事实源；
- 非法 JSON、空工具定义和诊断响应均按明确语义处理。

### Reasoning-only 续写

- 仅 Replyer + Responses + completed + 无正文且有 reasoning 时触发；
- 不追加 user 消息；
- 原上下文、模型和全部生成选项保持不变；
- 第一次输出逐 Item 参与第二次请求；
- Chat 和其他模块不触发；
- 最多续写一次，统计正确汇总。

## 15. 主要影响文件

- `src/llm_models/payload_content/`
- `src/llm_models/model_client/`
- `src/llm_models/request_snapshot.py`
- `src/common/data_models/llm_service_data_models.py`
- `src/services/llm_service.py`
- `src/maisaka/context/`
- `src/maisaka/chat_loop_service.py`
- `src/maisaka/reasoning_engine.py`
- `src/chat/replyer/maisaka_generator_base.py`
- `src/plugin_runtime/hook_payloads.py`
- `src/maisaka/display/`
- `src/maisaka/monitor/`
- `src/webui/routers/reasoning_process.py`
- `dashboard/src/lib/reasoning-process-api.ts`
- `dashboard/src/routes/reasoning-process.tsx`
- `dashboard/src/routes/reasoning-process/`

本次重构的最终原则是：内容属于 Item，顺序属于列表，工具关系属于 call/turn，Provider
响应归属属于诊断记录。任何回归都应在这四个边界内修正，不恢复响应级上下文容器或
assistant message 聚合所有权。
