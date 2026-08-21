# MaiBot WebUI Plugin Pages Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为已声明 WebUI 页面提供经过认证、白名单校验和 Runner RPC 隔离的插件后端 API 代理。

**Architecture:** WebUI Host 继续持有页面注册表，并在现有 `/api/webui/plugins` Router 中增加页面 API endpoint。Host 根据 `plugin_id/page_id` 找到页面记录，只把页面 Manifest 的 `api` 映射转换为插件组件名；运行时管理器提供公开的 API 查询和 `invoke_api` 路由方法，最终由对应 `PluginRunnerSupervisor` 通过 IPC 调用 Runner。插件异常、超时和不可序列化结果在 Host 边界转换为稳定的 HTTP 错误，不直接暴露内部堆栈。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、pytest、pytest-asyncio、现有 PluginRuntime RPC/Envelope。

**Spec:** `docs/superpowers/specs/2026-08-20-maibot-webui-plugin-page-design.md`

## Global Constraints

- 页面 API 只能使用 `POST /api/webui/plugins/{plugin_id}/pages/{page_id}/api/{operation}`，不允许插件直接注册 FastAPI `app`。
- 所有页面 API 必须经过现有 `require_auth`、页面记录查找、Manifest `api` 白名单和插件 API 启用状态校验。
- 请求体 JSON 序列化后最大 64 KiB；RPC 超时时间固定限制在 1,000～30,000 ms。
- Runner 内部异常堆栈只写 Host 日志，HTTP 响应不得包含堆栈、文件路径或 IPC 细节。
- 保留现有页面清单、资源、插件管理、插件市场和 MCP 路由行为；不修改 `.omo/`。
- 新增行为必须先写失败测试，确认红灯后再写生产代码；新增复杂函数保留类型注解和中文注释。

---

### Task 1: Expose runtime API lookup and invocation boundaries

**Files:**
- Modify: `src/plugin_runtime/integration.py`
- Test: `pytests/plugin_runtime/test_runtime_plugin_api.py`

**Interfaces:**
- Produces `PluginRuntimeManager.get_plugin_api(plugin_id: str, component_name: str, enabled_only: bool = True) -> Optional[APIEntry]`.
- Produces `PluginRuntimeManager.invoke_api(plugin_id: str, component_name: str, args: Optional[Dict[str, Any]] = None, timeout_ms: int = 30000) -> Any` and routes to the existing supervisor RPC with method `plugin.invoke_api`.

- [x] **Step 1: Write the failing tests**

```python
import pytest

from src.plugin_runtime.host.api_registry import APIRegistry
from src.plugin_runtime.integration import PluginRuntimeManager


@pytest.mark.asyncio
async def test_invoke_api_routes_to_the_plugin_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = PluginRuntimeManager()
    entry = APIRegistry()
    entry.register_api("get_status", "example.first", {"enabled": True})

    class FakeSupervisor:
        api_registry = entry

        async def invoke_api(self, plugin_id, component_name, args=None, timeout_ms=30000):
            return {"plugin_id": plugin_id, "component_name": component_name, "args": args, "timeout_ms": timeout_ms}

        def get_loaded_plugin_ids(self):
            return ["example.first"]

    monkeypatch.setattr(manager, "_builtin_supervisor", FakeSupervisor())
    result = await manager.invoke_api("example.first", "get_status", {"verbose": True}, 1500)

    assert result["component_name"] == "get_status"
    assert result["timeout_ms"] == 1500
    assert manager.get_plugin_api("example.first", "get_status") is not None


def test_get_plugin_api_hides_disabled_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = PluginRuntimeManager()
    registry = APIRegistry()
    registry.register_api("get_status", "example.first", {"enabled": False})

    class FakeSupervisor:
        api_registry = registry

        def get_loaded_plugin_ids(self):
            return ["example.first"]

    monkeypatch.setattr(manager, "_builtin_supervisor", FakeSupervisor())
    assert manager.get_plugin_api("example.first", "get_status") is None
    assert manager.get_plugin_api("example.first", "get_status", enabled_only=False) is not None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run --frozen python -m pytest pytests/plugin_runtime/test_runtime_plugin_api.py -q`
Expected: FAIL because `PluginRuntimeManager` has no public `get_plugin_api` or `invoke_api` methods.

- [x] **Step 3: Write minimal implementation**

Add public methods beside the existing `invoke_plugin` method. Resolve the unique Supervisor with `_get_supervisor_for_plugin`; return `None` when the plugin or component is not registered. Delegate `invoke_api` to the Supervisor method and preserve the existing async return type. Import `APIEntry` only for the method annotation.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run --frozen python -m pytest pytests/plugin_runtime/test_runtime_plugin_api.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/plugin_runtime/integration.py pytests/plugin_runtime/test_runtime_plugin_api.py
git commit -m "feat: expose runtime plugin api invocation"
```

### Task 2: Add the authenticated page API proxy

**Files:**
- Modify: `src/webui/routers/plugin/pages.py`
- Test: `pytests/webui/test_plugin_page_api_routes.py`

**Interfaces:**
- Adds `POST /api/webui/plugins/{plugin_id}/pages/{page_id}/api/{operation}`.
- Request body is parsed as `Dict[str, Any]`; response is `{success: True, data: <plugin result>}`.
- `PluginPageRecord.api[operation]` is the only source of the RPC component name.

- [x] **Step 1: Write the failing tests**

Cover one behavior per test:

```python
def test_page_api_invokes_manifest_allowlisted_component(page_api_app):
    response = page_api_app.post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={"verbose": True},
    )
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"status": "ok"}}


