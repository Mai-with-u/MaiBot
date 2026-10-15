import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'
import {
  extensionPath,
  invokePluginWebUI,
  refreshPluginWebUI,
  usePluginWebUI,
  visibleExtensions,
} from '@/lib/plugin-webui'
import type { WebUIExtension } from '@/lib/plugin-webui'

vi.mock('@/lib/http', () => ({ backendApi: { post: vi.fn(), get: vi.fn() } }))

describe('plugin WebUI gateway', () => {
  it('preserves unchanged declarations during navigation registry refresh', async () => {
    const extension: WebUIExtension = {
      schema_version: 2,
      plugin_id: 'stable.plugin',
      workspace_title: 'Statistics',
      pages: [],
    }
    vi.mocked(backendApi.get).mockResolvedValue({
      schema_version: 2,
      capabilities: [],
      extensions: [extension],
    })
    const hook = renderHook(() => usePluginWebUI())
    await act(async () => {
      await refreshPluginWebUI()
    })
    const previous = hook.result.current.extensions[0]
    vi.mocked(backendApi.get).mockResolvedValue({
      schema_version: 2,
      capabilities: [],
      extensions: [structuredClone(extension)],
    })
    await act(async () => {
      await refreshPluginWebUI()
    })
    expect(hook.result.current.extensions[0]).toBe(previous)
    vi.mocked(backendApi.get).mockResolvedValue({
      schema_version: 2,
      capabilities: [],
      extensions: [{ ...extension, workspace_title: 'New title' }],
    })
    await act(async () => {
      await refreshPluginWebUI()
    })
    expect(hook.result.current.extensions[0]).not.toBe(previous)
  })
  it('isolates unsupported capabilities to the affected plugin', async () => {
    vi.mocked(backendApi.get).mockResolvedValue({
      schema_version: 2,
      capabilities: [],
      extensions: [
        {
          schema_version: 2,
          plugin_id: 'unsupported',
          pages: [],
          required_capabilities: ['future_feature'],
        },
        { schema_version: 2, plugin_id: 'supported', pages: [], required_capabilities: [] },
      ],
    })
    const hook = renderHook(() => usePluginWebUI())
    await act(async () => {
      await refreshPluginWebUI()
    })
    expect(hook.result.current.error).toBeNull()
    expect(hook.result.current.extensions[0].compatibility_error).toContain('future_feature')
    expect(hook.result.current.extensions[1].compatibility_error).toBeUndefined()
    hook.unmount()
  })
  it('encodes plugin and page identity into the dedicated gateway path', async () => {
    vi.mocked(backendApi.post).mockResolvedValue({ result: { count: 2 } })
    expect(extensionPath('plugin/a', 'page b')).toBe('/extensions/plugin%2Fa/page%20b')
    expect(
      await invokePluginWebUI('plugin/a', 'overview', 'actions', 'reset', { count: 2 }, true)
    ).toEqual({ count: 2 })
    expect(backendApi.post).toHaveBeenCalledWith(
      '/api/webui/plugins/runtime/webui/plugin%2Fa/overview/actions/reset',
      {
        body: { args: { count: 2 }, confirmed: true },
        signal: undefined,
      }
    )
  })

  it('hides and orders entries without altering registered declarations', () => {
    const extensions: WebUIExtension[] = ['a', 'b', 'c'].map((plugin_id) => ({
      schema_version: 2,
      plugin_id,
      workspace_title: null,
      pages: [],
    }))
    const result = visibleExtensions({
      extensions,
      loading: false,
      error: null,
      preferences: { hidden: ['b'], order: ['c', 'a'] },
    })
    expect(result.map((item) => item.plugin_id)).toEqual(['c', 'a'])
    expect(extensions.map((item) => item.plugin_id)).toEqual(['a', 'b', 'c'])
  })
})
