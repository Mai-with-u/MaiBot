# 人设导入协议 v1

WebUI「身份与人格」→「导入」支持粘贴以下 JSON，也支持用 Markdown 的 json 代码块包裹。粘贴后自动校验并预览，点击「继续编辑」可调整所有字段，最后点击「创建人设」保存到人设库。导入不会自动切换当前人设，同名人设需修改名称后再保存。

```json
{
  "format": "maibot.personality",
  "version": 1,
  "profile": {
    "name": "温柔的麦麦",
    "personality": "温柔、好奇，喜欢和朋友分享日常。",
    "behavior_style": "自然参与聊天，认真倾听，不强行找话题。",
    "reply_style": "简短自然，语气温和。",
    "group_chat_prompt": "留意群友之间的互动，适时参与，不必逐条回复。",
    "private_chat_prompts": "关注对方的想法，保持自然连贯的一对一交流。",
    "multiple_reply_style": ["偶尔用一句俏皮话回复"],
    "multiple_probability": 0.1
  }
}
```

`format` 必须为 `maibot.personality`，`version` 必须为数字 `1`。`profile` 的前六个字段（含群聊与私聊提示词）为必填字符串，名称不能全为空白。`multiple_reply_style` 是可选字符串数组，默认 `[]`；`multiple_probability` 是可选的 0–1 数字，默认 `0`。不接受额外字段、自动类型转换或远程资源。单份文本最多 65536 个 JavaScript 字符（UTF-16 code units）。

## 外部网页唤起接口

使用用户的 WebUI 地址打开以下链接：

```text
{WEBUI_ORIGIN}/config/bot?tab=bot#personality-import={URL编码后的完整JSON}
```

这是浏览器导航接口，不是写入配置的 HTTP API。不需要发送认证令牌、调用跨域接口或配置 CORS。JSON 放在 fragment 中，不随 HTTP 请求发给 WebUI 服务器。用户仍需正常登录 WebUI；同一标签页会临时保存待导入内容，登录后回到导入卡片。取消或保存后清除待导入内容。

外部站点可以让用户填写自己的实例地址，然后用以下代码生成按钮链接：

```js
function personalityImportUrl(webuiOrigin, document) {
  const base = new URL(webuiOrigin);
  if (!['http:', 'https:'].includes(base.protocol)) {
    throw new Error('WebUI 地址必须是 HTTP 或 HTTPS');
  }
  const url = new URL('/config/bot?tab=bot', base.origin);
  url.hash = new URLSearchParams({
    'personality-import': JSON.stringify(document),
  }).toString();
  return url.href;
}

// document 为上面的完整 JSON 对象；实例地址由用户填写。
link.href = personalityImportUrl(webuiOrigin, document);
link.target = '_blank';
link.rel = 'noopener noreferrer';
link.textContent = '导入到 MaiBot';
```

URL 的可用长度还受浏览器和外部平台限制，长人设建议使用复制 JSON 的方式导入。此接口面向可访问的 WebUI HTTP(S) 实例，不负责启动尚未运行的 MaiBot 或桌面客户端。
