# MaiBot WebUI Plugin Pages Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 React 19 + TanStack Router Dashboard 中接入 Phase 1 页面清单，让已加载插件页面以动态菜单和固定宿主路由呈现。

**Architecture:** Dashboard 保留静态 TanStack Router 路由树，只增加受保护的 `/plugin-pages/$pluginId/$pageId` 宿主路由。菜单 Hook 通过现有 `backendApi` 获取 Host 生成的页面清单，将页面标题作为纯文本项追加到“扩展与集成”分组；宿主组件按清单入口执行受控 ESM `import()`，校验 `mount` 导出并在卸载时调用清理函数。

**Tech Stack:** React 19、TypeScript 5.9、TanStack Router、Vite、Vitest、Testing Library。

**Spec:** `docs/superpowers/specs/2026-08-20-maibot-webui-plugin-page-design.md`

## Global Constraints

- 前端实际技术栈是 React 19 + TanStack Router，不引入 Vue Router 或动态路由树重建。
- 页面入口只能使用后端 Host 生成的同源 URL，禁止 Manifest 远程 URL 和前端自行拼接插件目录路径。
- 认证请求统一使用现有 `backendApi`；插件上下文不能暴露认证 Cookie、令牌或任意 API 客户端实例。
- 动态菜单请求失败时保留所有静态菜单项，不能影响插件管理、插件市场和 MCP 设置。
- 页面标题按纯文本渲染，不作为 i18n key 解析；未知图标回退为通用插件图标。
- 新增复杂函数保留完整 TypeScript 类型注解，并在生命周期和错误处理逻辑前添加简短中文注释。
- 所有新增行为必须先写失败测试，确认失败后再写生产代码。

---

### Task 1: Define page API types and route-list client

**Files:**
- Modify: `dashboard/src/lib/plugin-api/types.ts`
- Create: `dashboard/src/lib/plugin-api/pages.ts`
- Test: `dashboard/src/lib/plugin-api/pages.test.ts`

**Interfaces:**
- `PluginPageSummary`: Host 返回的 `plugin_id/page_id/title/route/entry/component/icon/order/permissions/api_base` 字段。
- `PluginPagesResponse`: `{ success: boolean; pages: PluginPageSummary[]; warnings: string[] }`。
- `fetchPluginPages(signal?: AbortSignal): Promise<PluginPagesResponse>` 使用 `backendApi.get('/api/webui/plugins/pages', { signal })`，并通过 `requireSuccess` 校验业务包络。

- [x] **Step 1: Write the failing test**

```ts
it('通过 backendApi 获取并校验插件页面清单', async () => {
  vi.mocked(backendApi.get).mockResolvedValue({
    success: true,
    pages: [{
      plugin_id: 'example.first', page_id: 'hello', title: 'Hello',
      route: '/plugin-pages/example.first/hello',
      entry: '/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0',
      component: 'mount', icon: null, order: 0, permissions: [], api_base: '/api/webui/plugins/example.first/pages/hello/api',
    }],
    warnings: [],
  })

  await expect(fetchPluginPages()).resolves.toMatchObject({ pages: [{ page_id: 'hello' }] })
  expect(backendApi.get).toHaveBeenCalledWith('/api/webui/plugins/pages', { signal: undefined })
})

it('业务失败包络会被转换为 ApiError', async () => {
  vi.mocked(backendApi.get).mockResolvedValue({ success: false, pages: [], warnings: [], message: '不可用' })
  await expect(fetchPluginPages()).rejects.toThrow('不可用')
})
```

- [x] **Step 2: Run test to verify it fails**

Run: `npm --prefix dashboard run test:run -- src/lib/plugin-api/pages.test.ts`

Expected: FAIL because the page types, client function, and test module do not yet exist.

- [x] **Step 3: Write minimal implementation**

Add the response types beside the existing plugin API types, then implement the client with the existing HTTP/envelope modules:

```ts
export async function fetchPluginPages(signal?: AbortSignal): Promise<PluginPagesResponse> {
  const response = await backendApi.get<PluginPagesResponse>('/api/webui/plugins/pages', { signal })
  return requireSuccess(response, '加载插件页面失败')
}
```

- [x] **Step 4: Run test to verify it passes**

