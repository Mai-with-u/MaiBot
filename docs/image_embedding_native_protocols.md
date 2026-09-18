# 图片嵌入官方地址自动适配（百炼 / 豆包）

`client_type = "openai"` 的 Provider 在执行**图片嵌入**任务（A_Memorix 图片记忆、图片向量等）时，
会按 Provider 地址自动识别官方原生协议并切换请求格式，**无需再配置 `image_embedding_input` /
`image_embedding_body` 模板**。聊天、文本嵌入、音频等其他任务不受影响，仍走原 OpenAI 兼容逻辑。

## 自动识别的官方地址

| 服务商 | Provider base_url | 实际请求端点 |
| --- | --- | --- |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` 或 `.../api/v1` | `https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding` |
| 火山引擎豆包（普通 API） | `https://ark.cn-beijing.volces.com/api/v3` | `https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal` |
| 火山引擎豆包（Coding Plan） | `https://ark.cn-beijing.volces.com/api/plan/v3` | `https://ark.cn-beijing.volces.com/api/plan/v3/embeddings/multimodal` |

匹配按 hostname 与路径精确比对（兼容尾部斜杠），不做子串/子域名猜测；未识别的地址保持原有行为
（要求显式模板）。豆包的端点直接从 Provider 基路径派生——Coding Plan 套餐网关同样提供
`/embeddings/multimodal`（已实测），且套餐 Key 在普通 API 上无权限，因此**不要**把套餐地址改写到
`/api/v3`。百炼业务空间专属域名、其他地域的 Ark 域名暂未覆盖，仍需走模板配置。

## 配置示例

```toml
[[models]]
name = "qwen-image"
model_identifier = "qwen3-vl-embedding"
api_provider = "BaiLian"           # client_type = "openai"，地址为官方域名
extra_params = { parameters = { dimension = 1024 } }   # 百炼原生字段；顶层 dimensions 也会自动归一到 parameters.dimension

[[models]]
name = "doubao-image"
model_identifier = "doubao-embedding-vision-251215"    # 使用账号已开通的具体版本或 Endpoint ID
api_provider = "ARK"
extra_params = { dimensions = 1024 }                    # 豆包顶层 dimensions；instructions 等同样按顶层透传

[model_task_config.image_embedding]
model_list = ["qwen-image"]
```

`extra_params` 中的 `headers`、`query` 继续作为请求头/查询参数覆盖；其余键按原生协议并入请求体。
`model`、`input`（豆包另含 `encoding_format`，固定为 float）由本次图片输入独占，
试图通过 extra_params 覆盖会直接报错。

## 优先级与兼容性

1. 显式模板优先：配置了 `image_embedding_input` / `image_embedding_body` 时，即使是官方地址也按用户模板走
   OpenAI 兼容 `/embeddings` 逻辑，不会覆盖用户定制。
2. 失败不降级：识别为原生协议后不会在失败时改走 `/embeddings`，错误按原样暴露
   （连接/超时 → 网络错误，HTTP 状态 → RespNotOk，结构异常 → 解析错误）。
3. Coding Plan 与普通 API 的 Key 互不通用：套餐 Key 走套餐网关（含图片嵌入端点，已实测可用），
   普通 Key 走 `/api/v3`；两侧端点由 Provider 基路径自动区分，无需手动指定。

## 向量空间指纹

原生分支为每次图片嵌入生成精确的协议指纹（协议版本 + 实际端点 + 模型标识 + dimension/instructions
等有效参数），图片内容与密钥不参与计算。指纹变化代表向量空间变化：

- 切换百炼/豆包、修改模型或维度参数后，图片记忆索引会按指纹隔离，历史图片需要重新嵌入；
- 不要把两家模型放进同一个 `image_embedding.model_list` 依赖自动切换——同维度不代表同一向量空间。

协议依据：[百炼多模态向量 API](https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference)、
[火山方舟向量化指南](https://docs.volcengine.com/docs/ark/vectorization?lang=zh)。
