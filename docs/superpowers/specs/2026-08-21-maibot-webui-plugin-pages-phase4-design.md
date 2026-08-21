# MaiBot WebUI 插件页面 Phase 4 设计规格

## 目标

让插件安装、更新、启用和卸载操作与 WebUI 页面清单保持同步，使带有 `extensions.webui_pages` 声明且成功加载的插件无需重启 MaiBot 即可出现在“扩展与集成”菜单中。

## 当前架构约束

- 页面发现只读取 Runner 当前成功加载的插件目录；磁盘中存在插件目录不等于页面已可用。
- Dashboard 使用固定的 `/plugin-pages/$pluginId/$pageId` 宿主路由，插件入口通过 Host 清单加载，不把插件路径编译进 Dashboard。
- 插件页面资源和 API 已由 Host 同源代理，现有认证、路径校验、API 白名单和运行时 RPC 机制继续复用。
- 插件管理接口已有安装、更新、卸载互斥锁和运行时重载桥接，Phase 4 不改变原有插件市场和插件配置接口的响应语义。

## 方案

### 后端运行时同步

新增管理路由内部辅助函数，在安装或更新完成 Manifest 校验后调用 `PluginRuntimeManager.load_plugin_globally(plugin_id, reason)`；该调用通过 `run_on_main_loop` 执行，以兼容 WebUI 与 Runner 不同事件循环。响应增加 `runtime_loaded` 和可选 `runtime_warning` 字段，安装文件成功落盘但运行时加载失败时保留安装结果并清楚提示用户。

卸载流程继续先禁用并调用 `reload_plugins_globally(..., reason="uninstall")`，删除文件前清理运行时。启用/禁用流程继续使用已有配置热更新等待逻辑，成功后页面清单自然随运行时状态变化。

### Dashboard 页面清单刷新

在插件管理 API 客户端中定义 `PLUGIN_PAGES_UPDATED_EVENT` 和通知函数。安装、更新、卸载及启停成功后派发该同源窗口事件。`useMenuSections` 监听事件并重新请求页面清单；请求使用新的 AbortController，旧请求完成后不会覆盖新状态。静态菜单始终来自原始常量，事件失败只保留静态入口。

页面宿主保持现有清理函数语义。页面被卸载或清单不再包含时不强制跳转，用户再次点击菜单时由宿主显示清晰的页面不存在错误。

### 热重载边界

本阶段的热重载是 Runner 插件实例和 Host 页面清单热同步，不重启主进程。新安装/更新的插件在管理请求返回前尝试加载；前端清单事件使已打开 Dashboard 的菜单自动刷新。浏览器已经执行过的 ESM 模块不强制替换，避免破坏页面内部状态；重新进入页面时重新请求带版本参数的入口资源。

### SDK 兼容策略

MaiBot Host 负责扫描和安全校验，不把 Host 路由逻辑迁移到 SDK。SDK 仓库后续可增加 `WebUIPage` 类型、Manifest 示例和页面挂载上下文类型，但本阶段不直接修改或推送远程 SDK 仓库。MaiBot 内提供兼容模板和契约文档，插件可在没有新版 SDK 的情况下使用页面声明。

## 错误处理

- 运行时加载失败不伪装成页面可用；API 返回安装成功但包含 `runtime_loaded: false` 和脱敏告警。
- 页面清单请求失败不影响“插件管理”“插件市场”“MCP 设置”等静态入口。
- 事件监听器在组件卸载时移除，所有异步请求支持 AbortSignal。

## 验证

- Python：安装、更新、卸载运行时同步的单元/路由测试，覆盖加载成功、加载失败和非 Git 更新路径。
- TypeScript：安装流程事件通知、启停事件通知、菜单事件刷新和组件卸载测试。
- 构建：`npm --prefix dashboard run build`；静态检查：Ruff、`compileall`、`git diff --check`。
