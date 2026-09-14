import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getPluginConfigBundle,
  resetPluginConfig,
  togglePlugin,
  updatePluginConfig,
  updatePluginConfigRaw,
} from '@/lib/plugin-api'
import type { InstalledPlugin, PluginConfigBundle } from '@/lib/plugin-api'

import { usePluginConfigEditor } from '../usePluginConfigEditor'

const { toastMock, blockerState } = vi.hoisted(() => {
  const blockerState = {
    status: 'unblocked' as 'unblocked' | 'blocked',
    reset: vi.fn(),
    proceed: vi.fn(),
    lastOptions: undefined as
      | {
          shouldBlockFn: () => boolean
          enableBeforeUnload: boolean
          withResolver: boolean
        }
      | undefined,
  }
  return { toastMock: vi.fn(), blockerState }
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@tanstack/react-router', () => ({
  useBlocker: (options: {
    shouldBlockFn: () => boolean
    enableBeforeUnload: boolean
    withResolver: boolean
  }) => {
    blockerState.lastOptions = options
    return blockerState
  },
}))

vi.mock('@/lib/plugin-api', () => ({
  getPluginConfigBundle: vi.fn(),
  updatePluginConfig: vi.fn(),
  updatePluginConfigRaw: vi.fn(),
  resetPluginConfig: vi.fn(),
  togglePlugin: vi.fn(),
}))

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

function makePlugin(
  id = 'test.emoji',
  overrides: Partial<Omit<InstalledPlugin, 'manifest'>> & {
    manifest?: Partial<InstalledPlugin['manifest']>
  } = {}
): InstalledPlugin {
  const { manifest, ...rest } = overrides
  return {
    id,
    path: `/plugins/${id}`,
    enabled: true,
    load_status: 'success',
    manifest: {
      manifest_version: 2,
      id,
      name: 'Emoji Plugin',
      version: '1.0.0',
      description: 'desc',
      author: { name: 'tester' },
      license: 'MIT',
      host_application: { min_version: '1.0.0' },
      keywords: [],
      default_locale: 'zh',
      ...manifest,
    },
    ...rest,
  }
}

function makeBundle(overrides: Partial<PluginConfigBundle> = {}): PluginConfigBundle {
  return {
    schema: {
      plugin_id: 'test.emoji',
      plugin_info: { name: 'Emoji', version: '1.0.0', description: 'desc', author: 'tester' },
      sections: {
        general: {
          name: 'general',
          title: '通用',
          collapsed: false,
          order: 0,
          fields: {},
        },
      },
      layout: { type: 'auto', tabs: [] },
    },
    config: { general: { name: 'old' } },
    rawConfig: 'name = "old"\n',
    ...overrides,
  }
}

async function renderEditor(
  plugin: InstalledPlugin = makePlugin(),
  onBack = vi.fn(),
  initialTab?: string
) {
  const view = renderHook(() => usePluginConfigEditor({ plugin, onBack, initialTab }))
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return { ...view, onBack, plugin }
}

beforeEach(() => {
  blockerState.status = 'unblocked'
  blockerState.lastOptions = undefined
  blockerState.reset.mockImplementation(() => {
    blockerState.status = 'unblocked'
  })
  blockerState.proceed.mockImplementation(() => {
    blockerState.status = 'unblocked'
  })
  window.history.replaceState(null, '', '/plugin-config')
  vi.mocked(getPluginConfigBundle).mockResolvedValue(makeBundle())
  vi.mocked(updatePluginConfig).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(updatePluginConfigRaw).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(resetPluginConfig).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(togglePlugin).mockResolvedValue({
    success: true,
    enabled: true,
    message: '插件已启用',
  })
})

afterEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, '', '/plugin-config')
})

