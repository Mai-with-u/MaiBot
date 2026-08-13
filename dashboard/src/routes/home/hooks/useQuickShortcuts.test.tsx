import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getInstalledPlugins, getPluginConfigSchema } from '@/lib/plugin-api'
import type { InstalledPlugin, PluginConfigSchema } from '@/lib/plugin-api'

import { useQuickShortcuts } from './useQuickShortcuts'

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t }) }
})

vi.mock('@/lib/plugin-api', () => ({
  getInstalledPlugins: vi.fn().mockResolvedValue([]),
  getPluginConfigSchema: vi.fn(),
}))

const getInstalledPluginsMock = vi.mocked(getInstalledPlugins)
const getPluginConfigSchemaMock = vi.mocked(getPluginConfigSchema)

const STORAGE_KEY = 'maibot-home-quick-shortcuts'
const DEFAULT_IDS = ['action:restart', 'action:expression-review', 'route:logs']
const SIDEBAR_REDUNDANT_IDS = [
  'route:plugin-market',
  'route:plugin-config',
  'route:model-providers',
  'route:bot-config',
  'route:emoji',
  'route:expression',
]
const BUILTIN_OPTION_IDS = [
  'action:restart',
  'action:expression-review',
  'route:logs',
  'route:settings-appearance',
  'route:settings-local-cache',
  'route:model-list',
  'route:model-tasks',
]

