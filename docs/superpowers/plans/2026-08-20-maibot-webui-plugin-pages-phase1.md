# MaiBot WebUI Plugin Pages Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有插件管理接口的前提下，增加严格校验的 `extensions.webui_pages` Manifest、Host 页面注册表，以及受认证保护的页面清单和静态资源 API。

**Architecture:** Manifest 继续由 `ManifestValidator` 解析；WebUI Host 新增独立的页面注册表服务，将已加载插件目录中的页面声明转换为 Host 生成的 URL。新增 Router 挂载到已有 `/api/webui/plugins` 命名空间，资源通过鉴权的 `FileResponse` 返回，不使用无鉴权的 `StaticFiles` 挂载。

**Tech Stack:** Python 3.12、Pydantic 2、FastAPI、pytest、pytest-asyncio、uv。

**Spec:** `docs/superpowers/specs/2026-08-20-maibot-webui-plugin-page-design.md`

## Global Constraints

- 继续使用 `_manifest.json` 和 `manifest_version: 2`；`extensions` 是可选字段。
- 页面入口必须是插件目录内 `webui/dist/` 下的 `.js` 或 `.mjs` 文件。
- 页面 ID、route、entry、插件 ID 必须拒绝路径遍历、绝对路径、控制字符和符号链接越界。
- 所有页面清单和资源接口都必须依赖 `require_auth`。
- 不在 Phase 1 中执行插件 Python 代码或提供 `register(app)`。
- 保留现有插件管理、插件市场、MCP 和旧 Manifest 的行为。
- 新增复杂函数必须保留完整类型注解；复杂校验逻辑添加简洁中文注释。

## 文件结构

- Modify: `src/plugin_runtime/runner/manifest_validator.py` — 新增页面 Manifest 的严格 Pydantic 类型和字段校验。
- Create: `src/webui/services/plugin_page_registry.py` — 页面声明发现、路径安全检查、Host URL 生成和缓存无关的纯注册函数。
- Create: `src/webui/routers/plugin/pages.py` — 页面清单和资源 FastAPI Router。
- Modify: `src/webui/routers/plugin/__init__.py` — 注册 `pages_router`，不改现有子路由。
- Create: `pytests/plugin_runtime/test_manifest_webui_pages.py` — Manifest 页面字段和校验测试。
- Create: `pytests/webui/test_plugin_page_routes.py` — 页面注册表、鉴权、资源路径和响应契约测试。

### Task 1: Add strict Manifest page models

**Files:**
- Modify: `src/plugin_runtime/runner/manifest_validator.py`
- Test: `pytests/plugin_runtime/test_manifest_webui_pages.py`

**Interfaces:**
- Produces `ManifestWebUiPage`, `ManifestWebUiExtensions` and `PluginManifest.extensions: Optional[ManifestWebUiExtensions]`.
- `ManifestWebUiPage` fields are `id`, `title`, `route`, `entry`, `component`, `icon`, `order`, `permissions`, and `api`.
- `ManifestWebUiExtensions` has only `webui_pages` and uses `extra="forbid"`.

- [ ] **Step 1: Write the failing tests**

Add a complete valid Manifest fixture and test these behaviors:

```python
def test_manifest_without_webui_extensions_remains_valid():
    manifest = make_valid_manifest()
    parsed = ManifestValidator(validate_python_package_dependencies=False).parse_manifest(manifest)
    assert parsed is not None
    assert parsed.extensions is None


def test_manifest_parses_webui_page_declaration():
    manifest = make_valid_manifest(
        extensions={
            "webui_pages": [
                {
                    "id": "hello",
                    "title": "Hello World",
                    "route": "hello",
                    "entry": "webui/dist/index.js",
                    "component": "mount",
                    "permissions": ["webui.page:view"],
                    "api": {"get_status": "webui.hello.get_status"},
                }
            ]
        }
    )
    parsed = ManifestValidator(validate_python_package_dependencies=False).parse_manifest(manifest)
    assert parsed is not None
    assert parsed.extensions is not None
    assert parsed.extensions.webui_pages[0].entry == "webui/dist/index.js"


@pytest.mark.parametrize("route", ["../hello", "/hello", "hello/world", "hello\\world"])
def test_manifest_rejects_unsafe_page_route(route):
    manifest = make_valid_manifest(extensions={"webui_pages": [{**valid_page(), "route": route}]})
    validator = ManifestValidator(validate_python_package_dependencies=False)
    assert validator.parse_manifest(manifest) is None


def test_manifest_rejects_duplicate_page_ids():
    page = valid_page()
    manifest = make_valid_manifest(extensions={"webui_pages": [page, page]})
    validator = ManifestValidator(validate_python_package_dependencies=False)
    assert validator.parse_manifest(manifest) is None
```

The fixture must include the existing required Manifest v2 fields instead of weakening production validation.

- [ ] **Step 2: Run the tests and verify the expected failure**

Run:

