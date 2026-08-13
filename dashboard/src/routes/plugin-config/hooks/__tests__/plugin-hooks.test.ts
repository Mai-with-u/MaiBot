import type { MouseEvent } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchPluginList,
  getInstalledPlugins,
  getMaimaiVersion,
  getPluginConfigBundle,
  isPluginCompatible,
  resetPluginConfig,
  togglePlugin,
  uninstallPlugin,
  updatePlugin,
  updatePluginConfig,
  updatePluginConfigRaw,
} from '@/lib/plugin-api'
import type {
  InstalledPlugin,
  MaimaiVersion,
  PluginConfigBundle,
  PluginLoadProgress,
} from '@/lib/plugin-api'
import type { PluginInfo } from '@/types/plugin'

import { usePluginConfigEditor } from '../usePluginConfigEditor'
import { usePluginLifecycle } from '../usePluginLifecycle'
import { usePluginList } from '../usePluginList'

const { toastMock, blockerState, progressClient } = vi.hoisted(() => {
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
  const progressClient = {
    listener: null as null | ((progress: PluginLoadProgress) => void),
    cleanup: vi.fn(async () => undefined),
    subscribe: vi.fn(),
    emit(progress: PluginLoadProgress) {
      progressClient.listener?.(progress)
    },
  }
  return { toastMock: vi.fn(), blockerState, progressClient }
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

vi.mock('@/lib/plugin-progress-client', () => ({
  pluginProgressClient: { subscribe: progressClient.subscribe },
}))

vi.mock('@/lib/plugin-api', () => ({
  fetchPluginList: vi.fn(),
  getInstalledPlugins: vi.fn(),
  getMaimaiVersion: vi.fn(),
  isPluginCompatible: vi.fn(() => true),
  togglePlugin: vi.fn(),
  getPluginConfigBundle: vi.fn(),
  updatePluginConfig: vi.fn(),
  updatePluginConfigRaw: vi.fn(),
  resetPluginConfig: vi.fn(),
  uninstallPlugin: vi.fn(),
  updatePlugin: vi.fn(),
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

function makeMarketPlugin(
  id: string,
  manifest: Partial<PluginInfo['manifest']> = {}
): PluginInfo {
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

async function renderPluginList() {
  const view = renderHook(() => usePluginList())
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return view
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

async function renderEditor(
  plugin: InstalledPlugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } }),
  onBack = vi.fn(),
  initialTab?: string
) {
  const view = renderHook(() => usePluginConfigEditor({ plugin, onBack, initialTab }))
  await waitFor(() => expect(view.result.current.loading).toBe(false))
  return { ...view, onBack, plugin }
}

beforeEach(() => {
  progressClient.listener = null
  progressClient.cleanup.mockClear()
  progressClient.subscribe.mockImplementation(async (callback: (progress: PluginLoadProgress) => void) => {
    progressClient.listener = callback
    return progressClient.cleanup
  })
  blockerState.status = 'unblocked'
  blockerState.lastOptions = undefined
  blockerState.reset.mockImplementation(() => {
    blockerState.status = 'unblocked'
  })
  blockerState.proceed.mockImplementation(() => {
    blockerState.status = 'unblocked'
  })
  window.history.replaceState(null, '', '/plugin-config')
  vi.mocked(isPluginCompatible).mockReturnValue(true)
  vi.mocked(getInstalledPlugins).mockResolvedValue([
    makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } }),
  ])
  vi.mocked(fetchPluginList).mockResolvedValue([])
  vi.mocked(getMaimaiVersion).mockResolvedValue(defaultMaimaiVersion)
  vi.mocked(togglePlugin).mockResolvedValue({
    success: true,
    enabled: true,
    message: '插件已启用',
  })
  vi.mocked(getPluginConfigBundle).mockResolvedValue(makeBundle())
  vi.mocked(updatePluginConfig).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(updatePluginConfigRaw).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(resetPluginConfig).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(uninstallPlugin).mockResolvedValue({ success: true, message: 'ok' })
  vi.mocked(updatePlugin).mockResolvedValue({ success: true, message: 'ok' } as never)
})

afterEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, '', '/plugin-config')
})

