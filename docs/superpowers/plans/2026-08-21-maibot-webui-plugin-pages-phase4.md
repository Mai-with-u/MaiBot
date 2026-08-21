# MaiBot WebUI Plugin Pages Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将插件管理生命周期与 WebUI 页面清单及运行时热加载连接起来，让安装插件后页面入口自动出现。

**Architecture:** 后端在安装/更新成功后复用现有 `PluginRuntimeManager.load_plugin_globally`，通过主运行时事件循环桥接并在响应中报告加载结果。Dashboard 以同源窗口事件通知页面清单变化，`useMenuSections` 重新拉取清单；固定插件页面 Host 路由和已有安全代理保持不变。

**Tech Stack:** FastAPI, Python 3.12, pytest, React 19, TypeScript, TanStack Router, Vitest, Vite。

## Global Constraints

- 简体中文优先，保留现有注释和类型注解。
- Python 参数化泛型使用 `typing` 模块类型。
- 不修改 `.omo/`、`resource.lock`、根目录 `.gitignore` 或真实 Bot 配置。
- 不直接修改或推送 `maibot-plugin-sdk` 外部仓库。
- 不创建 Pull Request；阶段完成前运行完整验证命令。
- WebUI 构建使用 `npm --prefix dashboard run build`，开发服务端口保持 7999。

### Task 1: 后端安装/更新运行时同步

**Files:**
- Modify: `src/webui/routers/plugin/management.py`
- Test: `pytests/webui/test_plugin_management_routes.py`

**Interfaces:**
- Produces `_sync_plugin_runtime(plugin_id: str, reason: str) -> bool` for install/update lifecycle use.
- Install/update response adds `runtime_loaded: bool` and only adds `runtime_warning: str` on a load failure.

- [ ] **Step 1: Write the failing tests**

Add tests that monkeypatch `get_plugin_runtime_manager` with a fake manager and assert install and both update paths call `load_plugin_globally` with the plugin ID and lifecycle reason. Assert a false return is reported as `runtime_loaded: false` without deleting the installed plugin.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `uv run pytest pytests/webui/test_plugin_management_routes.py -k "runtime or install_plugin or update_non_git" -q`

Expected: the new assertions fail because installation and update currently return without invoking the runtime loader.

- [ ] **Step 3: Implement the minimal synchronization helper**

Add a typed async helper beside `_release_plugin_runtime_before_delete` that imports `run_on_main_loop` and the runtime manager, calls `load_plugin_globally`, logs a warning on exception, and returns `False` for a failed load. Call it after Manifest validation in install, after `_update_non_git_plugin`, and after Git Manifest validation. Preserve existing progress and response fields.

- [ ] **Step 4: Run focused tests and verify they pass**

Run the same pytest command and confirm all selected tests pass.

### Task 2: Dashboard lifecycle event contract

**Files:**
- Create: `dashboard/src/lib/plugin-api/plugin-pages-events.ts`
- Modify: `dashboard/src/lib/plugin-api/install-flow.ts`
- Modify: `dashboard/src/lib/plugin-api/config.ts`
- Modify: `dashboard/src/lib/plugin-api/index.ts`
- Test: `dashboard/src/lib/plugin-api/install-flow.test.ts`
- Test: `dashboard/src/lib/plugin-api/config.test.ts`

**Interfaces:**
- Produces `PLUGIN_PAGES_UPDATED_EVENT: string` and `notifyPluginPagesUpdated(): void`.
- `installPlugin`, `updatePlugin`, `uninstallPlugin`, and `togglePlugin` notify only after a successful backend response.

- [ ] **Step 1: Write failing event tests**

Spy on `window.dispatchEvent`, call each lifecycle API with a successful mocked response, and assert one event with `PLUGIN_PAGES_UPDATED_EVENT`; assert rejected requests dispatch nothing.

- [ ] **Step 2: Run focused Vitest tests and verify they fail**

Run: `npm --prefix dashboard run test:run -- src/lib/plugin-api/install-flow.test.ts src/lib/plugin-api/config.test.ts`

