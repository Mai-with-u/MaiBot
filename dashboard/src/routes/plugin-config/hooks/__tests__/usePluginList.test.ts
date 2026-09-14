import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchPluginList,
  getInstalledPlugins,
  getMaimaiVersion,
  isPluginCompatible,
  togglePlugin,
} from '@/lib/plugin-api'
import type { InstalledPlugin, MaimaiVersion } from '@/lib/plugin-api'
import type { PluginInfo } from '@/types/plugin'

import { usePluginList } from '../usePluginList'

const { toastMock } = vi.hoisted(() => ({ toastMock: vi.fn() }))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/plugin-api', () => ({
  fetchPluginList: vi.fn(),
  getInstalledPlugins: vi.fn(),
  getMaimaiVersion: vi.fn(),
  isPluginCompatible: vi.fn(() => true),
  togglePlugin: vi.fn(),
}))

const defaultMaimaiVersion: MaimaiVersion = {
  version: '1.1.0',
  version_major: 1,
  version_minor: 1,
  version_patch: 0,
}

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

function makeMarketPlugin(id: string, manifest: Partial<PluginInfo['manifest']> = {}): PluginInfo {
  return {
    id,
    manifest: {
      manifest_version: 2,
      id,
      name: id,
      version: '1.1.0',
      description: '',
      author: { name: 'tester' },
      license: 'MIT',
      host_application: { min_version: '1.0.0' },
      repository_url: `https://example.com/${id}.git`,
      keywords: [],
      default_locale: 'zh',
      ...manifest,
    },
    downloads: 0,
    rating: 0,
    review_count: 0,
    installed: true,
    published_at: '2026-01-01',
    updated_at: '2026-01-01',
  }
}

async function renderPluginList() {
  const view = renderHook(() => usePluginList())
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
}

