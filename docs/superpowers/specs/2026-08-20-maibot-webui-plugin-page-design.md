# MaiBot WebUI 插件页面注入设计规范

状态：Phase 0 已确认
日期：2026-08-20
适用版本：MaiBot 控制台 V1.2.1、WebUI v1.7.1

## 1. 背景与目标

MaiBot 已经具备插件目录扫描、Manifest 校验、插件 Runner 隔离、FastAPI WebUI 以及插件 API RPC 能力。本设计在这些边界上增加 WebUI 页面声明能力，使已安装插件可以在“扩展与集成”分组下显示页面入口，并在受控生命周期内加载插件前端模块和后端 API。

本设计优先保证：

1. 不改变现有插件管理、插件市场和 MCP 设置路由。
2. 不绕过现有 Runner 进程隔离。
3. 旧插件没有 `extensions` 字段时行为完全不变。
4. 页面资源和页面 API 都经过 WebUI Host 的认证、路径和权限校验。

## 2. 技术边界

### 2.1 前端边界

Dashboard 使用 React 19、Vite 和 TanStack Router。页面注入协议不绑定 Vue 或 React 组件类型，而是绑定浏览器容器生命周期：

```text
Manifest -> Host 页面列表 -> 固定宿主路由 -> ESM mount(container, context)
```

TanStack Router 的路由树在构建时生成。MVP 不进行运行时路由树重建，也不提供 Vue Router 风格的 `router.addRoute()`。

### 2.2 后端边界

插件由独立 Runner 进程加载，WebUI FastAPI 应用运行在独立的 WebUI 服务中。因此插件不能在 MVP 中直接接收 FastAPI `app` 并执行 `register(app)`。页面后端接口通过 Host 路由转发到现有插件 API Registry 和 `PluginRunnerSupervisor.invoke_api()`。

### 2.3 信任边界

MVP 只加载以下插件的页面：

- 已安装在本机插件目录中的插件；
- 插件已启用且 Runner 加载成功；
- 页面入口位于插件目录内的 `webui/dist/`；
- 页面声明不包含远程 HTTP(S) 资源入口。

同源 ESM 模块不是安全沙箱。可信插件页面可以访问当前页面 DOM 和同源 WebUI API，因此插件市场中的不可信代码不应直接采用同源 ESM 方式加载。后续如需支持不可信插件，应使用独立来源的 sandbox iframe 或签名验证机制。

## 3. Manifest 契约

### 3.1 结构

继续使用插件现有的 `_manifest.json` 和 `manifest_version: 2`。在严格的 `PluginManifest` 模型中增加一个可选字段：

```json
{
  "manifest_version": 2,
  "id": "example.hello-world",
  "version": "1.0.0",
  "name": "Hello World",
  "description": "WebUI 页面示例",
  "capabilities": ["webui.page", "webui.api"],
  "extensions": {
    "webui_pages": [
      {
        "id": "hello",
        "title": "Hello World",
        "route": "hello",
        "entry": "webui/dist/index.js",
        "component": "mount",
        "icon": "puzzle",
        "order": 100,
        "permissions": ["webui.page:view", "webui.api:invoke"],
        "api": {
          "get_status": "webui.hello.get_status"
        }
      }
    ]
  }
}
```

没有 `extensions` 的旧 Manifest 继续解析为无页面插件。

### 3.2 字段定义

| 字段 | 类型 | 约束 |
|---|---|---|
| `extensions` | object，可选 | 只允许 `webui_pages`，禁止未知字段 |
| `webui_pages` | array | 页面数量设置上限，建议 MVP 为 32 |
| `id` | string | 插件内唯一；`[a-z0-9][a-z0-9_-]{0,63}` |
| `title` | string | 1 至 80 个字符；按纯文本显示，不使用 HTML |
| `route` | string | 单个相对 slug；不得包含 `/`、`\\`、`..` 或控制字符 |
| `entry` | string | 相对插件根目录，必须位于 `webui/dist/`，仅允许 `.js` 或 `.mjs` |
| `component` | string | ESM 导出名称；MVP 固定为 `mount` |
| `icon` | string，可选 | Host 图标白名单中的名称，未知值回退为通用插件图标 |
| `order` | integer，可选 | 默认 0；同组按 `order`、插件 ID、页面 ID 稳定排序 |
| `permissions` | array[string] | 页面所需能力；不能直接授予用户管理员权限 |
| `api` | object，可选 | `operation -> 已注册插件 API 组件名` 的白名单 |