Expected: event assertions fail because the API clients do not currently dispatch lifecycle events.

- [ ] **Step 3: Implement the event helper and notifications**

Create a browser-safe helper that returns without dispatching when `window` is unavailable. Wrap successful API responses in each lifecycle client and call the helper before returning the same response object.

- [ ] **Step 4: Run focused Vitest tests and verify they pass**

Run the same command and confirm all selected tests pass.

### Task 3: Menu refresh after lifecycle events

**Files:**
- Modify: `dashboard/src/components/layout/use-menu-sections.ts`
- Test: `dashboard/src/components/layout/use-menu-sections.test.ts`

**Interfaces:**
- Consumes `PLUGIN_PAGES_UPDATED_EVENT`.
- Keeps `fetchPluginPages(signal?: AbortSignal)` and the existing static menu output unchanged.

- [ ] **Step 1: Write failing refresh tests**

Render `useMenuSections`, resolve the initial empty page list, dispatch the lifecycle event, resolve a second list containing a page, and assert the route appears. Add an unmount case proving the event no longer causes a fetch.

- [ ] **Step 2: Run focused Vitest tests and verify they fail**

Run: `npm --prefix dashboard run test:run -- src/components/layout/use-menu-sections.test.ts`

Expected: the second fetch is never called and the dynamic route is absent.

- [ ] **Step 3: Implement event-driven refresh**

Extract a `refreshPluginPages` callback that creates a new AbortController per request, ignores stale/cancelled completions, and register/unregister it for the lifecycle event. Keep the initial fetch and feature flag listener behavior intact.

- [ ] **Step 4: Run focused Vitest tests and verify they pass**

Run the same command and confirm all selected tests pass.

### Task 4: Hello World page plugin template and compatibility docs

**Files:**
- Modify: `plugins/hello_world_plugin/_manifest.json`
- Create: `plugins/hello_world_plugin/webui/dist/index.js`
- Create: `plugins/hello_world_plugin/webui/README.md`
- Create: `docs/plugin-webui-pages.md`

**Interfaces:**
- Manifest declares page ID `hello`, route metadata, `entry`, mount export `mount`, and API operation `greet`.
- Bundle exports `mount(container, context)` and uses `context.request` for the isolated API endpoint.

- [ ] **Step 1: Add a manifest/template contract test**

Add or extend a Python manifest test to assert the example page declaration has a valid route-safe page ID, `webui/dist/index.js` exists, and the example bundle contains the `mount` export.

- [ ] **Step 2: Run the contract test and verify it fails**

Run: `uv run pytest pytests/plugin_runtime/test_manifest_webui_pages.py -q`

Expected: the current Hello World manifest has no `extensions.webui_pages` declaration and no bundle.

- [ ] **Step 3: Add the template and developer documentation**

Declare the page using the existing Manifest schema, add a small same-origin ESM bundle that renders plain DOM controls and calls `context.request('greet', ...)`, and document that Host discovery requires the plugin to be enabled and successfully loaded. Explain that the external SDK does not need a runtime change for this declaration and list a future SDK type/template update.

- [ ] **Step 4: Run the contract test and verify it passes**

Run the same pytest command and confirm it passes.

### Task 5: Full verification

**Files:**
- No new source files; review all Phase 4 changes and existing untracked files.

- [ ] **Step 1: Run backend tests**

Run: `uv run pytest pytests/plugin_runtime pytests/webui -q`

- [ ] **Step 2: Run Dashboard tests and type/build checks**

Run: `npm --prefix dashboard run test:run` and `npm --prefix dashboard run build`.

- [ ] **Step 3: Run Python/static checks**

Run: `uv run ruff check src pytests` followed by `uv run python -m compileall -q src pytests` and `git diff --check`.

- [ ] **Step 4: Review the diff and report manual test steps**

Confirm `.omo/` and `resource.lock` remain untouched, do not create a PR, and report the exact environment variable needed to test local Dashboard assets: `$env:MAIBOT_WEBUI_USE_LOCAL_DASHBOARD = "1"`.