def test_page_api_rejects_operation_missing_from_manifest(page_api_app):
    response = page_api_app.post(
        "/api/webui/plugins/example.first/pages/hello/api/delete_all",
        json={},
    )
    assert response.status_code == 404


def test_page_api_rejects_disabled_registry_entry(page_api_app):
    page_api_app.state.fake_api_enabled = False
    response = page_api_app.post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={},
    )
    assert response.status_code == 403


def test_page_api_maps_rpc_timeout_to_gateway_timeout(page_api_app):
    page_api_app.state.fake_rpc_error = RPCError(ErrorCode.E_TIMEOUT, "internal timeout")
    response = page_api_app.post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        json={},
    )
    assert response.status_code == 504
    assert response.json()["detail"] == "插件页面 API 调用超时"


def test_page_api_rejects_body_over_64_kib(page_api_app):
    response = page_api_app.post(
        "/api/webui/plugins/example.first/pages/hello/api/get_status",
        content=("x" * (64 * 1024 + 1)).encode(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
```

The fixture patches `_get_page_records`, `get_plugin_runtime_manager`, and `require_auth` with a fake page and runtime. Add tests for 401 through the existing dependency behavior and for a non-JSON-serializable RPC result returning 502.

- [x] **Step 2: Run test to verify it fails**

Run: `uv run --frozen python -m pytest pytests/webui/test_plugin_page_api_routes.py -q`
Expected: FAIL because the API route and proxy helpers do not exist.

- [x] **Step 3: Write minimal implementation**

Implement focused helpers in `pages.py`:

```python
_MAX_PAGE_API_BODY_BYTES = 64 * 1024
_MIN_PAGE_API_TIMEOUT_MS = 1_000
_MAX_PAGE_API_TIMEOUT_MS = 30_000
_OPERATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


async def invoke_plugin_page_api(
    plugin_id: str,
    page_id: str,
    operation: str,
    request: Request,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    ...
```

The handler validates `Content-Length` when present and reads the body only when needed so chunked requests are also bounded. Reject invalid operation syntax with 404, missing page with 404, missing allowlist entry with 404, and disabled/mismatched registry entry with 403. Use the page API mapping to obtain `component_name`; call `runtime.invoke_api` with the API entry timeout clamped to the 1～30 second boundary. Catch `RPCError` explicitly (`E_TIMEOUT` -> 504, other Runner errors -> 502), catch unexpected exceptions with `logger.exception` and return 502, then use `jsonable_encoder`/`json.dumps` validation before responding. Never include the exception string in the public detail for Runner failures.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run --frozen python -m pytest pytests/webui/test_plugin_page_api_routes.py pytests/webui/test_plugin_page_routes.py -q`
Expected: PASS with the existing page list and asset tests unchanged.

- [x] **Step 5: Commit**

```bash
git add src/webui/routers/plugin/pages.py pytests/webui/test_plugin_page_api_routes.py
git commit -m "feat: proxy plugin page api calls"
```

### Task 3: Validate runtime integration and regression boundaries

**Files:**
- Modify: only files required by failing verification.
- Test: `pytests/plugin_runtime/test_runtime_plugin_api.py`, `pytests/webui/test_plugin_page_api_routes.py`, `pytests/webui/test_plugin_page_routes.py`, `pytests/webui/test_app.py`

- [x] **Step 1: Run focused Phase 3 tests**

Run: `uv run --frozen python -m pytest pytests/plugin_runtime/test_runtime_plugin_api.py pytests/webui/test_plugin_page_api_routes.py pytests/webui/test_plugin_page_routes.py pytests/webui/test_app.py -q`
Expected: all focused tests pass.

- [x] **Step 2: Run lint and compile checks**

Run: `uv run --frozen ruff check src/plugin_runtime/integration.py src/webui/routers/plugin/pages.py pytests/plugin_runtime/test_runtime_plugin_api.py pytests/webui/test_plugin_page_api_routes.py`
Run: `uv run --frozen python -m compileall -q src/plugin_runtime/integration.py src/webui/routers/plugin/pages.py`
Expected: no lint or compile errors.

- [x] **Step 3: Run Phase 1 and Phase 2 regression checks**

Run the frozen Phase 1 Python test list and Dashboard focused tests from the previous plans. Record the known duplicate `platform_io` collection conflict and unrelated legacy install-fixture failures separately; do not modify them in Phase 3.

- [x] **Step 4: Check the final diff**

Run: `git diff --check` and `git status --short --branch`.
Expected: only Phase 3 files are tracked; `.omo/` remains untracked and untouched.

- [x] **Step 5: Commit verification-only fixes**

Commit only if a focused verification exposes a Phase 3 regression. Do not create or push a PR until all phases are complete and the user explicitly asks for PR preparation.

## Self-review checklist

- The endpoint never accepts a component name directly from the browser.
- The runtime lookup is scoped to the same plugin and rejects disabled APIs.
- Request body size and RPC timeout are bounded before entering Runner IPC.
- RPC errors are converted to stable HTTP statuses without internal stack traces.
- Existing page list, static asset, plugin management, marketplace and MCP routes remain unchanged.
- Tests exercise the actual FastAPI route and a fake Supervisor RPC boundary.