```bash
uv run pytest pytests/plugin_runtime/test_manifest_webui_pages.py -q
```

Expected: collection succeeds, then the new model/field assertions fail because `PluginManifest` has no `extensions` field and unsafe declarations are not yet validated.

- [ ] **Step 3: Implement the minimal models and validators**

Add strict models next to the existing display/manifest models. Use `ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)` consistently. Add validators that:

1. Reject empty IDs and titles.
2. Accept only the documented page ID pattern.
3. Reject route strings containing `/`, `\\`, `..`, NUL, CR, LF, or tab.
4. Require `entry` to start with `webui/dist/`, reject absolute paths and `..`, and allow only `.js`/`.mjs` suffixes.
5. Reject duplicate page IDs within one plugin.
6. Normalize optional `component` to `mount`, `order` to a bounded integer, and optional collections to empty collections through Pydantic defaults.

Add `extensions: Optional[ManifestWebUiExtensions] = None` to `PluginManifest`. Keep existing comments and type annotations intact; add a short comment only before the path/route validation block because it combines URL and filesystem constraints.

- [ ] **Step 4: Run the focused tests and existing Manifest tests**

Run:

```bash
uv run pytest pytests/plugin_runtime/test_manifest_webui_pages.py pytests/plugin_runtime/test_manifest_validator_logging.py pytests/plugin_runtime/test_manifest_version_compatibility.py -q
```

Expected: all focused tests pass with no warnings introduced by the new optional field.

- [ ] **Step 5: Commit the Manifest contract**

```bash
git add src/plugin_runtime/runner/manifest_validator.py pytests/plugin_runtime/test_manifest_webui_pages.py
git commit -m "feat: validate plugin WebUI page manifests"
```

### Task 2: Build the Host page registry

**Files:**
- Create: `src/webui/services/plugin_page_registry.py`
- Test: `pytests/webui/test_plugin_page_routes.py`

**Interfaces:**
- Produces `PluginPageRecord` with `plugin_id`, `page_id`, `title`, `route`, `entry_url`, `component`, `icon`, `order`, `permissions`, `api_base`, `plugin_path`, and `entry_path`.
- Produces `discover_plugin_pages(plugin_paths: Iterable[Path], loaded_plugin_ids: Collection[str]) -> List[PluginPageRecord]`.
- Produces `get_plugin_page(plugin_id: str, page_id: str) -> PluginPageRecord` only after the Router wires a registry instance.

- [ ] **Step 1: Write failing discovery tests**

Use `tmp_path` to create two plugin directories with valid `_manifest.json` files and `webui/dist/index.js`. Assert that discovery:

```python
pages = discover_plugin_pages([first_plugin, second_plugin], {"example.first"})
assert [page.page_id for page in pages] == ["hello"]
assert pages[0].route == "/plugin-pages/example.first/hello"
assert pages[0].entry_url.endswith(
    "/api/webui/plugins/example.first/assets/webui/dist/index.js?v=1.0.0"
)
```

Also test that unloaded plugin IDs are omitted, missing entry files raise a clear `ValueError`, and a symlinked entry is rejected.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest pytests/webui/test_plugin_page_routes.py -q
```

Expected: import failure for the not-yet-created registry module or missing `discover_plugin_pages` symbol.

- [ ] **Step 3: Implement discovery and Host URL generation**

Implement the registry as a pure, dependency-injected function so tests do not start the full plugin runtime. Reuse `load_manifest_json`, `resolve_plugin_file_path`, and `validate_plugin_id` from `src.webui.routers.plugin.support` where possible. For each candidate:

1. Resolve the plugin root and reject a symlinked root.
2. Load `_manifest.json` and parse it through `ManifestValidator` with `require_entrypoint=False`.
3. Require the Manifest ID to be in `loaded_plugin_ids`.
4. Resolve each page entry with `allow_missing=False` and verify the final path remains below `webui/dist`.
5. Generate only Host-owned URLs; never concatenate a raw Manifest URL.
6. Return records sorted by `order`, plugin ID, and page ID.

Add a short Chinese comment before the entry-path resolution block explaining that Manifest validation and filesystem resolution are deliberately both performed because the first protects the contract and the second protects the actual filesystem.

- [ ] **Step 4: Run discovery tests and the existing plugin support tests**

```bash
uv run pytest pytests/webui/test_plugin_page_routes.py pytests/webui/test_plugin_management_routes.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the registry**

```bash
git add src/webui/services/plugin_page_registry.py pytests/webui/test_plugin_page_routes.py
git commit -m "feat: add plugin WebUI page registry"
```

### Task 3: Add authenticated page and asset routes

**Files:**
- Create: `src/webui/routers/plugin/pages.py`
- Modify: `src/webui/routers/plugin/__init__.py`
- Modify: `src/webui/routers/plugin/pages.py`
- Test: `pytests/webui/test_plugin_page_routes.py`

