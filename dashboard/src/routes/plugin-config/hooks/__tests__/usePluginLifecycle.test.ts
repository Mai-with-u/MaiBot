import type { MouseEvent } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { uninstallPlugin, updatePlugin } from '@/lib/plugin-api'
import type { InstalledPlugin, PluginLoadProgress } from '@/lib/plugin-api'

import { usePluginLifecycle } from '../usePluginLifecycle'

const { toastMock, progressClient } = vi.hoisted(() => {
  const progressClient = {
    listener: null as null | ((progress: PluginLoadProgress) => void),
    cleanup: vi.fn(async () => undefined),
    subscribe: vi.fn(),
    emit(progress: PluginLoadProgress) {
      progressClient.listener?.(progress)
    },
  }
  return { toastMock: vi.fn(), progressClient }
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/plugin-progress-client', () => ({
  pluginProgressClient: { subscribe: progressClient.subscribe },
}))

vi.mock('@/lib/plugin-api', () => ({
  uninstallPlugin: vi.fn(),
  updatePlugin: vi.fn(),
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

function makeClickEvent() {
  return {
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  } as unknown as MouseEvent<HTMLButtonElement>
}

function makePlugin(
  id: string,
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
      name: id,
      version: '1.0.0',
      description: `${id} 描述`,
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

function makeProgress(
  overrides: Partial<PluginLoadProgress> & Pick<PluginLoadProgress, 'operation' | 'stage'>
): PluginLoadProgress {
  return {
    progress: 10,
    message: '进行中',
    total_plugins: 1,
    loaded_plugins: 0,
    plugin_id: 'test.emoji',
    ...overrides,
  }
}

function renderLifecycle(
  overrides: Partial<{
    getPluginRepositoryUrl: (plugin: InstalledPlugin) => string | undefined
    onChanged: () => Promise<void> | void
    setActingPluginId: (id: string | null) => void
  }> = {}
) {
  const options = {
    getPluginRepositoryUrl: vi.fn((plugin: InstalledPlugin) => plugin.manifest.repository_url),
    onChanged: vi.fn(),
    setActingPluginId: vi.fn(),
    ...overrides,
  }
  const view = renderHook(() => usePluginLifecycle(options))
  return { ...view, options }
}

beforeEach(() => {
  progressClient.listener = null
  progressClient.cleanup.mockClear()
  progressClient.subscribe.mockImplementation(
    async (callback: (progress: PluginLoadProgress) => void) => {
      progressClient.listener = callback
      return progressClient.cleanup
    }
  )
  vi.mocked(uninstallPlugin).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(updatePlugin).mockResolvedValue({ success: true, message: 'ok' } as never)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('usePluginLifecycle 缺口', () => {
  it('打开对话框会重新订阅进度；关闭后忽略后续事件', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    const { result } = renderLifecycle()
    await waitFor(() => expect(progressClient.subscribe).toHaveBeenCalledTimes(1))

    act(() => result.current.openDeletePluginDialog(plugin, makeClickEvent()))
    await waitFor(() => expect(progressClient.subscribe).toHaveBeenCalledTimes(2))
    expect(progressClient.cleanup).toHaveBeenCalled()

    act(() => result.current.closeDeletePluginDialog())
    expect(result.current.deletingPlugin).toBeNull()

    act(() => {
      progressClient.emit(
        makeProgress({ operation: 'uninstall', stage: 'loading', plugin_id: 'test.emoji', progress: 40 })
      )
      progressClient.emit(
        makeProgress({ operation: 'update', stage: 'loading', plugin_id: 'test.emoji', progress: 70 })
      )
    })
    expect(result.current.deleteProgress).toBeNull()
    expect(result.current.updateProgress).toBeNull()
  })

  it('只写入当前操作且 plugin_id 匹配的进度', async () => {
    const plugin = makePlugin('test.emoji')
    const { result } = renderLifecycle()
    await waitFor(() => expect(progressClient.subscribe).toHaveBeenCalled())

    act(() => result.current.openDeletePluginDialog(plugin, makeClickEvent()))
    await waitFor(() => expect(progressClient.listener).toBeTruthy())

    act(() => {
      progressClient.emit(
        makeProgress({ operation: 'update', stage: 'loading', plugin_id: 'test.emoji', progress: 88 })
      )
      progressClient.emit(
        makeProgress({
          operation: 'uninstall',
          stage: 'loading',
          plugin_id: 'other.plugin',
          progress: 44,
        })
      )
    })
    expect(result.current.deleteProgress).toBeNull()
    expect(result.current.updateProgress).toBeNull()

    act(() => {
      progressClient.emit(
        makeProgress({
          operation: 'uninstall',
          stage: 'loading',
          plugin_id: 'test.emoji',
          progress: 30,
        })
      )
    })
    expect(result.current.deleteProgress).toMatchObject({
      operation: 'uninstall',
      progress: 30,
    })
  })

  it('仓库地址为空字符串时按缺仓库处理，不调用更新接口', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    const { result, options } = renderLifecycle({
      getPluginRepositoryUrl: vi.fn(() => ''),
    })

    act(() => result.current.openUpdatePluginDialog(plugin, makeClickEvent()))
    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })

    expect(updatePlugin).not.toHaveBeenCalled()
    expect(options.setActingPluginId).not.toHaveBeenCalled()
    expect(result.current.updateProgress).toMatchObject({
      operation: 'update',
      stage: 'error',
      message: '插件清单中没有仓库地址，无法更新/升级',
    })
  })

  it('更新失败后可直接再次确认，不必重开对话框', async () => {
    const plugin = makePlugin('test.emoji', {
      manifest: { name: 'Emoji Plugin', repository_url: 'https://example.com/emoji.git' },
    })
    const { result, options } = renderLifecycle()

    act(() => result.current.openUpdatePluginDialog(plugin, makeClickEvent()))
    vi.mocked(updatePlugin).mockRejectedValueOnce(new Error('第一次失败'))
    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })
    expect(result.current.updateProgress?.stage).toBe('error')
    expect(result.current.updateDialogOpen).toBe(true)

    vi.mocked(updatePlugin).mockResolvedValueOnce({ success: true, message: 'ok' } as never)
    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })
    await waitFor(() => expect(result.current.updateProgress?.stage).toBe('success'))
    expect(updatePlugin).toHaveBeenCalledTimes(2)
    expect(options.onChanged).toHaveBeenCalledTimes(1)
    expect(options.setActingPluginId).toHaveBeenLastCalledWith(null)
  })

  it('卸载过程中会标记 actingPluginId，可用 setDeleteDialogOpen 同步对话框', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    const { result, options } = renderLifecycle()

    act(() => result.current.setDeleteDialogOpen(true))
    expect(result.current.deleteDialogOpen).toBe(true)
    act(() => result.current.setUpdateDialogOpen(true))
    expect(result.current.updateDialogOpen).toBe(true)

    act(() => result.current.openDeletePluginDialog(plugin, makeClickEvent()))
    const deferred = createDeferred<{ success: boolean; message: string }>()
    vi.mocked(uninstallPlugin).mockReturnValueOnce(deferred.promise)
    let deletePromise!: Promise<void>
    act(() => {
      deletePromise = result.current.handleConfirmDeletePlugin()
    })
    await waitFor(() => expect(options.setActingPluginId).toHaveBeenCalledWith('test.emoji'))
    expect(result.current.deleteProgress?.stage).toBe('loading')

    await act(async () => {
      deferred.resolve({ success: true, message: 'ok' })
      await deletePromise
    })
    expect(options.setActingPluginId).toHaveBeenLastCalledWith(null)
    expect(result.current.deleteProgress?.stage).toBe('success')
  })
})
