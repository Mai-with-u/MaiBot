# Sign in with ChatGPT（本机预览）

`chatgpt` 客户端让 MaiBot 使用用户明确授权的 ChatGPT 套餐推理能力。它与 `openai` / `openai_responses` 的 API Key 配置相互独立。请确认账号具有 OpenAI 当前开放的 SIWC 权限及可用额度；订阅本身并不保证所有账号、模型或功能可用。

实现遵循 OpenAI 的 [本地/开源应用指南](https://developers.openai.com/siwc/token-sharing-open-source)，使用官方动态注册和公共 `https://api.openai.com/v1/responses`。不读取 Codex 登录记录、浏览器 cookie 或其他应用令牌，不需要中转服务。当前本地凭据存储支持 **macOS/Linux**；Windows 需实现受保护存储后再启用。

## 连接与选择账号

在运行 MaiBot 的同一台电脑、同一系统用户下执行：

```sh
maibot chatgpt login --directory /path/to/instance --account personal
maibot chatgpt accounts --directory /path/to/instance
maibot chatgpt models --directory /path/to/instance --account personal
```

登录命令会先说明用途并要求确认，然后打开系统浏览器的 OpenAI 授权页，短暂监听 `127.0.0.1` 随机端口。请在官方页面检查账号、工作区、套餐权限及用量设置。不要把密码、API Key 或 OAuth 令牌粘贴到聊天、配置表单或终端参数中。

每个账号/工作区使用独立标签，标签允许 1–64 位字母、数字、下划线和短横线。同一注册再次登录、退出后重新连接时，复用原标签；不自动创建另一注册来绕过失败。每个提供商显式保存 `chatgpt_account`，没有全局“当前账号”或失败后换账号的行为。

在 WebUI 模型提供商中选择 **ChatGPT（Sign in with ChatGPT）**，填写相同标签，保留 API Key 为空。保存后获取模型列表，只选择该账号实际返回的模型。手动配置示例：

```toml
[[api_providers]]
name = "ChatGPT personal"
client_type = "chatgpt"
chatgpt_account = "personal"
base_url = "https://api.openai.com/v1"
api_key = ""
max_retry = 1
timeout = 120
retry_interval = 4

[[models]]
name = "chatgpt-reply"
model_identifier = "REPLACE_WITH_SLUG_FROM_MODELS"
api_provider = "ChatGPT personal"
model_type = "llm"
price_in = 0
price_out = 0
send_temperature = false
force_stream_mode = true
```

`price_in/out = 0` 只是 MaiBot 内部统计字段，不表示免费或不限量。模型名占位符必须替换；可用 slug 不应硬编码为某个 API 模型。按实际模型能力配置 `utils`、`replyer`、`planner` 等任务的 `model_list`，不配置其他提供商作为隐式计费后备。模型发现使用 `/v1/models` 返回的 `models` 数组，仅展示 `visibility=list`，使用 `slug` 调用、`display_name` 展示。

## 支持范围与故障行为

- 当前提供文本、账号支持的图片理解、本地 function 工具及账号支持的 web search。函数统一放进 `maibot` namespace，回放的 function call 使用相同 namespace。
- 请求固定 `stream=true`、`store=false`，每次发送完整上下文。system 转为 developer；移除预览接口不支持的 temperature、top_p、max_output_tokens 等采样字段。只有收到 `response.completed` 才成功，失败、中断及缺失终态都报错。
- 不支持 embeddings、语音转录、音视频输入/生成、文件上传、file search、code interpreter、image generation 或 hosted MCP。这些功能不能通过把 SIWC 令牌填成 API Key 来启用。需要这些功能时应单独评估并明确选择支持的提供商。
- 不支持自定义鉴权头、代理 base URL 或模型级 header/query 注入；令牌仅发送给官方端点。此初始实现禁用环境 HTTP 代理和重定向。
- 登录、授权、额度、推理或任务超时失败时终止该请求，不自动切换另一账号或 API 计费提供商，也不自动打开登录页面。401/403 等错误需要用户检查或重新连接，不自动重放可能已计费的推理请求。
- MaiBot 的 function 工具仍受原有工具权限和运行环境约束。连接聊天平台前应先检查群/私聊策略、自动发言和工具能力。

## 凭据与恢复

独立注册保存在实例的 `data/chatgpt/`，目录权限 `0700`、文件 `0600`；包含敏感 OAuth 凭据，**不要提交、分享、上传故障附件或同步到公共位置**。配置文件中仅有标签。登录使用 fresh state、nonce、PKCE S256，并验证 ID token 的签名、issuer、audience、时间和账号绑定。稳定 host ID 和 issued client ID 会保留，以供同一注册再次登录。

刷新通过跨进程文件锁串行执行，替换凭据采用原子写入。新 grant 在 ID token 网络校验前写入受保护的待验证记录；JWKS 临时不可用时保留轮换后的 refresh token，下一次请求恢复校验，未经校验的 token 不参与推理。注册冲突、缺少套餐权限或身份不一致时停止，请使用原标签重新登录并核对官方页面。

停止 MaiBot 后退出选中的注册：

```sh
maibot chatgpt logout --directory /path/to/instance --account personal
```

该命令尝试撤销 renewable session，网络/5xx 错误有限重试，随后清除本地 access/refresh/ID token，保留注册映射。如果远端撤销无法确认，命令返回非零并明确提示到 ChatGPT 设置断开 MaiBot。可随时打开 [Manage usage](https://chatgpt.com/settings/usage) 检查额度和授权。退出前先停止 MaiBot，避免已发出的请求继续完成。

## 验证状态

新增测试覆盖生成签名身份的验证、模拟回调、PKCE、账号绑定、刷新锁和恢复、撤销失败，以及通过 OpenAI SDK + MockTransport 验证的 Responses 成功/错误/断流和禁止计费后备。2026-10-28 已在 macOS 上经用户明确同意，完成独立官方授权、模型目录发现和 `gpt-6.1-terra` 的真实 Responses 流式文本验证；MaiBot 客户端收到了完成终态。图片理解、真实工具往返、更多账号/工作区和其他系统尚待验证；此预览仍需维护者与自愿测试者继续检查。

官方参考：[登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)、[账号与会话](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)、[模型与推理](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)、[预览限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)。