**Interfaces:**
- Adds `GET /pages` under the existing `/api/webui/plugins` prefix.
- Adds `GET /{plugin_id}/assets/{asset_path:path}` under the same prefix.
- Does not add arbitrary plugin FastAPI registration.

- [ ] **Step 1: Write failing route tests**

Create a small FastAPI app with the pages Router and override `require_auth`. Test:

```python
def test_page_list_requires_auth(client):
    assert client.get("/api/webui/plugins/pages").status_code == 401


def test_page_list_returns_host_generated_urls(authenticated_client):
    response = authenticated_client.get("/api/webui/plugins/pages")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["pages"][0]["route"] == "/plugin-pages/example.first/hello"


def test_asset_route_rejects_parent_path(authenticated_client):
    response = authenticated_client.get(
        "/api/webui/plugins/example.first/assets/../_manifest.json"
    )
    assert response.status_code in {400, 404}


def test_asset_route_returns_javascript(authenticated_client):
    response = authenticated_client.get(
        "/api/webui/plugins/example.first/assets/webui/dist/index.js"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
```

- [ ] **Step 2: Run the route tests and verify the expected failure**

```bash
uv run pytest pytests/webui/test_plugin_page_routes.py -q
```

Expected: route import or 404 failures because the Router is not registered.

- [ ] **Step 3: Implement the Router**

Use `APIRouter(tags=["插件页面"])` and `Depends(require_auth)` on each endpoint. The list endpoint serializes only public `PluginPageRecord` fields. The asset endpoint validates the plugin ID, looks up the page registry, restricts `asset_path` to the plugin `webui/dist` root, rejects symlinks, determines MIME type from a fixed allowlist, and returns `FileResponse`.

Do not add a catch-all route to `app.py`; the existing SPA catch-all must continue to return 404 for `/api/...` paths. Include the new Router in `src/webui/routers/plugin/__init__.py` alongside existing plugin routers.

- [ ] **Step 4: Run route and application regression tests**

```bash
uv run pytest pytests/webui/test_plugin_page_routes.py pytests/webui/test_app.py pytests/webui/test_plugin_management_routes.py -q
```

Expected: all tests pass and existing plugin management endpoints remain unchanged.

- [ ] **Step 5: Commit the routes**

```bash
git add src/webui/routers/plugin/pages.py src/webui/routers/plugin/__init__.py pytests/webui/test_plugin_page_routes.py
git commit -m "feat: expose authenticated plugin WebUI pages"
```

### Task 4: Wire runtime paths and perform Phase 1 verification

**Files:**
- Modify: `src/webui/routers/plugin/pages.py`
- Modify: `src/webui/services/plugin_page_registry.py`
- Test: `pytests/webui/test_plugin_page_routes.py`

**Interfaces:**
- The production Router obtains plugin paths and loaded IDs from the existing runtime manager, while tests continue to inject a registry.
- Runtime discovery failures are logged and omit only the invalid page; existing plugin management routes remain available.

- [ ] **Step 1: Write the runtime integration regression test**

Patch `get_plugin_runtime_manager()` with a small object exposing current plugin directories and load statuses. Assert that `/api/webui/plugins/pages` includes only status `success` plugins and does not scan unrelated directories.

- [ ] **Step 2: Run the regression test and verify it fails**

```bash
uv run pytest pytests/webui/test_plugin_page_routes.py::test_page_list_uses_loaded_runtime_plugins -q
```

Expected: the endpoint currently has no runtime-backed registry and returns an empty page list or raises an import error.

- [ ] **Step 3: Wire the existing runtime manager without importing plugin code**

Use the manager's existing plugin directory/status accessors. Do not call `create_plugin`, import `plugin.py`, or duplicate Runner startup. Refresh the registry per request for MVP; add a versioned cache only after measuring the cost.

- [ ] **Step 4: Run the complete Phase 1 verification set**

```bash
uv run pytest pytests/plugin_runtime/test_manifest_webui_pages.py pytests/plugin_runtime/test_manifest_validator_logging.py pytests/plugin_runtime/test_manifest_version_compatibility.py pytests/webui/test_plugin_page_routes.py pytests/webui/test_app.py pytests/webui/test_plugin_management_routes.py -q
git diff --check HEAD~4..HEAD
```

Expected: pytest exits with code 0, all selected tests pass, and Git reports no whitespace errors.

- [ ] **Step 5: Commit Phase 1 integration**

```bash
git add src pytests
git commit -m "feat: integrate runtime-backed plugin page discovery"
```

## Self-review checklist

- Manifest compatibility is covered by Task 1.
- Filesystem traversal and symlink checks are covered by Tasks 2 and 3.
- Authentication and existing route isolation are covered by Task 3.
- Runner isolation and runtime status filtering are covered by Task 4.
- No direct `register(app)`, arbitrary remote URL, or unauthenticated `StaticFiles` mount is introduced.
- Every production function added in Tasks 2-4 has a preceding failing test.
- Phase 2 frontend work is intentionally excluded from this plan.