Manifest 仍采用现有严格模型的 `extra="forbid"` 策略。`extensions` 或其页面字段校验失败时，按现有 Manifest 校验规则记录错误并拒绝加载该插件，避免不完整或不安全的页面声明进入运行时。

### 3.3 Host 生成字段

插件不能声明 Host 生成的绝对 URL。页面清单 API 返回以下由 Host 计算的字段：

```json
{
  "plugin_id": "example.hello-world",
  "page_id": "hello",
  "title": "Hello World",
  "route": "/plugin-pages/example.hello-world/hello",
  "entry": "/api/webui/plugins/example.hello-world/assets/webui/dist/index.js?v=1.0.0",
  "component": "mount",
  "api_base": "/api/webui/plugins/example.hello-world/pages/hello/api",
  "permissions": ["webui.page:view", "webui.api:invoke"]
}
```

`plugin_id` 和 `page_id` 进入 URL 前必须经过 Host 侧校验和 URL 编码。`entry` 的版本参数用于插件重载后的缓存失效；不得把用户提供的任意 URL 原样返回给前端。

## 4. 前端 ABI

### 4.1 模块导出

页面入口必须导出 `component` 指定的函数。MVP 的默认函数名为 `mount`：

```ts
export type PluginPageModule = {
  mount: (
    container: HTMLElement,
    context: PluginPageContext
  ) => void | (() => void)
}
```

`mount` 返回清理函数时，Host 在离开路由、插件重载或页面错误时调用该函数。插件不得依赖 Host 直接调用 React/Vue 组件构造函数。

### 4.2 上下文

```ts
export type PluginPageContext = {
  pluginId: string
  pageId: string
  hostVersion: string
  apiBase: string
  assetsBase: string
  request<T>(
    operation: string,
    options?: {
      method?: 'POST'
      body?: unknown
      signal?: AbortSignal
    }
  ): Promise<T>
}
```

`request()` 只能调用 Manifest `api` 白名单中的操作。Host 不向插件暴露 `maibot_session`、后端访问令牌或任意 API 客户端实例。

页面 bundle 应自包含其运行时依赖，或遵守后续明确的共享依赖 ABI。MVP 不承诺远程 bundle 可以直接导入 Host 的 React/Vue 包。

## 5. HTTP 契约

### 5.1 页面清单

```text
GET /api/webui/plugins/pages
```

要求有效 `maibot_session` Cookie。只返回已安装、已启用、Runner 加载成功且页面声明有效的插件页面。响应结构：

```json
{
  "success": true,
  "pages": [],
  "warnings": []
}
```

页面列表为空是合法响应，不应影响现有插件管理页面。

### 5.2 页面资源

```text
GET /api/webui/plugins/{plugin_id}/assets/{asset_path:path}
```

资源路由必须：

1. 使用 `require_auth`；
2. 只允许读取对应插件 `webui/dist/` 下的文件；
3. 拒绝绝对路径、`..`、符号链接和越界解析结果；
4. 只返回允许的静态 MIME 类型；
5. 不把整个插件目录作为无鉴权 `StaticFiles` 目录暴露。

### 5.3 页面 API

```text
POST /api/webui/plugins/{plugin_id}/pages/{page_id}/api/{operation}
```

Host 先根据 `plugin_id/page_id` 查找页面，再从页面的 `api` 映射中解析 `operation`。未经白名单声明的操作返回 404 或 403，不允许插件页面把任意字符串直接转换成 RPC 组件名。

请求体使用 JSON，MVP 设置请求体上限、RPC 超时和每页面频率限制。插件返回值必须是 JSON 可序列化数据；RPC 错误转换为统一 WebUI 错误格式，不泄漏 Runner 内部堆栈。

## 6. 页面生命周期