describe('usePluginList', () => {
  it('加载已装插件，并按深链接自动选中插件与配置页签', async () => {
    window.history.replaceState(null, '', '/plugin-config?plugin=test.emoji&tab=advanced')
    const { result } = await renderPluginList()

    expect(result.current.plugins).toHaveLength(1)
    expect(result.current.selectedPlugin?.id).toBe('test.emoji')
    expect(result.current.selectedPluginTab).toBe('advanced')
    expect(window.location.search).toBe('?plugin=test.emoji&tab=advanced')
    // 深链接已选中插件时，首屏不拉市场更新
    expect(fetchPluginList).not.toHaveBeenCalled()
  })

  it('深链接插件不在已装列表时保持未选中', async () => {
    window.history.replaceState(null, '', '/plugin-config?plugin=missing.plugin')
    const { result } = await renderPluginList()

    expect(result.current.selectedPlugin).toBeNull()
    expect(result.current.selectedPluginTab).toBeUndefined()
  })

  it('加载失败时用 Error.message 弹出 toast', async () => {
    vi.mocked(getInstalledPlugins).mockRejectedValue(new Error('列表接口不可用'))
    const { result } = await renderPluginList()

    expect(result.current.plugins).toEqual([])
    expect(toastMock).toHaveBeenCalledWith({
      title: '加载插件列表失败',
      description: '列表接口不可用',
      variant: 'destructive',
    })
  })

  it('加载失败且非 Error 时使用未知错误文案', async () => {
    vi.mocked(getInstalledPlugins).mockRejectedValue('offline')
    await renderPluginList()

    expect(toastMock).toHaveBeenCalledWith({
      title: '加载插件列表失败',
      description: '未知错误',
      variant: 'destructive',
    })
  })

  it('适配器管理路径只保留 adapter 类型插件', async () => {
    window.history.replaceState(null, '', '/adapter-management')
    vi.mocked(getInstalledPlugins).mockResolvedValue([
      makePlugin('adapter.qq', { manifest: { name: 'QQ Adapter', plugin_type: 'adapter' } }),
      makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin', plugin_type: 'chat' } }),
    ])
    const { result } = await renderPluginList()

    expect(result.current.plugins.map((plugin) => plugin.id)).toEqual(['adapter.qq'])
    expect(result.current.installedCount).toBe(1)
  })

  it('按 id/名称/描述搜索，并去掉重复 plugin.id', async () => {
    const first = makePlugin('dup.plugin', { manifest: { name: 'Alpha', description: '天气助手' } })
    const duplicate = makePlugin('dup.plugin', {
      path: '/plugins/dup.plugin-copy',
      manifest: { name: 'Alpha Copy', description: '天气助手副本' },
    })
    const other = makePlugin('other.plugin', { manifest: { name: 'Beta', description: '无关' } })
    vi.mocked(getInstalledPlugins).mockResolvedValue([first, duplicate, other])
    const { result } = await renderPluginList()

    act(() => result.current.setSearchQuery('天气'))
    expect(result.current.visiblePlugins.map((plugin) => plugin.path)).toEqual([first.path])

    act(() => result.current.setSearchQuery('beta'))
    expect(result.current.visiblePlugins.map((plugin) => plugin.id)).toEqual(['other.plugin'])

    act(() => result.current.setSearchQuery('dup.plugin'))
    expect(result.current.visiblePlugins).toHaveLength(1)
  })

  it('openPluginConfig / closePluginConfig 会改写 URL，关闭后再检查更新', async () => {
    window.history.replaceState(null, '', '/plugin-config/embed?plugin=test.emoji')
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    vi.mocked(getInstalledPlugins).mockResolvedValue([plugin])
    const { result } = await renderPluginList()

    act(() => result.current.openPluginConfig(plugin))
    expect(window.location.pathname).toBe('/plugin-config/embed')
    expect(window.location.search).toBe('?plugin=test.emoji')

    act(() => result.current.closePluginConfig())
    expect(result.current.selectedPlugin).toBeNull()
    expect(result.current.selectedPluginTab).toBeUndefined()
    expect(window.location.pathname).toBe('/plugin-config/embed')
    expect(window.location.search).toBe('')
    await waitFor(() => expect(fetchPluginList).toHaveBeenCalledTimes(1))
  })

  it('performTogglePlugin 按启停结果提示，并在失败时弹出错误', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    const { result } = await renderPluginList()

    vi.mocked(togglePlugin).mockResolvedValueOnce({
      success: true,
      enabled: true,
      message: '',
    })
    await act(async () => {
      await result.current.performTogglePlugin(plugin)
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '插件已启动',
      description: 'Emoji Plugin 状态已更新',
    })
    expect(result.current.actingPluginId).toBeNull()

    vi.mocked(togglePlugin).mockResolvedValueOnce({
      success: true,
      enabled: false,
      message: '自定义关闭说明',
    })
    await act(async () => {
      await result.current.performTogglePlugin(plugin)
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '插件已关闭',
      description: '自定义关闭说明',
    })

    vi.mocked(togglePlugin).mockRejectedValueOnce(new Error('启停接口失败'))
    await act(async () => {
      await result.current.performTogglePlugin(plugin)
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '切换插件状态失败',
      description: '启停接口失败',
      variant: 'destructive',
    })

    vi.mocked(togglePlugin).mockRejectedValueOnce('offline')
    await act(async () => {
      await result.current.performTogglePlugin(plugin)
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '切换插件状态失败',
      description: '未知错误',
      variant: 'destructive',
    })
  })

  it('checkPluginUpdates 会同时按市场 id 与 manifest.id 建索引，0.0.0 版本视为空', async () => {
    const plugin = makePlugin('local.weather', {
      manifest: {
        id: 'market.weather',
        name: 'Weather',
        version: '1.0.0',
      },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([plugin])
    vi.mocked(fetchPluginList).mockResolvedValue([
      makeMarketPlugin('repo.weather', {
        id: 'market.weather',
        version: '2.0.0',
        repository_url: 'https://example.com/weather.git',
      }),
    ])
    vi.mocked(getMaimaiVersion).mockResolvedValue({
      ...defaultMaimaiVersion,
      version: '0.0.0',
    })
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    // 版本被置空后不再做兼容性拦截，只要市场版本更新即可升级
    expect(result.current.getPluginUpdateState(plugin)).toMatchObject({
      canUpdate: true,
      hasUpdate: true,
      latestVersion: '2.0.0',
      title: '发现新版本 v2.0.0',
    })
    expect(result.current.getPluginRepositoryUrl(plugin)).toBe('https://example.com/weather.git')
  })

  it('获取麦麦版本失败时仍写入市场信息，并打出警告', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const plugin = makePlugin('test.emoji', {
      manifest: { repository_url: 'https://example.com/emoji.git' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([plugin])
    vi.mocked(fetchPluginList).mockResolvedValue([
      makeMarketPlugin('test.emoji', { version: '1.2.0' }),
    ])
    vi.mocked(getMaimaiVersion).mockRejectedValue(new Error('version api down'))
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    expect(warn).toHaveBeenCalledWith(
      '获取麦麦版本信息失败，跳过插件更新兼容性检查:',
      expect.any(Error)
    )
    expect(result.current.getPluginUpdateState(plugin).hasUpdate).toBe(true)
    warn.mockRestore()
  })

  it('市场列表拉取失败时清空版本信息，并允许之后重试', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    vi.mocked(fetchPluginList).mockRejectedValueOnce(new Error('market down'))
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))
    expect(warn).toHaveBeenCalledWith('加载插件市场版本信息失败:', expect.any(Error))

    const plugin = result.current.plugins[0]
    expect(result.current.getPluginUpdateState(plugin).title).toBe(
      '插件市场中没有找到该插件，无法判断新版本'
    )

    vi.mocked(fetchPluginList).mockResolvedValueOnce([
      makeMarketPlugin('test.emoji', { version: '1.4.0' }),
    ])
    act(() => result.current.setShowUpdateOnly(true))
    await waitFor(() => expect(fetchPluginList).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))
    expect(result.current.visiblePlugins.map((item) => item.id)).toEqual(['test.emoji'])
    warn.mockRestore()
  })

  it('更新检查启动后再次触发会直接返回，检查中不会重复请求', async () => {
    const deferred = createDeferred<PluginInfo[]>()
    vi.mocked(fetchPluginList).mockReturnValue(deferred.promise)
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    vi.mocked(getInstalledPlugins).mockResolvedValue([plugin])
    const { result } = renderHook(() => usePluginList())
    await waitFor(() => expect(result.current.checkingUpdates).toBe(true))

    act(() => result.current.setShowUpdateOnly(true))
    act(() => result.current.openPluginConfig(plugin, 'general'))
    act(() => result.current.closePluginConfig())
    expect(fetchPluginList).toHaveBeenCalledTimes(1)
    expect(result.current.getPluginUpdateState(plugin)).toEqual({
      canUpdate: false,
      hasUpdate: false,
      title: '正在检查更新',
    })

    await act(async () => {
      deferred.resolve([])
    })
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))
    act(() => result.current.closePluginConfig())
    expect(fetchPluginList).toHaveBeenCalledTimes(1)
  })

  it('仅看有更新时只保留 hasUpdate 插件，市场已有数据时不再重复检查', async () => {
    const updatable = makePlugin('updatable.plugin', {
      manifest: { name: '可更新', version: '1.0.0', repository_url: 'https://example.com/up.git' },
    })
    const latest = makePlugin('latest.plugin', {
      manifest: { name: '已最新', version: '3.0.0', repository_url: 'https://example.com/latest.git' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([updatable, latest])
    vi.mocked(fetchPluginList).mockResolvedValue([
      makeMarketPlugin('updatable.plugin', { version: '1.2.0' }),
      makeMarketPlugin('latest.plugin', { version: '3.0.0' }),
    ])
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    expect(result.current.getPluginUpdateState(latest)).toMatchObject({
      canUpdate: false,
      hasUpdate: false,
      latestVersion: '3.0.0',
      title: '当前已是最新版本',
    })

    act(() => result.current.setShowUpdateOnly(true))
    expect(result.current.showUpdateOnly).toBe(true)
    expect(result.current.visiblePlugins.map((plugin) => plugin.id)).toEqual(['updatable.plugin'])
    expect(fetchPluginList).toHaveBeenCalledTimes(1)
  })

  it('派生状态条、标签、分组和熔断统计', async () => {
    const disabled = makePlugin('p.disabled', {
      enabled: false,
      manifest: { name: 'Disabled' },
    })
    const flaggedDisabled = makePlugin('p.flag', {
      disabled: true,
      manifest: { name: 'FlagDisabled' },
    })
    const circuitOpen = makePlugin('p.open', {
      load_status: 'failed',
      circuit_status: {
        state: 'open',
        remaining_sec: 3.2,
        cooldown_level: 1,
        half_open_inflight: false,
      },
      manifest: { name: 'Open' },
    })
    const circuitOpenIdle = makePlugin('p.open-idle', {
      load_status: 'failed',
      circuit_status: {
        state: 'open',
        remaining_sec: 0,
        cooldown_level: 1,
        half_open_inflight: false,
      },
      manifest: { name: 'OpenIdle' },
    })
    const halfOpen = makePlugin('p.half', {
      load_status: 'failed',
      circuit_status: {
        state: 'half_open',
        remaining_sec: 1,
        cooldown_level: 1,
        half_open_inflight: true,
      },
      manifest: { name: 'Half' },
    })
    const loadingPlugin = makePlugin('p.loading', {
      load_status: 'loading',
      manifest: { name: 'Loading' },
    })
    const failed = makePlugin('p.failed', {
      load_status: 'failed',
      load_error: 'boom',
      manifest: { name: 'Failed' },
    })
    const success = makePlugin('p.success', { manifest: { name: 'Success' } })
    const loadedFlag = makePlugin('p.loaded', {
      load_status: 'unknown',
      loaded: true,
      manifest: { name: 'LoadedFlag' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([
      disabled,
      flaggedDisabled,
      circuitOpen,
      circuitOpenIdle,
      halfOpen,
      loadingPlugin,
      failed,
      success,
      loadedFlag,
    ])
    const { result } = await renderPluginList()

    expect(result.current.getPluginStatusBarClassName(disabled)).toBe('bg-muted-foreground/45')
    expect(result.current.getPluginStatusBarClassName(flaggedDisabled)).toBe('bg-muted-foreground/45')
    expect(result.current.getPluginStatusBarClassName(circuitOpen)).toBe('bg-orange-500')
    expect(result.current.getPluginStatusBarClassName(halfOpen)).toBe('bg-yellow-500')
    expect(result.current.getPluginStatusBarClassName(loadingPlugin)).toBe('bg-sky-500')
    expect(result.current.getPluginStatusBarClassName(failed)).toBe('bg-red-500')
    expect(result.current.getPluginStatusBarClassName(success)).toBe('bg-emerald-500')

    expect(result.current.getPluginStatusLabel(disabled)).toBe('已禁用')
    expect(result.current.getPluginStatusLabel(circuitOpen)).toBe('熔断中 4s')
    expect(result.current.getPluginStatusLabel(circuitOpenIdle)).toBe('熔断中')
    expect(result.current.getPluginStatusLabel(halfOpen)).toBe('半开测试')
    expect(result.current.getPluginStatusLabel(loadingPlugin)).toBe('加载中')
    expect(result.current.getPluginStatusLabel(failed)).toBe('启动失败')
    expect(result.current.getPluginStatusLabel(success)).toBe('已启用')

    expect(result.current.getPluginStatusMeta(disabled)).toEqual({
      dotClassName: 'bg-muted-foreground/45',
      label: '已禁用',
    })
    expect(result.current.getPluginStatusMeta(circuitOpen)).toEqual({
      dotClassName: 'bg-orange-500',
      label: '熔断中 4s',
      badgeClassName: 'border-orange-600 text-orange-600',
      icon: 'circuit',
    })
    expect(result.current.getPluginStatusMeta(halfOpen)).toEqual({
      dotClassName: 'bg-yellow-500',
      label: '半开测试',
      badgeClassName: 'border-yellow-600 text-yellow-700',
      icon: 'warning',
    })
    expect(result.current.getPluginStatusMeta(loadingPlugin)).toEqual({
      dotClassName: 'bg-sky-500',
      label: '加载中',
      badgeClassName: 'border-sky-600 text-sky-600',
      icon: 'loading',
    })
    expect(result.current.getPluginStatusMeta(success)).toEqual({
      dotClassName: 'bg-emerald-500',
      label: '加载成功',
      showsBadge: false,
    })
    expect(result.current.getPluginStatusMeta(loadedFlag)).toEqual({
      dotClassName: 'bg-emerald-500',
      label: '加载成功',
      showsBadge: false,
    })
    expect(result.current.getPluginStatusMeta(failed)).toEqual({
      dotClassName: 'bg-red-500',
      label: '加载失败',
      badgeClassName: 'border-red-600 text-red-600',
      icon: 'warning',
    })

    expect(result.current.visiblePluginGroups.map((group) => group.key)).toEqual([
      'success',
      'loading',
      'failed',
      'disabled',
    ])
    expect(result.current.showsCircuitSummary).toBe(true)
    expect(result.current.circuitOpenCount).toBe(2)
    expect(result.current.modernLoadSummaryLabel).toContain('熔断中 2 个')
    expect(result.current.futureRetroPluginSummaryLabel).toContain('已安装 9 个插件')
    expect(result.current.loadSuccessCount).toBe(2)
    expect(result.current.disabledCount).toBe(2)
  })

  it('版本不兼容判定覆盖错误标记、manifest 版本/host、以及未失败插件', async () => {
    const marker = makePlugin('p.marker', {
      load_status: 'failed',
      load_error: 'manifest 校验失败: Host 版本不兼容',
    })
    const sdkMarker = makePlugin('p.sdk', {
      load_status: 'failed',
      load_error: 'SDK 版本不兼容',
    })
    const manifestMarker = makePlugin('p.mf', {
      load_status: 'failed',
      load_error: 'Manifest 版本不兼容',
    })
    const oldManifest = makePlugin('p.old', {
      load_status: 'failed',
      load_error: '启动失败',
      manifest: { manifest_version: 1 },
    })
    const noHost = makePlugin('p.nohost', {
      load_status: 'failed',
      load_error: '启动失败',
      manifest: {
        host_application: undefined as unknown as InstalledPlugin['manifest']['host_application'],
      },
    })
    const compatibleFailed = makePlugin('p.compat', {
      load_status: 'failed',
      load_error: '普通错误',
    })
    const success = makePlugin('p.ok')
    vi.mocked(getInstalledPlugins).mockResolvedValue([
      marker,
      sdkMarker,
      manifestMarker,
      oldManifest,
      noHost,
      compatibleFailed,
      success,
    ])
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    expect(result.current.isPluginVersionIncompatible(marker)).toBe(true)
    expect(result.current.isPluginVersionIncompatible(sdkMarker)).toBe(true)
    expect(result.current.isPluginVersionIncompatible(manifestMarker)).toBe(true)
    expect(result.current.isPluginVersionIncompatible(oldManifest)).toBe(true)
    expect(result.current.isPluginVersionIncompatible(noHost)).toBe(true)
    expect(result.current.isPluginVersionIncompatible(compatibleFailed)).toBe(false)
    expect(result.current.isPluginVersionIncompatible(success)).toBe(false)
  })

  it('getPluginUpdateState / getPluginRepositoryUrl 覆盖仓库来源与版本比较分支', async () => {
    const noMarket = makePlugin('none.plugin')
    const noRepo = makePlugin('norepo.plugin', { manifest: { version: '1.0.0' } })
    const fromUrls = makePlugin('urls.plugin', {
      manifest: {
        version: '1.0.0',
        urls: { repository: 'https://example.com/from-urls.git' },
      },
    })
    const currentNewer = makePlugin('newer.plugin', {
      manifest: { version: '2.0.0', repository_url: 'https://example.com/newer.git' },
    })
    const incompatibleMarket = makePlugin('incompat.plugin', {
      manifest: { version: '1.0.0', repository_url: 'https://example.com/incompat.git' },
    })
    const marketRepoOnly = makePlugin('market-repo.plugin', {
      manifest: { version: '1.0.0' },
    })
    vi.mocked(getInstalledPlugins).mockResolvedValue([
      noMarket,
      noRepo,
      fromUrls,
      currentNewer,
      incompatibleMarket,
      marketRepoOnly,
    ])
    vi.mocked(fetchPluginList).mockResolvedValue([
      makeMarketPlugin('norepo.plugin', { version: '1.5.0', repository_url: undefined }),
      makeMarketPlugin('urls.plugin', { version: '1.0.0' }),
      makeMarketPlugin('newer.plugin', { version: '1.5.0' }),
      makeMarketPlugin('incompat.plugin', {
        version: '1.4.0',
        manifest_version: 1,
      }),
      makeMarketPlugin('market-repo.plugin', {
        version: '1.2.0',
        repository_url: undefined,
        urls: { repository: 'https://example.com/market-only.git' },
      }),
    ])
    const { result } = await renderPluginList()
    await waitFor(() => expect(result.current.checkingUpdates).toBe(false))

    expect(result.current.getPluginUpdateState(noMarket).title).toBe(
      '插件市场中没有找到该插件，无法判断新版本'
    )
    expect(result.current.getPluginUpdateState(noRepo).title).toBe(
      '插件清单中没有仓库地址，无法更新/升级'
    )
    expect(result.current.getPluginRepositoryUrl(fromUrls)).toBe('https://example.com/from-urls.git')
    expect(result.current.getPluginUpdateState(fromUrls).title).toBe('当前已是最新版本')
    expect(result.current.getPluginUpdateState(currentNewer).title).toBe('当前已是最新版本')
    expect(result.current.getPluginUpdateState(incompatibleMarket)).toEqual({
      canUpdate: false,
      hasUpdate: false,
      latestVersion: '1.4.0',
      title: '插件市场最新版本 v1.4.0 与当前麦麦不兼容',
    })
    expect(result.current.getPluginRepositoryUrl(marketRepoOnly)).toBe(
      'https://example.com/market-only.git'
    )
    expect(result.current.getPluginUpdateState(marketRepoOnly).hasUpdate).toBe(true)
    expect(result.current.isPluginDisabled(noMarket)).toBe(false)
    expect(result.current.isPluginLoadFailed(noMarket)).toBe(false)
  })
})

describe('usePluginLifecycle', () => {
  it('更新对话框：缺仓库、成功、失败、loading 守卫与二次确认短路', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    const getPluginRepositoryUrl = vi.fn<(plugin: InstalledPlugin) => string | undefined>()
    const { result, options } = renderLifecycle({ getPluginRepositoryUrl })
    const event = makeClickEvent()

    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })
    expect(updatePlugin).not.toHaveBeenCalled()

    act(() => result.current.openUpdatePluginDialog(plugin, event))
    expect(event.preventDefault).toHaveBeenCalled()
    expect(event.stopPropagation).toHaveBeenCalled()
    expect(result.current.updateDialogOpen).toBe(true)
    expect(result.current.updatingPlugin?.id).toBe('test.emoji')

    getPluginRepositoryUrl.mockReturnValue(undefined)
    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })
    expect(result.current.updateProgress).toMatchObject({
      operation: 'update',
      stage: 'error',
      message: '插件清单中没有仓库地址，无法更新/升级',
    })
    expect(updatePlugin).not.toHaveBeenCalled()
    expect(options.setActingPluginId).not.toHaveBeenCalled()

    act(() => result.current.closeUpdatePluginDialog())
    act(() => result.current.openUpdatePluginDialog(plugin, makeClickEvent()))
    getPluginRepositoryUrl.mockReturnValue('https://example.com/emoji.git')
    const deferred = createDeferred<{ success: boolean }>()
    vi.mocked(updatePlugin).mockReturnValueOnce(deferred.promise as never)
    await act(async () => {
      void result.current.handleConfirmUpdatePlugin()
    })
    await waitFor(() => expect(result.current.updateProgress?.stage).toBe('loading'))

    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })
    expect(updatePlugin).toHaveBeenCalledTimes(1)
    act(() => result.current.closeUpdatePluginDialog())
    expect(result.current.updateDialogOpen).toBe(true)

    await act(async () => {
      deferred.resolve({ success: true })
    })
    await waitFor(() => expect(result.current.updateProgress?.stage).toBe('success'))
    expect(updatePlugin).toHaveBeenCalledWith('test.emoji', 'https://example.com/emoji.git', 'main')
    expect(toastMock).toHaveBeenCalledWith({
      title: '更新插件成功',
      description: 'Emoji Plugin 已完成更新/升级',
    })
    expect(options.onChanged).toHaveBeenCalledTimes(1)
    expect(options.setActingPluginId).toHaveBeenLastCalledWith(null)

    act(() => result.current.closeUpdatePluginDialog())
    expect(result.current.updateDialogOpen).toBe(false)
    expect(result.current.updatingPlugin).toBeNull()

    act(() => result.current.openUpdatePluginDialog(plugin, makeClickEvent()))
    vi.mocked(updatePlugin).mockRejectedValueOnce(new Error('仓库拉取失败'))
    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })
    expect(result.current.updateProgress?.stage).toBe('error')
    expect(toastMock).toHaveBeenCalledWith({
      title: '更新插件失败',
      description: '仓库拉取失败',
      variant: 'destructive',
    })

    act(() => result.current.openUpdatePluginDialog(plugin, makeClickEvent()))
    vi.mocked(updatePlugin).mockRejectedValueOnce('offline')
    await act(async () => {
      await result.current.handleConfirmUpdatePlugin()
    })
    expect(result.current.updateProgress).toMatchObject({
      stage: 'error',
      message: '未知错误',
      error: '未知错误',
    })
  })

  it('卸载对话框：成功、失败、loading 关闭守卫与未选中短路', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    const { result, options } = renderLifecycle()
    const event = makeClickEvent()

    await act(async () => {
      await result.current.handleConfirmDeletePlugin()
    })
    expect(uninstallPlugin).not.toHaveBeenCalled()

    act(() => result.current.openDeletePluginDialog(plugin, event))
    expect(event.preventDefault).toHaveBeenCalled()
    expect(result.current.deleteDialogOpen).toBe(true)

    const deferred = createDeferred<{ success: boolean; message: string }>()
    vi.mocked(uninstallPlugin).mockReturnValueOnce(deferred.promise)
    await act(async () => {
      void result.current.handleConfirmDeletePlugin()
    })
    await waitFor(() => expect(result.current.deleteProgress?.stage).toBe('loading'))
    act(() => result.current.closeDeletePluginDialog())
    expect(result.current.deleteDialogOpen).toBe(true)

    await act(async () => {
      deferred.resolve({ success: true, message: 'ok' })
    })
    await waitFor(() => expect(result.current.deleteProgress?.stage).toBe('success'))
    expect(uninstallPlugin).toHaveBeenCalledWith('test.emoji')
    expect(toastMock).toHaveBeenCalledWith({
      title: '删除插件成功',
      description: 'Emoji Plugin 已删除',
    })
    expect(options.onChanged).toHaveBeenCalledTimes(1)

    act(() => result.current.closeDeletePluginDialog())
    expect(result.current.deleteDialogOpen).toBe(false)
    expect(result.current.deletingPlugin).toBeNull()

    act(() => result.current.openDeletePluginDialog(plugin, makeClickEvent()))
    vi.mocked(uninstallPlugin).mockRejectedValueOnce(new Error('仍被占用'))
    await act(async () => {
      await result.current.handleConfirmDeletePlugin()
    })
    expect(result.current.deleteProgress).toMatchObject({
      stage: 'error',
      message: '仍被占用',
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '删除插件失败',
      description: '仍被占用',
      variant: 'destructive',
    })

    act(() => result.current.openDeletePluginDialog(plugin, makeClickEvent()))
    vi.mocked(uninstallPlugin).mockRejectedValueOnce('offline')
    await act(async () => {
      await result.current.handleConfirmDeletePlugin()
    })
    expect(result.current.deleteProgress?.message).toBe('未知错误')
  })

  it('进度订阅只写入匹配的更新/卸载插件，卸载后忽略后续事件', async () => {
    const plugin = makePlugin('test.emoji', { manifest: { name: 'Emoji Plugin' } })
    const other = makePlugin('other.plugin')
    const { result, unmount } = renderLifecycle()
    await waitFor(() => expect(progressClient.subscribe).toHaveBeenCalled())

    act(() => result.current.openUpdatePluginDialog(plugin, makeClickEvent()))
    act(() => result.current.openDeletePluginDialog(plugin, makeClickEvent()))
    await waitFor(() => expect(progressClient.listener).toBeTruthy())

    act(() => {
      progressClient.emit(
        makeProgress({ operation: 'update', stage: 'loading', plugin_id: 'test.emoji', progress: 40 })
      )
      progressClient.emit(
        makeProgress({
          operation: 'uninstall',
          stage: 'loading',
          plugin_id: 'test.emoji',
          progress: 55,
        })
      )
      progressClient.emit(
        makeProgress({ operation: 'update', stage: 'loading', plugin_id: other.id, progress: 99 })
      )
      progressClient.emit(
        makeProgress({ operation: 'install', stage: 'loading', plugin_id: 'test.emoji', progress: 80 })
      )
    })

    expect(result.current.updateProgress).toMatchObject({
      operation: 'update',
      progress: 40,
      plugin_id: 'test.emoji',
    })
    expect(result.current.deleteProgress).toMatchObject({
      operation: 'uninstall',
      progress: 55,
      plugin_id: 'test.emoji',
    })

    unmount()
    expect(progressClient.cleanup).toHaveBeenCalled()
    expect(() =>
      progressClient.emit(makeProgress({ operation: 'update', stage: 'success', progress: 100 }))
    ).not.toThrow()
  })

  it('订阅尚未完成就卸载时，会在 resolve 后立刻 cleanup', async () => {
    const deferred = createDeferred<() => Promise<void>>()
    progressClient.subscribe.mockImplementationOnce(() => deferred.promise)
    const { unmount } = renderLifecycle()
    unmount()

    const cleanup = vi.fn(async () => undefined)
    await act(async () => {
      deferred.resolve(cleanup)
    })
    expect(cleanup).toHaveBeenCalled()
  })
})

