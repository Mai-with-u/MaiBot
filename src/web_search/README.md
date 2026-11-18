# 联网搜索后端

通过 `WebSearchToolProvider` 注册 `web_search(query, max_results=5)` 和 `web_fetch(url)`。
服务选择、协议转换和工具实现均独立于 WebUI。默认未分配，不开放统一联网工具。

插件可声明 `web.search` / `web.fetch` 能力，通过 SDK 的 `ctx.web.search(query, max_results=5)`
和 `ctx.web.fetch(url)` 复用已分配服务。插件接口直接返回完整结构化结果，无需聊天 ID，
不写入工具结果缓存；序列化结果超过 500000 字符时明确报错。SDK 调用示例与返回字段见
[插件联网接口文档](https://github.com/Mai-with-u/maibot-plugin-sdk/blob/main/docs/web.md)。

## 配置

在 `model_config.toml` 的独立 `web` 节配置服务实例，无需声明模型。
以下为示例；将示例密钥替换为实际密钥后再启用，不要把密钥提交到仓库。

```toml
[web]
preview_chars = 4000

[model_task_config.web_search]
model_list = ["deepseek"]

[web.services.deepseek]
adapter = "deepseek"
api_key = "YOUR_DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
timeout = 60.0

[web.services.tavily]
adapter = "tavily"
api_key = "YOUR_TAVILY_API_KEY"
search_depth = "basic"
extract_depth = "basic"
timeout = 60.0

[web.services.step]
adapter = "stepsearch_mcp"
server_ref = "stepsearch"
category = ""
timeout = 60.0

[web.services.anysearch]
adapter = "anysearch"
api_key = "YOUR_ANYSEARCH_API_KEY"

[web.services.exa]
adapter = "exa"
api_key = "YOUR_EXA_API_KEY"

[web.services.firecrawl]
adapter = "firecrawl"
api_key = "YOUR_FIRECRAWL_API_KEY"
```

HTTP 服务省略 `base_url` 时使用官方地址；DeepSeek 同时接受根地址和 `/v1` 地址。
StepSearch 的 `server_ref` 指向 `bot_config.toml` 中已有且已启用的 `mcp.servers` 名称，
地址和鉴权只在 MCP 配置维护。不会自动启动第二套 MCP 连接。

六种适配器均支持搜索和读取。在 `model_task_config.web_search.model_list` 中选择一个服务名称，
搜索和网页读取统一使用该服务。留空时不注册工具，残留的直接调用也会被拒绝。
仅添加服务或填写旧的 `web.enabled` / `search` / `fetch` 字段不会启用联网功能。
不支持的过滤参数会报错，不会将日期过滤悄悄转换成搜索关键词，也不自动切换服务或重试。
Tavily 的搜索深度、提取深度和 StepSearch 的类别在服务实例中配置，不由模型选择。
AnySearch 使用 v1 Search / Extract，单次搜索最多 10 条；需要填写 API Key。
Exa 使用 Search 返回 highlights 搜索片段，Contents 返回完整正文。
Firecrawl 使用 v2 Search / Scrape，搜索词最多 500 字符，网页读取返回 Markdown。
AnySearch 和 Firecrawl 的自定义地址可填写根地址或分别带 `/v1`、`/v2` 的 API 地址。

默认模板由 `src/config/default_model_config.py` 与配置类生成，版本为 `1.17.24`。
读取配置通过稳定配置代理完成；每次调用使用配置快照，下一次调用生效于新配置。
WebUI 在模型设置左侧加号菜单中提供“添加联网搜索”，支持添加、编辑和删除服务。
在“功能分配 → 联网搜索 (web_search)”选定服务后才启用搜索和读取。已分配的服务需先取消分配才能改名或删除。
StepSearch MCP 复用插件扩展中已有的连接，原始 MCP 工具不受统一联网任务分配影响。
DeepSeek 可选择检测到的模型提供商组，复制当前地址和密钥到联网配置，也可选择“自定义”手动填写。
管理界面复用配置加载与分节保存接口，`web` 节整体替换，其他模型配置保持不变。

## 工具结果

- 搜索保留 `title`、`url`、`snippet`、供应商提供的 `summary` 和 `published_at`；未知日期为空。
- 网页读取返回 `content_kind=page_text`，内容为供应商提取的正文，不额外调用模型总结。
- 结果包含服务名称、获取时间、`result_id`、`total_chars`、`truncated` 和 `next_offset`。
- 模型历史仅包含限长预览，完整返回内容存入现有的会话级 `ToolResultStore`。
- 开启联网功能会提供 `read_tool_result`，不依赖实验性的长结果整理开关。
- `read_tool_result(result_id, keyword=...)` 定位原文，或通过 `offset` / `max_chars` 分段读取。
- 搜索的原文是规范化结果 JSON，网页的原文是正文；偏移按字符计算，不含预览头尾说明。
- 暂存沿用现有每会话最近 20 条、重启失效的规则。必须传入真实聊天 `session_id`。
- 供应商响应最多 8 MiB，单条暂存文本最多 500000 字符；超限明确失败，不伪装为完整结果。

超时、鉴权失败、限流、协议错误和网页提取失败均返回失败结果；空搜索结果是成功的空列表。
错误不回显上游响应体或凭据。调用取消会继续向上传播。

## 重名工具

统一联网工具在插件、MCP Provider 之前注册。注册表保留先注册工具的裸名称，
重名项按真实来源加前缀，例如 `mcp_stepsearch_web_search`，不隐藏原始 MCP 工具。
名称超过模型接口的 64 字符限制或前缀名称再次冲突时，附加由来源生成的稳定短哈希。
工具声明不会被原地修改；执行时还原远端名称，指定 MCP 服务器，并保留原调用 ID 和聊天上下文。

统一服务选择 StepSearch 时直接通过 `MCPService` 指定服务器调用，不经过模型可见别名。
原始 MCP 工具仍保持原参数和原输出格式，不经过统一联网适配器。

## 协议来源

- [DeepSeek Search / Fetch](https://api-docs.deepseek.com/zh-cn/guides/search/)
- [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- [AnySearch Search](https://www.anysearch.com/docs/api-endpoints/v1-search) / [Extract](https://www.anysearch.com/docs/api-endpoints/v1-extract)
- [Exa Search](https://exa.ai/docs/reference/search) / [Contents](https://exa.ai/docs/reference/get-contents)
- [Firecrawl Search](https://docs.firecrawl.dev/api-reference/endpoint/search) / [Scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape)
- StepSearch 使用其 MCP 声明及结构化响应：搜索 `query/n/category`，读取 `url`；
  搜索结果为 `results`，网页结果为 `page.markdown`。

## 验证

`pytests/web_search` 覆盖请求映射、错误语义、超时取消、会话隔离与重名路由。
运行涉及全局配置的测试时建议设置 `MAIBOT_INSTANCE_DIR` 为临时实例，避免触发实际配置升级。
