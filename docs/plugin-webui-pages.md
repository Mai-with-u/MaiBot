# 插件 WebUI 页面开发契约

## Manifest 声明

在 `_manifest.json` 中加入 `extensions.webui_pages`：

```json
{
  "extensions": {
    "webui_pages": [
      {
        "id": "settings",
        "title": "插件设置",
        "route": "settings",
        "entry": "webui/dist/index.js",
        "component": "mount",
        "permissions": ["webui.page:view"],
        "api": {
          "get_status": "example.settings.get_status"
        }
      }
    ]
  }
}
```

`route` 只能是单段相对路径，`entry` 必须位于插件的 `webui/dist` 目录内，`component` 是 ESM 模块导出的挂载函数名。Host 会校验路径、同源资源和 API 白名单；页面不会获得插件本地目录路径。

## 页面入口

页面 bundle 应导出：

```js
export function mount(container, context) {
  // 使用 container 渲染页面。
  // context.request(operation, { body }) 调用声明过的插件 API。
  return () => {
    // 移除事件监听器和其他资源。
  }
}
```

`context` 包含 `pluginId`、`pageId`、`hostVersion`、`apiBase`、`assetsBase` 和 `request`。入口通过 Host 同源 `/api/webui/plugins/{plugin_id}/assets/...` 提供，浏览器只允许加载当前 Host 的页面资源。

## 后端 API

插件用现有 SDK 的 `@API` 装饰器注册 API，Manifest 的 `api` 字段只允许引用该插件自身的 API。页面请求由 Host 认证、校验 JSON 大小和 API 白名单后转发到 Runner，不应在插件页面中绕过 Host。

普通请求 `context.request('operation')` 直接返回插件 API 的业务数据。需要排查跨层调用时可传入
`{ debug: true }`，此时返回 `{ data, request_id }`，其中 `request_id` 可用于关联 Host 日志。

## SDK 兼容说明

当前页面声明由 MaiBot Host 解析，插件不要求安装新版 SDK。`maibot-plugin-sdk` 后续适合增加 `WebUIPage`、`PluginPageContext` 类型和模板，但不应把 Host 的路由、认证或静态资源安全策略复制进 SDK。