describe('usePluginConfigEditor', () => {
  it('加载配置成功，失败时按 Error / 非 Error 弹出 toast', async () => {
    const { result } = await renderEditor(makePlugin('test.emoji'), vi.fn(), 'advanced')
    expect(result.current.schema?.plugin_id).toBe('test.emoji')
    expect(result.current.config).toEqual({ general: { name: 'old' } })
    expect(result.current.sourceCode).toBe('name = "old"\n')
    expect(result.current.activeConfigTab).toBe('advanced')
    expect(result.current.hasChanges).toBe(false)

    vi.mocked(getPluginConfigBundle).mockRejectedValueOnce(new Error('配置丢失'))
    const failed = renderHook(() =>
      usePluginConfigEditor({
        plugin: makePlugin('broken.plugin'),
        onBack: vi.fn(),
      })
    )
    await waitFor(() => expect(failed.result.current.loading).toBe(false))
    expect(toastMock).toHaveBeenCalledWith({
      title: '加载配置失败',
      description: '配置丢失',
      variant: 'destructive',
    })

    vi.mocked(getPluginConfigBundle).mockRejectedValueOnce('offline')
    const failedUnknown = renderHook(() =>
      usePluginConfigEditor({
        plugin: makePlugin('broken.plugin'),
        onBack: vi.fn(),
      })
    )
    await waitFor(() => expect(failedUnknown.result.current.loading).toBe(false))
    expect(toastMock).toHaveBeenCalledWith({
      title: '加载配置失败',
      description: '未知错误',
      variant: 'destructive',
    })
  })

  it('可视化草稿变更后保存，并同步嵌套字段', async () => {
    const { result } = await renderEditor()
    expect(blockerState.lastOptions?.shouldBlockFn()).toBe(false)
    expect(blockerState.lastOptions?.enableBeforeUnload).toBe(false)

    act(() => result.current.handleFieldChange('general', 'name', 'new'))
    expect(result.current.config).toEqual({ general: { name: 'new' } })
    expect(result.current.hasChanges).toBe(true)
    expect(blockerState.lastOptions?.shouldBlockFn()).toBe(true)
    expect(blockerState.lastOptions?.enableBeforeUnload).toBe(true)

    await act(async () => {
      await expect(result.current.handleSave()).resolves.toBe(true)
    })
    expect(updatePluginConfig).toHaveBeenCalledWith('test.emoji', { general: { name: 'new' } })
    expect(result.current.hasChanges).toBe(false)
    expect(toastMock).toHaveBeenCalledWith({
      title: '配置已保存',
      description: '更改将在插件重新加载后生效',
    })
  })

  it('源码模式保存成功、TOML 解析失败、API 失败，以及改稿后清除 toml 错误', async () => {
    const { result } = await renderEditor()

    act(() => result.current.setEditMode('source'))
    act(() => result.current.handleSourceCodeChange('name = "changed"\n'))
    expect(result.current.hasChanges).toBe(true)

    await act(async () => {
      await expect(result.current.handleSave()).resolves.toBe(true)
    })
    expect(updatePluginConfigRaw).toHaveBeenCalledWith('test.emoji', 'name = "changed"\n')
    expect(result.current.hasChanges).toBe(false)
    expect(result.current.hasTomlError).toBe(false)

    act(() => result.current.handleSourceCodeChange('[[[not-toml'))
    await act(async () => {
      await expect(result.current.handleSave()).resolves.toBe(false)
    })
    expect(result.current.hasTomlError).toBe(true)
    expect(updatePluginConfigRaw).toHaveBeenCalledTimes(1)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'TOML 格式错误',
        variant: 'destructive',
      })
    )

    act(() => result.current.handleSourceCodeChange('name = "fixed"\n'))
    expect(result.current.hasTomlError).toBe(false)

    vi.mocked(updatePluginConfigRaw).mockRejectedValueOnce(new Error('写入失败'))
    await act(async () => {
      await expect(result.current.handleSave()).resolves.toBe(false)
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '保存失败',
      description: '写入失败',
      variant: 'destructive',
    })

    vi.mocked(updatePluginConfigRaw).mockRejectedValueOnce('offline')
    await act(async () => {
      await expect(result.current.handleSave()).resolves.toBe(false)
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '保存失败',
      description: '未知错误',
      variant: 'destructive',
    })
  })

  it('离开拦截：返回确认、取消、不保存离开、保存后离开与保存失败短路', async () => {
    const { result, onBack } = await renderEditor()

    act(() => result.current.handleBack())
    expect(onBack).toHaveBeenCalledTimes(1)
    expect(result.current.internalLeavePromptOpen).toBe(false)

    act(() => result.current.handleFieldChange('general', 'name', 'dirty'))
    act(() => result.current.handleBack())
    expect(onBack).toHaveBeenCalledTimes(1)
    expect(result.current.internalLeavePromptOpen).toBe(true)

    blockerState.status = 'blocked'
    act(() => result.current.closeLeavePrompt())
    expect(blockerState.reset).toHaveBeenCalled()
    expect(result.current.internalLeavePromptOpen).toBe(false)

    act(() => result.current.handleBack())
    act(() => result.current.leaveWithoutSaving())
    expect(onBack).toHaveBeenCalledTimes(2)
    expect(result.current.internalLeavePromptOpen).toBe(false)

    act(() => result.current.handleFieldChange('general', 'name', 'dirty-2'))
    vi.mocked(updatePluginConfig).mockRejectedValueOnce(new Error('保存被拒'))
    act(() => result.current.handleBack())
    await act(async () => {
      await result.current.saveAndLeave()
    })
    expect(onBack).toHaveBeenCalledTimes(2)
    expect(result.current.internalLeavePromptOpen).toBe(true)

    vi.mocked(updatePluginConfig).mockResolvedValueOnce({ success: true, message: 'ok' })
    await act(async () => {
      await result.current.saveAndLeave()
    })
    expect(onBack).toHaveBeenCalledTimes(3)
    expect(result.current.internalLeavePromptOpen).toBe(false)

    // 路由 blocker 路径：内部确认框未打开时走 proceed
    act(() => result.current.handleFieldChange('general', 'name', 'dirty-3'))
    blockerState.status = 'blocked'
    act(() => result.current.leaveWithoutSaving())
    expect(blockerState.proceed).toHaveBeenCalled()

    act(() => result.current.handleFieldChange('general', 'name', 'dirty-4'))
    blockerState.status = 'blocked'
    await act(async () => {
      await result.current.saveAndLeave()
    })
    expect(blockerState.proceed).toHaveBeenCalledTimes(2)
  })

  it('重置与启停成功会重新加载配置，失败则 toast', async () => {
    const { result } = await renderEditor()

    await act(async () => {
      await result.current.handleReset()
    })
    expect(resetPluginConfig).toHaveBeenCalledWith('test.emoji')
    expect(toastMock).toHaveBeenCalledWith({
      title: '配置已重置',
      description: '下次加载插件时将使用默认配置',
    })
    expect(result.current.resetDialogOpen).toBe(false)
    expect(getPluginConfigBundle).toHaveBeenCalledTimes(2)

    vi.mocked(resetPluginConfig).mockRejectedValueOnce(new Error('重置接口失败'))
    await act(async () => {
      await result.current.handleReset()
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '重置失败',
      description: '重置接口失败',
      variant: 'destructive',
    })

    vi.mocked(resetPluginConfig).mockRejectedValueOnce('offline')
    await act(async () => {
      await result.current.handleReset()
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '重置失败',
      description: '未知错误',
      variant: 'destructive',
    })

    vi.mocked(togglePlugin).mockResolvedValueOnce({
      success: true,
      enabled: false,
      message: '已关闭',
      note: '需要重启',
    })
    await act(async () => {
      await result.current.handleToggle()
    })
    expect(togglePlugin).toHaveBeenCalledWith('test.emoji')
    expect(toastMock).toHaveBeenCalledWith({
      title: '已关闭',
      description: '需要重启',
    })
    expect(getPluginConfigBundle).toHaveBeenCalledTimes(3)

    vi.mocked(togglePlugin).mockRejectedValueOnce(new Error('切换失败'))
    await act(async () => {
      await result.current.handleToggle()
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '切换状态失败',
      description: '切换失败',
      variant: 'destructive',
    })

    vi.mocked(togglePlugin).mockRejectedValueOnce('offline')
    await act(async () => {
      await result.current.handleToggle()
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '切换状态失败',
      description: '未知错误',
      variant: 'destructive',
    })
  })

  it('切换配置页签会把 plugin 与 tab 写回当前路由', async () => {
    window.history.replaceState(null, '', '/adapter-management?plugin=test.emoji')
    const { result } = await renderEditor()

    act(() => result.current.handleConfigTabChange('details'))
    expect(result.current.activeConfigTab).toBe('details')
    expect(window.location.pathname).toBe('/adapter-management')
    expect(window.location.search).toBe('?plugin=test.emoji&tab=details')
  })
})