beforeEach(() => {
  window.history.replaceState(null, '', '/plugin-config')
  vi.mocked(isPluginCompatible).mockReturnValue(true)
  vi.mocked(getInstalledPlugins).mockResolvedValue([makePlugin('test.emoji')])
  vi.mocked(fetchPluginList).mockResolvedValue([])
  vi.mocked(getMaimaiVersion).mockResolvedValue(defaultMaimaiVersion)
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

describe('usePluginList 缺口', () => {
  it('适配器管理路径不拉市场更新，仅看有更新也不会补拉', async () => {
    window.history.replaceState(null, '', '/adapter-management')
    vi.mocked(getInstalledPlugins).mockResolvedValue([
      makePlugin('adapter.qq', { manifest: { plugin_type: 'adapter' } }),
      makePlugin('test.emoji', { manifest: { plugin_type: 'chat' } }),
    ])
    const { result } = await renderPluginList()

    expect(result.current.plugins.map((plugin) => plugin.id)).toEqual(['adapter.qq'])
    expect(fetchPluginList).not.toHaveBeenCalled()
    expect(getMaimaiVersion).not.toHaveBeenCalled()

    act(() => result.current.setShowUpdateOnly(true))
    act(() => result.current.closePluginConfig())
    expect(fetchPluginList).not.toHaveBeenCalled()
    expect(result.current.showUpdateOnly).toBe(true)
  })

  it('关闭「仅看有更新」不会触发市场检查', async () => {
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))
    const calls = vi.mocked(fetchPluginList).mock.calls.length

    act(() => result.current.setShowUpdateOnly(true))
    act(() => result.current.setShowUpdateOnly(false))
    expect(result.current.showUpdateOnly).toBe(false)
    expect(fetchPluginList).toHaveBeenCalledTimes(calls)
  })

  it('空搜索展示去重后的全部插件；无描述插件仍可按名称命中', async () => {
    const named = makePlugin('search.plugin', {
      manifest: { name: '天气助手', description: '' },
    })
    const duplicate = makePlugin('search.plugin', {
      path: '/plugins/search.plugin-copy',
      manifest: { name: '天气助手副本', description: '' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([named, duplicate])
    const { result } = await renderPluginList()

    expect(result.current.visiblePlugins).toHaveLength(1)
    act(() => result.current.setSearchQuery('天气助手'))
    expect(result.current.visiblePlugins.map((plugin) => plugin.id)).toEqual(['search.plugin'])
  })

  it('openPluginConfig 带 tab 时写入 URL；启停过程中暴露 actingPluginId', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    vi.mocked(getInstalledPlugins).mockResolvedValue([plugin])
    const { result } = await renderPluginList()

    act(() => result.current.openPluginConfig(plugin, 'host-policy'))
    expect(window.location.search).toBe('?plugin=test.emoji&tab=host-policy')
    expect(result.current.selectedPluginTab).toBe('host-policy')

    const deferred = createDeferred<{ success: boolean; enabled: boolean; message: string }>()
    vi.mocked(togglePlugin).mockReturnValueOnce(deferred.promise)
    let togglePromise!: Promise<void>
    act(() => {
      togglePromise = result.current.performTogglePlugin(plugin)
    })
    await waitFor(() => expect(result.current.actingPluginId).toBe('test.emoji'))

    await act(async () => {
      deferred.resolve({ success: true, enabled: true, message: 'ok' })
      await togglePromise
    })
    await waitFor(() => expect(result.current.actingPluginId).toBeNull())
  })

  it('isPluginCompatible 为 false 时，失败插件与市场上新版本都判为不兼容', async () => {
    const failed = makePlugin('p.fail', {
      load_status: 'failed',
      load_error: '普通启动错误',
      manifest: { version: '1.0.0', repository_url: 'https://example.com/fail.git' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([failed])
    vi.mocked(fetchPluginList).mockResolvedValue([
      makeMarketPlugin('p.fail', { version: '1.4.0' }),
    ])
    vi.mocked(isPluginCompatible).mockReturnValue(false)
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    expect(result.current.isPluginVersionIncompatible(failed)).toBe(true)
    expect(result.current.getPluginUpdateState(failed)).toEqual({
      canUpdate: false,
      hasUpdate: false,
      latestVersion: '1.4.0',
      title: '插件市场最新版本 v1.4.0 与当前麦麦不兼容',
    })
  })

  it('空白 load_error 不会命中不兼容标记，兼容 manifest 时不算版本不兼容', async () => {
    const failed = makePlugin('p.blank', {
      load_status: 'failed',
      load_error: '   ',
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([failed])
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    expect(result.current.isPluginVersionIncompatible(failed)).toBe(false)
  })

  it('comparePluginVersions 覆盖不同长度与本地仓库优先', async () => {
    const shortCurrent = makePlugin('short.plugin', {
      manifest: {
        version: '1.0',
        repository_url: 'https://local.example/short.git',
      },
    })
    const extraLatest = makePlugin('extra.plugin', {
      manifest: { version: '1.0.0', repository_url: 'https://example.com/extra.git' },
    })
    const newerCurrent = makePlugin('newer.plugin', {
      manifest: { version: '1.10.0', repository_url: 'https://example.com/newer.git' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([shortCurrent, extraLatest, newerCurrent])
    vi.mocked(fetchPluginList).mockResolvedValue([
      makeMarketPlugin('short.plugin', {
        version: '1.0.1',
        repository_url: 'https://market.example/short.git',
      }),
      makeMarketPlugin('extra.plugin', { version: '1.0.0.1' }),
      makeMarketPlugin('newer.plugin', { version: '1.9.0' }),
    ])
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    expect(result.current.getPluginRepositoryUrl(shortCurrent)).toBe(
      'https://local.example/short.git'
    )
    expect(result.current.getPluginUpdateState(shortCurrent)).toMatchObject({
      canUpdate: true,
      hasUpdate: true,
      latestVersion: '1.0.1',
    })
    expect(result.current.getPluginUpdateState(extraLatest)).toMatchObject({
      canUpdate: true,
      hasUpdate: true,
      latestVersion: '1.0.0.1',
    })
    expect(result.current.getPluginUpdateState(newerCurrent)).toMatchObject({
      canUpdate: false,
      hasUpdate: false,
      title: '当前已是最新版本',
    })
  })

  it('熔断 remaining_sec=0 的状态元数据，以及仅半开时不展示熔断汇总', async () => {
    const circuitIdle = makePlugin('p.open-idle', {
      load_status: 'failed',
      circuit_status: {
        state: 'open',
        remaining_sec: 0,
        cooldown_level: 1,
        half_open_inflight: false,
      },
    })
    const halfOpen = makePlugin('p.half', {
      load_status: 'failed',
      circuit_status: {
        state: 'half_open',
        remaining_sec: 1,
        cooldown_level: 1,
        half_open_inflight: true,
      },
    })
    const success = makePlugin('p.ok')
    vi.mocked(getInstalledPlugins).mockResolvedValue([circuitIdle, halfOpen, success])
    const { result } = await renderPluginList()

    expect(result.current.getPluginStatusMeta(circuitIdle)).toEqual({
      dotClassName: 'bg-orange-500',
      label: '熔断中',
      badgeClassName: 'border-orange-600 text-orange-600',
      icon: 'circuit',
    })
    expect(result.current.circuitOpenCount).toBe(1)
    expect(result.current.showsCircuitSummary).toBe(true)
    // 熔断插件同时计入 loadFailedCount 与 circuitActiveCount
    expect(result.current.circuitPercent).toBeCloseTo(40)

    vi.mocked(getInstalledPlugins).mockResolvedValue([halfOpen, success])
    await act(async () => {
      await result.current.loadPlugins()
    })
    expect(result.current.circuitOpenCount).toBe(0)
    expect(result.current.showsCircuitSummary).toBe(false)
    expect(result.current.modernLoadSummaryLabel).not.toContain('熔断中')
    expect(result.current.circuitPercent).toBeCloseTo((1 / 3) * 100)
  })

  it('空列表百分比为 0；无离线/熔断时摘要不包含可选段', async () => {
    vi.mocked(getInstalledPlugins).mockResolvedValue([])
    const empty = await renderPluginList()
    expect(empty.result.current.loadSuccessPercent).toBe(0)
    expect(empty.result.current.loadFailedPercent).toBe(0)
    expect(empty.result.current.loadingPercent).toBe(0)
    expect(empty.result.current.circuitPercent).toBe(0)
    expect(empty.result.current.modernLoadSummaryLabel).toBe(
      '加载成功 0 个，加载中 0 个，加载失败 0 个'
    )
    expect(empty.result.current.futureRetroPluginSummaryLabel).toBe(
      '已安装 0 个插件，已启用 0 个，已禁用 0 个，加载中 0 个，启动失败 0 个'
    )

    vi.mocked(getInstalledPlugins).mockResolvedValue([
      makePlugin('p.ok'),
      makePlugin('p.failed', { load_status: 'failed', load_error: 'boom' }),
      makePlugin('p.loading', { load_status: 'loading' }),
      makePlugin('p.off', { enabled: false }),
    ])
    const { result } = await renderPluginList()
    expect(result.current.loadSuccessPercent).toBeCloseTo((1 / 3) * 100)
    expect(result.current.loadFailedPercent).toBeCloseTo((1 / 3) * 100)
    expect(result.current.loadingPercent).toBeCloseTo((1 / 3) * 100)
    expect(result.current.circuitPercent).toBe(0)
    expect(result.current.modernLoadSummaryLabel).toBe(
      '加载成功 1 个，加载中 1 个，加载失败 1 个'
    )
    expect(result.current.futureRetroPluginSummaryLabel).toBe(
      '已安装 4 个插件，已启用 3 个，已禁用 1 个，加载中 1 个，启动失败 1 个'
    )
  })

  it('仅看有更新且没有任何新版本时可见列表为空', async () => {
    const latest = makePlugin('latest.plugin', {
      manifest: { version: '2.0.0', repository_url: 'https://example.com/latest.git' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([latest])
    vi.mocked(fetchPluginList).mockResolvedValue([
      makeMarketPlugin('latest.plugin', { version: '2.0.0' }),
    ])
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    act(() => result.current.setShowUpdateOnly(true))
    expect(result.current.visiblePlugins).toEqual([])
    expect(result.current.visiblePluginGroups).toEqual([])
  })
})