describe('usePluginConfigEditor 缺口', () => {
  it('默认页签为 settings，可切换 pluginPageTab 与重置对话框', async () => {
    const { result } = await renderEditor()
    expect(result.current.pluginPageTab).toBe('settings')
    expect(result.current.resetDialogOpen).toBe(false)

    act(() => result.current.setPluginPageTab('host-policy'))
    expect(result.current.pluginPageTab).toBe('host-policy')
    act(() => result.current.setPluginPageTab('details'))
    expect(result.current.pluginPageTab).toBe('details')

    act(() => result.current.setResetDialogOpen(true))
    expect(result.current.resetDialogOpen).toBe(true)
    act(() => result.current.setResetDialogOpen(false))
    expect(result.current.resetDialogOpen).toBe(false)
  })

  it('handleFieldChange 会创建缺失的嵌套节', async () => {
    const { result } = await renderEditor()
    act(() => result.current.handleFieldChange('section.nested', 'field', 7))
    expect(result.current.config).toEqual({
      general: { name: 'old' },
      section: { nested: { field: 7 } },
    })
    expect(result.current.hasChanges).toBe(true)
  })

  it('切换编辑模式时 hasChanges 跟随当前草稿；无 toml 错误时改源码保持 false', async () => {
    const { result } = await renderEditor()
    act(() => result.current.handleFieldChange('general', 'name', 'dirty'))
    expect(result.current.hasChanges).toBe(true)

    act(() => result.current.setEditMode('source'))
    expect(result.current.editMode).toBe('source')
    expect(result.current.hasChanges).toBe(false)
    expect(result.current.hasTomlError).toBe(false)

    act(() => result.current.handleSourceCodeChange('name = "old"\n'))
    expect(result.current.hasTomlError).toBe(false)
    expect(result.current.hasChanges).toBe(false)

    act(() => result.current.handleSourceCodeChange('name = "src"\n'))
    expect(result.current.hasChanges).toBe(true)

    act(() => result.current.setEditMode('visual'))
    expect(result.current.hasChanges).toBe(true)
  })

  it('可视化保存失败按 Error / 非 Error 弹出 toast，且保持未保存', async () => {
    const { result } = await renderEditor()
    act(() => result.current.handleFieldChange('general', 'name', 'new'))

    vi.mocked(updatePluginConfig).mockRejectedValueOnce(new Error('可视化写入失败'))
    await act(async () => {
      await expect(result.current.handleSave()).resolves.toBe(false)
    })
    expect(updatePluginConfigRaw).not.toHaveBeenCalled()
    expect(result.current.hasChanges).toBe(true)
    expect(toastMock).toHaveBeenCalledWith({
      title: '保存失败',
      description: '可视化写入失败',
      variant: 'destructive',
    })

    vi.mocked(updatePluginConfig).mockRejectedValueOnce('offline')
    await act(async () => {
      await expect(result.current.handleSave()).resolves.toBe(false)
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '保存失败',
      description: '未知错误',
      variant: 'destructive',
    })
  })

  it('保存过程中 saving 为 true，结束后恢复', async () => {
    const { result } = await renderEditor()
    act(() => result.current.handleFieldChange('general', 'name', 'new'))

    const deferred = createDeferred<{ success: boolean; message: string }>()
    vi.mocked(updatePluginConfig).mockReturnValueOnce(deferred.promise)
    let savePromise!: Promise<boolean>
    act(() => {
      savePromise = result.current.handleSave()
    })
    await waitFor(() => expect(result.current.saving).toBe(true))

    await act(async () => {
      deferred.resolve({ success: true, message: 'ok' })
      await expect(savePromise).resolves.toBe(true)
    })
    expect(result.current.saving).toBe(false)
    expect(result.current.hasChanges).toBe(false)
  })

  it('closeLeavePrompt 在未被拦截时不调用 reset', async () => {
    const { result } = await renderEditor()
    act(() => result.current.handleFieldChange('general', 'name', 'dirty'))
    act(() => result.current.handleBack())
    expect(result.current.internalLeavePromptOpen).toBe(true)

    blockerState.status = 'unblocked'
    act(() => result.current.closeLeavePrompt())
    expect(blockerState.reset).not.toHaveBeenCalled()
    expect(result.current.internalLeavePromptOpen).toBe(false)
  })
})
