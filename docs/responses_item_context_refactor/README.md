# Responses Item 上下文化重构备注

> 状态：计划中，延期到下一版本实施。
>
> 当前版本仅合入 Responses reasoning Item 提取修复，确保 `summary` 为空时仍能读取
> `content[].reasoning_text`；本文描述的上下文模型重构不进入当前版本。

## 背景

当前 Responses 兼容层使用两份相关但用途不同的数据：

- Maisaka 历史中的 `AssistantMessage.content` 和 `tool_calls`；
- `ProviderState.output_items` 中保存的完整 `response.output`。

同 Provider、同端点、同模型且消息指纹匹配时，Responses 客户端会绕过通用
assistant 消息，原样回放 `ProviderState.output_items`。这种方案适合低侵入地加入
Responses 支持，但长期存在两份运行时表示：Maisaka 业务看到的是通用消息，实际
Responses 请求使用的可能是完整 Items。

OpenAI 对 `store=false` 的无状态续接建议是：把上一轮完整 `response.output` 追加到
下一轮 `input`，并保留 reasoning、message、function call 和其他输出 Item。参考：
[Manually manage conversation state](https://developers.openai.com/api/docs/guides/conversation-state#manually-manage-conversation-state)。

## 重构目标

将“一次模型响应产生的有序 Item 批次”作为 Maisaka assistant 历史的唯一运行时
事实来源：

```text
response.output[]
  -> AssistantOutput
  -> APIResponse / LLMResponseResult
  -> AssistantMessage.output
  -> Maisaka chat_history
  -> Provider 请求转换
```

正文、推理、function call 和原生工具展示均从同一个 `AssistantOutput` 派生，不再
作为可以独立修改的并行状态保存。

本文重构不采用以下方案：

- 不为 `web_search_call`、`file_search_call` 等每一种原生工具建立内部类；
- 不把一次响应的每个 Item 拆成独立顶层历史消息；
- 不引入 `previous_response_id`，继续使用 `store=false` 和本地 Items 回放；
- 不让 Maisaka 业务模块理解特定 Provider 的 Item 细节。

## 建议数据模型

只新增响应批次和回放范围两个核心概念：

```python
@dataclass(frozen=True, slots=True)
class ProviderScope:
    schema_version: int
    client_type: str
    provider_name: str
    endpoint_fingerprint: str
    model_identifier: str


@dataclass(frozen=True, slots=True)
class AssistantOutput:
    items: tuple[dict[str, Any], ...]
    replay_scope: ProviderScope | None
```

约束：

- `items` 完整保留 Provider 返回顺序和未知字段；
- 内部保存前执行深拷贝，外部只能获取副本，禁止原地修改；
- Provider 返回的未知 Item 同范围时允许原样回放；
- `replay_scope=None` 表示通用输出，只能通过通用消息转换发送；
- `content`、`reasoning_parts`、`tool_calls` 和原生工具摘要是只读投影；
- 如需缓存投影，必须和 `AssistantOutput` 一次性构造，不能独立赋值。

`AssistantMessage` 调整为持有一个完整输出批次：

```python
@dataclass(slots=True)
class AssistantMessage(LLMContextMessage):
    output: AssistantOutput
    timestamp: datetime
    source_kind: str = "assistant"
```

为了控制迁移规模，可以保留 `content`、`reasoning_parts` 和 `tool_calls` 只读属性，
但这些属性必须从 `output.items` 派生。

## Item 处理规则

### 同 Provider、同端点、同模型

当 `replay_scope` 与本次请求完全匹配时，按原顺序把整个 `items` 批次追加到
Responses `input`。不得只回放 message 或 function call。

### Provider 或模型不匹配

从同一个 `AssistantOutput` 生成可移植投影：

- message 转换为普通 assistant 消息；
- function call 转换为通用工具调用；
- reasoning 和 Provider 原生工具 Item 不跨 Provider 原样发送；
- 原始 Item 仍可用于当前响应的日志和展示。

### 非 Responses 客户端

Chat Completions、Gemini 和插件客户端返回结果时，构造最小通用 Item 批次。通用
批次可以包含 message、reasoning 和 function call，但不带原生回放范围。

## 工具循环

一次 assistant 输出批次是历史原子单元：

```text
AssistantMessage.output
  - reasoning item
  - function_call item A
  - function_call item B
ToolResultMessage A
ToolResultMessage B
```

`ToolResultMessage.tool_call_id` 必须继续等于 Responses `function_call.call_id`。
Responses 请求转换时生成：

```json
{
  "type": "function_call_output",
  "call_id": "call_xxx",
  "output": "工具结果"
}
```

历史选择、移动、裁剪和折叠必须以整个 assistant 输出批次及其工具结果为协议原子
段，禁止在普通上下文裁剪中拆分 `AssistantOutput.items`。

## Hook 与状态修改

所有修改都必须返回新的 `AssistantOutput`，不能同时修改投影字段和原生 Items。

| 操作 | 处理方式 |
| --- | --- |
| 复制或原样移动消息 | 保留原 `AssistantOutput` |
| `after_response` 未修改正文和工具调用 | 保留原生 Items 和回放范围 |
| `after_response` 修改正文或工具调用 | 使用修改结果重建通用批次，清除回放范围 |
| 删除部分 function call | 使用剩余内容重建通用批次，清除回放范围 |
| 折叠工具历史 | 整个原生输出批次退出原始工具链历史 |
| 日志脱敏 | 只修改日志副本，不影响运行时批次 |

第一阶段保持现有 `before_request` Hook 的 Chat 风格 `messages` 协议：

- Hook 未修改 `messages` 时，继续使用原始 Item 批次；
- Hook 修改 `messages` 时，本次请求整体采用 Hook 返回的通用消息，不夹带隐藏原生
  Items；
- 暂不把原生 Items 混入现有 `messages` 字段，以免破坏旧插件；
- 如以后确实需要插件操作 Items，应新增版本化 Hook，而不是改变旧字段语义。

## 日志与 WebUI

运行时上下文和诊断响应继续分工：

- `AssistantOutput`：参与后续请求的运行时上下文；
- `provider_response`：完整 Provider 响应诊断信息，不参与请求构建；
- `request_messages`：适合人工阅读的通用消息投影；
- `request_items`：Responses 客户端最终实际发送的 Items。

Prompt JSON 和 WebUI 对超大 base64 进行省略或摘要，但不能修改运行时 Items。WebUI
按一次响应分组展示 reasoning、原生工具调用、function call 和最终 message。历史输出
在下一轮再次出现在 `request_items` 属于正常的上下文回放，不应被误判为同一条记录
内部的重复输出。

## 分阶段实施

### 第一阶段：建立模型，不改变行为

1. 新增 `AssistantOutput`、`ProviderScope` 和 Item 投影函数。
2. 为当前 `ProviderState` 提供临时转换入口。
3. 补齐 Item 顺序、未知字段和不可变性测试。

### 第二阶段：贯通响应链路

1. Responses 客户端直接从完整 `response.output` 构造 `AssistantOutput`。
2. `APIResponse`、`LLMResponseResult` 和 `ChatResponse` 传递同一个输出对象。
3. 流式和非流式响应统一在完整响应完成后生成输出批次。

### 第三阶段：改造 Maisaka 上下文

1. `AssistantMessage` 攓为持有 `AssistantOutput`。
2. 正文、推理、工具调用和原生工具摘要改为只读投影。
3. 工具配对、历史选择和上下文计数以响应批次为单位。

### 第四阶段：请求从上下文构建

1. Responses 客户端从 `AssistantMessage.output` 原样回放匹配 Items。
2. 不匹配时从相同输出对象生成通用消息。
3. Chat Completions、Gemini 和插件客户端消费通用投影。

### 第五阶段：统一失效逻辑

1. Hook 改写统一重建通用输出批次。
2. 历史裁剪、工具调用删除和折叠不再手工处理 ProviderState。
3. 保留原对象表示未修改，生成新对象表示已修改。

### 第六阶段：清理旧结构

1. 删除 `ProviderState` 和 `message_fingerprint`。
2. 删除各层 `provider_state` 透传参数。
3. 将 `native_tool_calls` 改成从输出 Items 派生的展示数据。
4. 保留 `provider_response` 作为独立诊断载荷。

## 预计影响文件

主要涉及：

- `src/llm_models/payload_content/`
- `src/llm_models/model_client/base_client.py`
- `src/llm_models/model_client/openai_responses_client.py`
- `src/llm_models/model_client/openai_client.py`
- `src/llm_models/model_client/gemini_client.py`
- `src/llm_models/model_client/plugin_client.py`
- `src/common/data_models/llm_service_data_models.py`
- `src/llm_models/utils_model.py`
- `src/maisaka/context/messages.py`
- `src/maisaka/context/history.py`
- `src/maisaka/context/post_processor.py`
- `src/maisaka/chat_loop_service.py`
- `src/maisaka/reasoning_engine.py`
- `src/plugin_runtime/hook_payloads.py`
- `src/maisaka/display/`
- `src/maisaka/monitor/`

## 验收测试

- 纯文本和多个 message Item；
- 多个 reasoning Item，以及每个 Item 中多个 summary/content part；
- summary 优先、`reasoning_text` 补充读取；
- reasoning、function call、tool result 和最终 message 的多轮工具循环；
- 多次 web search 和未知原生 Item 的顺序保留；
- 同 Provider、同端点、同模型时，历史 Items 与原始 output 深度相等；
- 切模型、切 Provider、切端点时只发送通用投影；
- Hook 未修改时保留原生批次，修改后不再回放过期 Items；
- 删除部分工具调用后不残留旧 function call；
- 裁剪、折叠、移动工具结果后协议仍完整；
- 流式和非流式生成一致的最终 `AssistantOutput`；
- 大型 base64 在运行时保留，在 Prompt JSON 和 WebUI 中被安全省略；
- Chat Completions、Gemini 和插件客户端既有行为不回归。

## 当前版本发布边界

当前版本只处理已经确认的 Responses reasoning 提取缺陷：

- 保留所有 reasoning Item；
- 每个 Item 优先读取非空 `summary[].text`；
- `summary` 为空时读取全部 `content[].reasoning_text`；
- 不在当前版本迁移 Maisaka 上下文数据模型；
- 不在当前版本移除 `ProviderState`。

待当前 bugfix 稳定发布后，再按本文阶段拆分提交，避免把兼容性修复和大规模上下文
重构混入同一个版本。