Run: `npm --prefix dashboard run test:run -- src/lib/plugin-api/pages.test.ts`

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add dashboard/src/lib/plugin-api/types.ts dashboard/src/lib/plugin-api/pages.ts dashboard/src/lib/plugin-api/pages.test.ts
git commit -m "feat: add dashboard plugin page client"
```

### Task 2: Merge dynamic pages into the Extensions menu

**Files:**
- Modify: `dashboard/src/components/layout/types.ts`
- Modify: `dashboard/src/components/layout/NavItem.tsx`
- Modify: `dashboard/src/components/layout/use-menu-sections.ts`
- Test: `dashboard/src/components/layout/use-menu-sections.test.ts`
- Test: `dashboard/src/components/layout/NavItem.test.tsx`

**Interfaces:**
- Extend `MenuItem` with `labelMode?: 'i18n' | 'text'` and `icon` remains a Host-owned `MenuIcon`.
- Add pure helpers `appendPluginPages(sections: MenuSection[], pages: PluginPageSummary[]): MenuSection[]` and `filterMenuSections(...)` behavior so tests can prove stable ordering and static fallback.

- [x] **Step 1: Write the failing test**

Add tests that resolve a page response and assert:

```ts
expect(flattenPaths(result.current)).toContain('/plugin-pages/example.first/hello')
expect(result.current.find((s) => s.title === 'sidebar.groups.extensionsMonitor')?.items).toEqual(
  expect.arrayContaining([expect.objectContaining({ label: 'Hello', labelMode: 'text', icon: expect.any(Function) })])
)
```

Add an error test where `fetchPluginPages` rejects and assert that `/plugin-config`, `/plugins`, and `/mcp-settings` still exist and the plugin page path does not. Add a `NavItem` test showing `labelMode: 'text'` renders the literal title without calling `t`.

- [x] **Step 2: Run test to verify it fails**

Run: `npm --prefix dashboard run test:run -- src/components/layout/use-menu-sections.test.ts src/components/layout/NavItem.test.tsx`

Expected: FAIL because the Hook does not fetch plugin pages, `MenuItem` has no label mode, and `NavItem` always translates labels.

- [x] **Step 3: Write minimal implementation**

Fetch plugin pages in the same cancellable effect as feature flags (using an `AbortController`), catch errors without replacing static sections, map page summaries to `/plugin-pages/...` menu items, and insert them only into `sidebar.groups.extensionsMonitor` sorted by `order/plugin_id/page_id`. Use `Puzzle` as the fallback icon. In `NavItem`, render `item.label` directly when `labelMode === 'text'`, otherwise call `t(item.label)`.

- [x] **Step 4: Run test to verify it passes**

Run: `npm --prefix dashboard run test:run -- src/components/layout/use-menu-sections.test.ts src/components/layout/NavItem.test.tsx`

Expected: PASS with existing menu tests unchanged.

- [x] **Step 5: Commit**

```bash
git add dashboard/src/components/layout/types.ts dashboard/src/components/layout/NavItem.tsx dashboard/src/components/layout/use-menu-sections.ts dashboard/src/components/layout/use-menu-sections.test.ts dashboard/src/components/layout/NavItem.test.tsx
git commit -m "feat: show plugin pages in extensions menu"
```

### Task 3: Add the fixed plugin page host route

**Files:**
- Create: `dashboard/src/routes/plugin-pages/PluginPageHost.tsx`
- Create: `dashboard/src/routes/plugin-pages/PluginPageHost.test.tsx`
- Modify: `dashboard/src/router.tsx`
- Modify: `dashboard/src/__tests__/router.test.tsx`

**Interfaces:**
- `PluginPageModule`: `{ [component: string]: unknown; mount?: (container: HTMLElement, context: PluginPageContext) => void | (() => void) }`.
- `PluginPageContext`: `pluginId`, `pageId`, `hostVersion`, `apiBase`, `assetsBase`, and `request<T>(operation, options?)`.
- Host uses `useParams({ from: '/plugin-pages/$pluginId/$pageId' })`, `fetchPluginPages`, and `backendApi.post` for context requests.

- [x] **Step 1: Write the failing test**

Write component tests with a mocked page list and dynamic import boundary:

```ts
it('加载入口并调用 mount，离开时调用清理函数', async () => {
  // import() is replaced by a test-only loader seam that returns mount and cleanup
  const cleanup = vi.fn()
  const mount = vi.fn(() => cleanup)
  ...
  expect(mount).toHaveBeenCalledWith(expect.any(HTMLElement), expect.objectContaining({ pluginId: 'example.first' }))
  unmount()
  expect(cleanup).toHaveBeenCalledOnce()
})