```text
WebUI 启动或插件重载
  -> Host 读取已加载插件路径
  -> 解析并校验 extensions.webui_pages
  -> 注册页面清单缓存
  -> GET /pages 返回清单
  -> useMenuSections 合并扩展菜单项
  -> 固定宿主路由匹配 pluginId/pageId
  -> 鉴权后获取 ESM entry
  -> 调用 mount(container, context)
  -> 路由离开时执行清理函数
```

页面注册表由 WebUI Host 持有，不在 Runner 中复制一份。插件重载或禁用时，注册表必须失效对应页面缓存；已经打开的页面显示错误状态并执行清理。

## 7. 前端路由和菜单策略

### 7.1 固定宿主路由

增加一个静态受保护路由：

```text
/plugin-pages/$pluginId/$pageId
```

该路由只负责页面容器、加载状态、错误边界和生命周期管理。插件的 `route` 是相对 slug，不能改变 TanStack Router 的路由树，也不能覆盖现有 `/plugins`、`/plugin-config` 等路径。

### 7.2 动态菜单

`useMenuSections()` 获取页面清单后，把页面映射为普通 `MenuItem` 并追加到 `extensionsMonitor` 分组。动态标题使用纯文本字段，不作为 i18n key 解析。图标使用 Host 白名单组件，未知图标回退为通用插件图标。

动态菜单获取失败时保留静态菜单，并在页面内部记录可诊断错误；不能因为页面清单 API 暂时不可用而隐藏插件管理、插件市场或 MCP 设置。

## 8. 安全和兼容性

### 8.1 页面代码信任

浏览器 CSP 继续限制脚本来源为 Host 本地来源和受信任的 `app:` 来源，不允许通过 Manifest 任意扩大 `script-src`、`connect-src` 或启用 `unsafe-eval`。在 Electron 中，插件资源需要通过显式的 `app://` 资源协议或受控后端代理提供。

### 8.2 权限

`capabilities` 表示插件申请的 Host 能力，`permissions` 表示页面访问这些能力的范围。MVP 没有独立用户 RBAC，因此不能把 `permissions` 解释成管理员授权；后端仍必须在每个页面 API 请求中执行认证、页面存在性、操作白名单和插件状态校验。

### 8.3 版本

页面模块通过 `hostVersion` 获得 Host 版本。插件应通过现有 `host_application` 兼容区间声明最低版本。若未来修改 `PluginPageContext` 或 `mount` ABI，应增加 `webui_api_version`，而不是静默改变字段含义。

### 8.4 旧插件兼容

没有 `extensions` 字段的插件不生成任何页面，现有插件 API 和插件管理接口不变。新增页面字段属于 Manifest v2 的可选扩展，不需要修改旧插件目录结构。

## 9. 后续实现映射

Phase 1：

- 扩展 `src/plugin_runtime/runner/manifest_validator.py` 的 Pydantic 模型；
- 新增 WebUI 页面注册表和页面清单/资源 Router；
- 复用现有插件路径和安全路径解析函数；
- 为 Manifest、页面清单、资源越界和重复页面 ID 添加测试。

Phase 2：

- 在 `dashboard/src/router.tsx` 增加固定宿主路由；
- 扩展 `MenuItem` 类型和 `useMenuSections()`；
- 实现 ESM 加载、`mount` 生命周期、加载状态和错误边界。

Phase 3：

- 接入 `PluginRunnerSupervisor.invoke_api()`；
- 实现页面 API 白名单、超时、请求体限制和错误转换；
- 补充跨进程集成测试。

Phase 4：

- 提供 Hello World 插件模板；
- 覆盖浏览器 WebUI 和 Electron 资源加载路径；
- 编写插件开发者文档和变更记录。

## 10. Phase 0 验收标准

- [x] 已确认前端技术栈为 React 19 + TanStack Router，而非 Vue 3。
- [x] 已确认页面 API 采用 `/api/webui/plugins` 命名空间。
- [x] 已确认插件 Runner 与 WebUI FastAPI 的进程/线程边界。
- [x] 已冻结 `extensions.webui_pages` Manifest 结构和校验规则。
- [x] 已冻结 `mount(container, context)` 前端 ABI。
- [x] 已冻结页面清单、资源和 API 代理的 URL 契约。
- [x] 已冻结 MVP 信任模型和非目标范围。
- [ ] Phase 1 实现和测试尚未开始。