function createDeferred<T>() {
  let resolve: (value: T | PromiseLike<T>) => void = () => {}
  let reject: (reason?: unknown) => void = () => {}
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function createPlugin(
  id: string,
  options?: {
    name?: string
    enabled?: boolean
    disabled?: boolean
  }
): InstalledPlugin {
  return {
    id,
    manifest: {
      manifest_version: 1,
      name: options?.name ?? id,
      version: '1.0.0',
      description: '',
      author: { name: 'tester' },
      license: 'MIT',
      host_application: { min_version: '0.0.0' },
    },
    path: `/plugins/${id}`,
    enabled: options?.enabled,
    disabled: options?.disabled,
  }
}

function createSchema(
  pluginId: string,
  layout: PluginConfigSchema['layout']
): PluginConfigSchema {
  return {
    plugin_id: pluginId,
    plugin_info: {
      name: pluginId,
      version: '1.0.0',
      description: '',
      author: 'tester',
    },
    sections: {},
    layout,
  }
}

function createTabsSchema(
  pluginId: string,
  tabs: Array<{ id: string; title?: string }>
): PluginConfigSchema {
  return createSchema(pluginId, {
    type: 'tabs',
    tabs: tabs.map((tab, order) => ({
      id: tab.id,
      title: tab.title ?? '',
      sections: [],
      order,
    })),
  })
}

function renderQuickShortcuts(
  overrides?: Partial<Parameters<typeof useQuickShortcuts>[0]>
) {
  return renderHook(
    (props: Parameters<typeof useQuickShortcuts>[0]) => useQuickShortcuts(props),
    {
      initialProps: {
        isRestarting: false,
        handleRestart: vi.fn(),
        uncheckedCount: 0,
        onOpenReviewer: vi.fn(),
        ...overrides,
      },
    }
  )
}

beforeEach(() => {
  localStorage.clear()
  getInstalledPluginsMock.mockReset()
  getPluginConfigSchemaMock.mockReset()
  getInstalledPluginsMock.mockResolvedValue([])
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useQuickShortcuts', () => {
  it('默认快捷操作不包含侧边栏可一步到达的入口', () => {
    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual(DEFAULT_IDS)
    expect(result.current.filteredQuickShortcutOptions.map((item) => item.id)).not.toEqual(
      expect.arrayContaining(SIDEBAR_REDUNDANT_IDS)
    )
    expect(getInstalledPluginsMock).not.toHaveBeenCalled()
  })

  it('读取旧配置时移除重复入口并同步迁移本地存储', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        'route:plugin-market',
        'route:model-list',
        'route:bot-config',
        'action:restart',
      ])
    )

    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual(['route:model-list', 'action:restart'])
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual([
      'route:model-list',
      'action:restart',
    ])
  })

  it('旧配置只含重复入口时恢复为新的默认快捷操作', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(SIDEBAR_REDUNDANT_IDS))

    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual(DEFAULT_IDS)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual(DEFAULT_IDS)
  })

  it('非法或非数组本地存储回退到默认快捷操作且不改写存储', () => {
    localStorage.setItem(STORAGE_KEY, '{broken')
    const setItem = vi.spyOn(localStorage, 'setItem')

    const { result, unmount } = renderQuickShortcuts()
    expect(result.current.quickShortcutIds).toEqual(DEFAULT_IDS)
    expect(setItem).not.toHaveBeenCalled()
    unmount()

    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ids: ['route:logs'] }))
    const { result: objectResult } = renderQuickShortcuts()
    expect(objectResult.current.quickShortcutIds).toEqual(DEFAULT_IDS)
    expect(localStorage.getItem(STORAGE_KEY)).toBe(JSON.stringify({ ids: ['route:logs'] }))
  })

  it('空数组、空串与非字符串项会被过滤，必要时回写默认值', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([]))
    const { result: emptyResult, unmount } = renderQuickShortcuts()
    expect(emptyResult.current.quickShortcutIds).toEqual(DEFAULT_IDS)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual(DEFAULT_IDS)
    unmount()

    localStorage.setItem(STORAGE_KEY, JSON.stringify(['', null, 12, 'route:logs', 'route:logs']))
    const setItem = vi.spyOn(localStorage, 'setItem')
    const { result } = renderQuickShortcuts()
    // 去重后只剩有效 id，与侧边栏过滤结果一致，无需回写
    expect(result.current.quickShortcutIds).toEqual(['route:logs'])
    expect(setItem).not.toHaveBeenCalled()
  })

  it('合法且无冗余的旧配置不会触发迁移回写', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['route:logs', 'action:restart']))
    const setItem = vi.spyOn(localStorage, 'setItem')

    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual(['route:logs', 'action:restart'])
    expect(setItem).not.toHaveBeenCalled()
  })

  it('toggle 可增删并去重，reset 恢复默认并写入本地存储', () => {
    const { result } = renderQuickShortcuts()

    act(() => {
      result.current.toggleQuickShortcut('route:model-list', true)
    })
    expect(result.current.quickShortcutIds).toEqual([...DEFAULT_IDS, 'route:model-list'])
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual([
      ...DEFAULT_IDS,
      'route:model-list',
    ])

    act(() => {
      result.current.toggleQuickShortcut('action:restart', true)
    })
    expect(result.current.quickShortcutIds).toEqual([...DEFAULT_IDS, 'route:model-list'])

    act(() => {
      result.current.toggleQuickShortcut('action:restart', false)
    })
    expect(result.current.quickShortcutIds).toEqual([
      'action:expression-review',
      'route:logs',
      'route:model-list',
    ])

    act(() => {
      result.current.resetQuickShortcuts()
    })
    expect(result.current.quickShortcutIds).toEqual(DEFAULT_IDS)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).toEqual(DEFAULT_IDS)
  })

  it('重启中切换文案并禁用，未审核数量按 99+ 截断', () => {
    const handleRestart = vi.fn()
    const onOpenReviewer = vi.fn()
    const { result, rerender } = renderQuickShortcuts({
      isRestarting: true,
      handleRestart,
      uncheckedCount: 100,
      onOpenReviewer,
    })

    const restarting = result.current.filteredQuickShortcutOptions.find(
      (item) => item.id === 'action:restart'
    )
    const reviewOverflow = result.current.filteredQuickShortcutOptions.find(
      (item) => item.id === 'action:expression-review'
    )
    expect(restarting?.label).toBe('home.quickActions.restarting')
    expect(restarting?.disabled).toBe(true)
    expect(reviewOverflow?.badge).toBe('99+')

    void restarting?.action?.()
    void reviewOverflow?.action?.()
    expect(handleRestart).toHaveBeenCalledTimes(1)
    expect(onOpenReviewer).toHaveBeenCalledTimes(1)

    rerender({
      isRestarting: false,
      handleRestart,
      uncheckedCount: 7,
      onOpenReviewer,
    })
    const restart = result.current.filteredQuickShortcutOptions.find(
      (item) => item.id === 'action:restart'
    )
    const review = result.current.filteredQuickShortcutOptions.find(
      (item) => item.id === 'action:expression-review'
    )
    expect(restart?.label).toBe('home.quickActions.restart')
    expect(restart?.disabled).toBe(false)
    expect(review?.badge).toBe('7')

    rerender({
      isRestarting: false,
      handleRestart,
      uncheckedCount: 0,
      onOpenReviewer,
    })
    expect(
      result.current.filteredQuickShortcutOptions.find(
        (item) => item.id === 'action:expression-review'
      )?.badge
    ).toBeUndefined()
  })

  it('搜索按标签与描述过滤，空白查询返回全部内置项', () => {
    const { result } = renderQuickShortcuts()

    expect(result.current.filteredQuickShortcutOptions.map((item) => item.id)).toEqual(
      BUILTIN_OPTION_IDS
    )

    act(() => {
      result.current.setQuickShortcutSearch('  VIEWLOGS  ')
    })
    expect(result.current.filteredQuickShortcutOptions.map((item) => item.id)).toEqual([
      'route:logs',
    ])

    act(() => {
      result.current.setQuickShortcutSearch('   ')
    })
    expect(result.current.filteredQuickShortcutOptions.map((item) => item.id)).toEqual(
      BUILTIN_OPTION_IDS
    )
  })

  it('未知非插件 id 不会进入已选列表', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['route:logs', 'mystery:unknown']))

    const { result } = renderQuickShortcuts()

    expect(result.current.quickShortcutIds).toEqual(['route:logs', 'mystery:unknown'])
    expect(result.current.selectedQuickShortcuts.map((item) => item.id)).toEqual(['route:logs'])
  })

  it('对话框打开时加载已启用插件并去重，禁用项不出现', async () => {
    getInstalledPluginsMock.mockResolvedValue([
      createPlugin('demo', { name: 'Demo' }),
      createPlugin('off', { enabled: false }),
      createPlugin('disabled', { disabled: true }),
      createPlugin('demo', { name: 'Demo Duplicate' }),
      createPlugin('org/pkg', { name: '' }),
    ])

    const { result } = renderQuickShortcuts()

    act(() => {
      result.current.setQuickShortcutDialogOpen(true)
    })

    await waitFor(() => {
      expect(
        result.current.filteredQuickShortcutOptions.some((item) => item.id === 'plugin-config:demo')
      ).toBe(true)
    })
    expect(result.current.isPluginShortcutsLoading).toBe(false)

    const pluginOptions = result.current.filteredQuickShortcutOptions.filter(
      (item) => item.category === 'plugin'
    )
    expect(pluginOptions.map((item) => item.id)).toEqual([
      'plugin-config:demo',
      'plugin-config:org%2Fpkg',
    ])
    expect(pluginOptions[0]).toMatchObject({
      label: 'home.pluginShortcuts.baseLabel',
      description: 'home.pluginShortcuts.baseDescription',
      href: '/plugin-config?plugin=demo',
    })
    expect(pluginOptions[1]).toMatchObject({
      href: '/plugin-config?plugin=org%2Fpkg',
    })
  })

  it('对话框关闭时只保留已选插件入口，并为缺失页签提供 fallback', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        'plugin-config:demo',
        'plugin-config:demo:tab:general',
        'plugin-config:missing:tab:gone',
        'plugin-config:',
      ])
    )
    getInstalledPluginsMock.mockResolvedValue([
      createPlugin('demo', { name: 'Demo' }),
      createPlugin('other', { name: 'Other' }),
    ])
    getPluginConfigSchemaMock.mockImplementation(async (pluginId) => {
      if (pluginId === 'demo') {
        return createTabsSchema('demo', [
          { id: 'general', title: '常规' },
          { id: 'advanced', title: '高级' },
        ])
      }
      return createTabsSchema(pluginId, [{ id: 'gone', title: 'Gone' }])
    })

    const { result } = renderQuickShortcuts()

    await waitFor(() => {
      expect(result.current.isPluginShortcutsLoading).toBe(false)
      expect(result.current.selectedQuickShortcuts.map((item) => item.id)).toEqual([
        'plugin-config:demo',
        'plugin-config:demo:tab:general',
        'plugin-config:missing:tab:gone',
      ])
      expect(
        result.current.filteredQuickShortcutOptions
          .filter((item) => item.category === 'plugin')
          .map((item) => item.id)
      ).toEqual(['plugin-config:demo', 'plugin-config:demo:tab:general'])
    })

    const selectedIds = result.current.filteredQuickShortcutOptions
      .filter((item) => item.category === 'plugin')
      .map((item) => item.id)
    expect(selectedIds).not.toContain('plugin-config:other')

    const tabShortcut = result.current.selectedQuickShortcuts.find(
      (item) => item.id === 'plugin-config:demo:tab:general'
    )
    expect(tabShortcut).toMatchObject({
      label: 'Demo / 常规',
      description: 'home.pluginShortcuts.tabDescription',
      href: '/plugin-config?plugin=demo&tab=general',
    })

    const fallback = result.current.selectedQuickShortcuts.find(
      (item) => item.id === 'plugin-config:missing:tab:gone'
    )
    expect(fallback).toMatchObject({
      label: 'home.pluginShortcuts.fallbackTabLabel',
      description: 'home.pluginShortcuts.fallbackTabDescription',
      href: '/plugin-config?plugin=missing&tab=gone',
    })
  })

  it('非 tabs 布局或 schema 拉取失败时页签入口走 fallback', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([
        'plugin-config:auto:tab:hidden',
        'plugin-config:broken:tab:settings',
      ])
    )
    getInstalledPluginsMock.mockResolvedValue([
      createPlugin('auto'),
      createPlugin('broken'),
    ])
    getPluginConfigSchemaMock.mockImplementation(async (pluginId) => {
      if (pluginId === 'auto') {
        return createSchema('auto', { type: 'auto', tabs: [{ id: 'hidden', title: 'Hidden', sections: [], order: 0 }] })
      }
      throw new Error('schema 不可用')
    })

    const { result } = renderQuickShortcuts()

    await waitFor(() => {
      expect(getPluginConfigSchemaMock).toHaveBeenCalled()
      expect(result.current.isPluginShortcutsLoading).toBe(false)
    })

    expect(
      result.current.filteredQuickShortcutOptions
        .filter((item) => item.category === 'plugin')
        .map((item) => item.id)
    ).toEqual(['plugin-config:auto', 'plugin-config:broken'])
    expect(result.current.selectedQuickShortcuts.map((item) => item.id)).toEqual([
      'plugin-config:auto:tab:hidden',
      'plugin-config:broken:tab:settings',
    ])
    expect(
      result.current.selectedQuickShortcuts.find((item) => item.id === 'plugin-config:auto:tab:hidden')
    ).toMatchObject({
      label: 'home.pluginShortcuts.fallbackTabLabel',
      href: '/plugin-config?plugin=auto&tab=hidden',
    })
  })

  it('schema.layout 访问异常时吞掉单个插件页签错误并保留基础入口', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['plugin-config:demo:tab:general']))
    getInstalledPluginsMock.mockResolvedValue([createPlugin('demo')])
    getPluginConfigSchemaMock.mockResolvedValue({ layout: null } as unknown as PluginConfigSchema)

    const { result } = renderQuickShortcuts()

    await waitFor(() => {
      expect(warn).toHaveBeenCalledWith(
        '加载插件 demo 已选配置页签快捷入口失败:',
        expect.any(Error)
      )
    })
    expect(
      result.current.filteredQuickShortcutOptions.some((item) => item.id === 'plugin-config:demo')
    ).toBe(true)
    expect(
      result.current.selectedQuickShortcuts.find((item) => item.id === 'plugin-config:demo:tab:general')
    ).toMatchObject({
      label: 'home.pluginShortcuts.fallbackTabLabel',
    })
    warn.mockRestore()
  })

  it('已禁用插件的已选页签不会生成真实入口，仅保留 fallback', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['plugin-config:off:tab:general']))
    getInstalledPluginsMock.mockResolvedValue([createPlugin('off', { enabled: false })])
    getPluginConfigSchemaMock.mockResolvedValue(
      createTabsSchema('off', [{ id: 'general', title: '常规' }])
    )

    const { result } = renderQuickShortcuts()

    await waitFor(() => {
      expect(getInstalledPluginsMock).toHaveBeenCalled()
      expect(result.current.isPluginShortcutsLoading).toBe(false)
    })

    expect(
      result.current.filteredQuickShortcutOptions.filter((item) => item.category === 'plugin')
    ).toEqual([])
    expect(getPluginConfigSchemaMock).not.toHaveBeenCalled()
    expect(result.current.selectedQuickShortcuts).toEqual([
      expect.objectContaining({
        id: 'plugin-config:off:tab:general',
        label: 'home.pluginShortcuts.fallbackTabLabel',
      }),
    ])
  })

  it('无页签标记的异常 plugin-config id 走无 tab 的 fallback', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['plugin-config:demo:other:x']))
    getInstalledPluginsMock.mockResolvedValue([createPlugin('demo')])

    const { result } = renderQuickShortcuts()

    await waitFor(() => {
      expect(
        result.current.filteredQuickShortcutOptions.some((item) => item.id === 'plugin-config:demo')
      ).toBe(true)
    })

    const fallback = result.current.selectedQuickShortcuts.find(
      (item) => item.id === 'plugin-config:demo:other:x'
    )
    expect(fallback).toMatchObject({
      label: 'home.pluginShortcuts.fallbackLabel',
      description: 'home.pluginShortcuts.fallbackDescription',
      href: '/plugin-config?plugin=demo',
    })
  })

  it('页签标题为空时回退到 tab.id，并编码特殊字符', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(['plugin-config:org%2Fpkg:tab:a%20b'])
    )
    getInstalledPluginsMock.mockResolvedValue([createPlugin('org/pkg', { name: 'Org Pkg' })])
    getPluginConfigSchemaMock.mockResolvedValue(
      createTabsSchema('org/pkg', [{ id: 'a b' }])
    )

    const { result } = renderQuickShortcuts()

    await waitFor(() => {
      expect(
        result.current.filteredQuickShortcutOptions.some(
          (item) => item.id === 'plugin-config:org%2Fpkg:tab:a%20b'
        )
      ).toBe(true)
    })

    expect(
      result.current.selectedQuickShortcuts.find(
        (item) => item.id === 'plugin-config:org%2Fpkg:tab:a%20b'
      )
    ).toMatchObject({
      label: 'Org Pkg / a b',
      href: '/plugin-config?plugin=org%2Fpkg&tab=a+b',
    })
  })

  it('已安装但未加载到选项中的插件 id 使用无 tab fallback', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['plugin-config:ghost']))

    const { result } = renderQuickShortcuts()

    await waitFor(() => {
      expect(getInstalledPluginsMock).toHaveBeenCalled()
      expect(result.current.isPluginShortcutsLoading).toBe(false)
    })

    expect(result.current.selectedQuickShortcuts).toEqual([
      expect.objectContaining({
        id: 'plugin-config:ghost',
        label: 'home.pluginShortcuts.fallbackLabel',
        description: 'home.pluginShortcuts.fallbackDescription',
        href: '/plugin-config?plugin=ghost',
      }),
    ])
  })

  it('getInstalledPlugins 失败时记录错误并结束加载', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    getInstalledPluginsMock.mockRejectedValue(new Error('插件列表失败'))

    const { result } = renderQuickShortcuts()
    act(() => {
      result.current.setQuickShortcutDialogOpen(true)
    })

    await waitFor(() => {
      expect(error).toHaveBeenCalledWith('加载插件快捷入口失败:', expect.any(Error))
    })
    expect(result.current.isPluginShortcutsLoading).toBe(false)
    expect(
      result.current.filteredQuickShortcutOptions.filter((item) => item.category === 'plugin')
    ).toEqual([])
    error.mockRestore()
  })

  it('卸载或关闭对话框会取消过期的插件加载结果', async () => {
    const first = createDeferred<InstalledPlugin[]>()
    getInstalledPluginsMock.mockReturnValueOnce(first.promise)

    const { result } = renderQuickShortcuts()
    act(() => {
      result.current.setQuickShortcutDialogOpen(true)
    })
    await waitFor(() => {
      expect(result.current.isPluginShortcutsLoading).toBe(true)
    })

    act(() => {
      result.current.setQuickShortcutDialogOpen(false)
    })
    expect(result.current.isPluginShortcutsLoading).toBe(false)

    await act(async () => {
      first.resolve([createPlugin('late')])
      await first.promise
    })

    expect(
      result.current.filteredQuickShortcutOptions.some((item) => item.id === 'plugin-config:late')
    ).toBe(false)
  })

  it('切换已选页签会取消进行中的 schema 请求，避免过期页签写入', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(['plugin-config:demo:tab:stale']))
    getInstalledPluginsMock.mockResolvedValue([createPlugin('demo')])
    const schema = createDeferred<PluginConfigSchema>()
    getPluginConfigSchemaMock.mockReturnValueOnce(schema.promise)

    const { result } = renderQuickShortcuts()
    await waitFor(() => {
      expect(
        result.current.filteredQuickShortcutOptions.some((item) => item.id === 'plugin-config:demo')
      ).toBe(true)
    })

    act(() => {
      result.current.toggleQuickShortcut('plugin-config:demo:tab:stale', false)
    })

    await act(async () => {
      schema.resolve(createTabsSchema('demo', [{ id: 'stale', title: '过期页签' }]))
      await schema.promise
    })

    expect(
      result.current.filteredQuickShortcutOptions.some(
        (item) => item.id === 'plugin-config:demo:tab:stale'
      )
    ).toBe(false)
  })
})