it('入口加载失败时显示可诊断错误而不是空白页面', async () => { ... })
```

The router test must add `/plugin-pages/$pluginId/$pageId` to `expectedPaths` and assert its component is registered under the protected route.

- [x] **Step 2: Run test to verify it fails**

Run: `npm --prefix dashboard run test:run -- src/routes/plugin-pages/PluginPageHost.test.tsx src/__tests__/router.test.tsx`

Expected: FAIL because the host component and route are absent.

- [x] **Step 3: Write minimal implementation**

Implement the lifecycle with an injectable `loadPluginPageModule` helper so tests can use a real module seam while production uses `import(/* @vite-ignore */ entry)`. The host must:

1. Abort stale page-list requests on unmount.
2. Display loading, missing-page, module-error, and invalid-export states.
3. Set up a dedicated container and call `mount(container, context)` exactly once per entry.
4. Call a returned cleanup function on dependency change and unmount, guarding against duplicate cleanup.
5. Build `request` through `backendApi.post` with an encoded operation and the plugin page `apiBase`; the plugin receives no client or credentials object.

Add the protected TanStack route:

```ts
const pluginPageHostRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugin-pages/$pluginId/$pageId',
  component: lazyRouteComponent(() => import('./routes/plugin-pages/PluginPageHost'), 'PluginPageHost'),
})
```

- [x] **Step 4: Run test to verify it passes**

Run: `npm --prefix dashboard run test:run -- src/routes/plugin-pages/PluginPageHost.test.tsx src/__tests__/router.test.tsx`

Expected: PASS, including existing route/auth tests.

- [x] **Step 5: Commit**

```bash
git add dashboard/src/routes/plugin-pages/PluginPageHost.tsx dashboard/src/routes/plugin-pages/PluginPageHost.test.tsx dashboard/src/router.tsx dashboard/src/__tests__/router.test.tsx
git commit -m "feat: add plugin page host route"
```

### Task 4: Phase 2 verification and integration checks

**Files:**
- Modify only files required by failing verification; do not include `.omo/`.

- [x] **Step 1: Run focused Dashboard tests**

Run: `npm --prefix dashboard run test:run -- src/lib/plugin-api/pages.test.ts src/components/layout/use-menu-sections.test.ts src/components/layout/NavItem.test.tsx src/routes/plugin-pages/PluginPageHost.test.tsx src/__tests__/router.test.tsx`

- [x] **Step 2: Run TypeScript checks**

Run: `npm --prefix dashboard run typecheck`

- [x] **Step 3: Build the Dashboard**

Run: `npm --prefix dashboard run build`

The build is required because Vite dynamic import handling and the static route tree are part of this phase's behavior.

- [x] **Step 4: Re-run Phase 1 Python tests**

Run with the frozen environment: `uv run --frozen python -m pytest pytests/plugin_runtime/test_manifest_webui_pages.py pytests/plugin_runtime/test_manifest_validator_logging.py pytests/plugin_runtime/test_manifest_version_compatibility.py pytests/webui/test_plugin_page_routes.py pytests/webui/test_app.py -q`

Record the existing duplicate `platform_io` collection mismatch and unrelated legacy install-fixture failures separately; do not hide them as Phase 2 regressions.

- [x] **Step 5: Commit verification-only fixes and report status**

Use `git diff --check` and `git status --short --branch`. Do not create or push a PR in this phase; wait until all phases and the final verification review are complete.

## Self-review checklist

- The dynamic menu never removes static extension entries when the page API is unavailable.
- Titles are rendered as text and cannot be interpreted as translation keys or HTML.
- The host route is protected by the existing `protectedRoute` layout and never changes the route tree at runtime.
- Dynamic modules are loaded only from Host-generated same-origin entries and receive only the narrow context ABI.
- Cleanup is idempotent across navigation, stale requests, module errors, and unmount.
- Phase 1 route and manifest tests remain green after the Dashboard changes.
