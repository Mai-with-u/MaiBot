# 插件语音与联网协议

插件可以在现有 SDK 的 `@LLMProvider` 上声明 ASR、TTS 和联网操作。插件负责对接上游协议，MaiBot 负责模型分工、语音附件、试听、搜索结果存储和正文预览。基础注册方式见 [SDK 开发指南](https://github.com/Mai-with-u/maibot-plugin-sdk/blob/main/docs/guide.md)。

## 注册

在 `_manifest.json` 的顶层 `llm_providers` 数组中加入 `{"client_type": "example.media"}`。在插件方法的装饰器中使用相同的类型名，并通过 `formats` 描述要开放的能力。各项均可省略。

```python
@LLMProvider(
    "example.media",
    formats={
        "asr": {"transcribe-v1": {"label": "示例转写"}},
        "tts": {
            "speech-v1": {
                "label": "示例朗读",
                "voices": {"spring": "春日", "autumn": "秋日"},
            },
        },
        "web": ["web_search", "web_fetch"],
    },
)
async def handle_media(self, operation, request):
    # 此处的 handlers 由插件实现，键为下表中的操作名。
    return await self.handlers[operation](request)
```

`asr` 和 `tts` 的字典键是实际模型标识符，`label` 显示在模型库。音色映射按声明顺序展示，第一个为默认音色。固定音色模型可省略 `voices`，此时传入的 `voice_id` 是空字符串。仅实现搜索或读取的服务在 `web` 中列出对应的一项即可。

## 请求与返回

每次调用都会收到用户为当前服务配置的 `request["api_provider"]`，可从中读取地址、鉴权和超时。插件应让上游错误抛出，日志及错误消息中应省略密钥。

| operation | 主要请求字段 | 返回示例 |
| --- | --- | --- |
| `audio_transcription` | `audio_base64`、`model_info.model_identifier`、`extra_params` | `{"content": "转写文字"}` |
| `audio_synthesis` | `text`、`model_identifier`、`voice_id` | `{"audio_base64": "...", "media_type": "audio/mpeg"}` |
| `web_search` | `query`、`max_results` | `{"results": [{"url": "https://example.org/", "title": "标题", "snippet": "摘要"}]}` |
| `web_fetch` | `url` | `{"url": "https://example.org/", "title": "标题", "content": "网页正文"}` |

音频使用标准 Base64 编码的完整文件，`media_type` 可选 `audio/wav`、`audio/mpeg`、`audio/ogg`、`audio/flac`、`audio/mp4` 或 `audio/webm`。音频编码后连同 RPC 信封须在 32 MiB 帧限制内。搜索结果可附带字符串 `summary` 和 `published_at`。网页链接使用 HTTP(S)。

## 在 WebUI 中使用

- **语音**　在模型库添加语音识别或语音合成模型。选好插件声明的语音类型后，复用已有提供商，或选「填写其他连接信息」创建提供商。接着选择具体模型，TTS 还可选音色并试听。最后确认名称，保存后在「分工」中选用。
- **联网**　在联网服务的「服务商」下拉框选择插件条目。这个服务独立保存地址、密钥和超时，插件自行管理连接时可留空地址与密钥。保存后在「联网搜索」分工选用该服务。

插件重载后重新打开配置表单即可看到最新声明。卸载会注销协议，已保存的配置继续保留，重新加载相同插件和协议后可继续使用。
