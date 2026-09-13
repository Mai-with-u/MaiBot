import type { ComponentProps } from 'react'
import type { PluginManifest } from '@/types/plugin'
import type {
  GitStatus,
  MaimaiVersion,
  PluginInfo,
  PluginLoadProgress,
  PluginStatsData,
} from '../types'

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { UpdatesTab } from '../UpdatesTab'

vi.mock('@/lib/plugin-api', () => ({
  checkGitStatus: vi.fn(),
  checkPluginInstalled: vi.fn(),
  connectPluginProgressWebSocket: vi.fn(),
  fetchPluginList: vi.fn(),
  getCachedPluginList: vi.fn(),
  getInstalledPluginVersion: vi.fn(),
  getInstalledPlugins: vi.fn(),
  getMaimaiVersion: vi.fn(),
  installPlugin: vi.fn(),
  isPluginCompatible: vi.fn(),
  uninstallPlugin: vi.fn(),
  updatePlugin: vi.fn(),
}))

const gitStatus: GitStatus = { installed: true, version: '2.40.0' }
const maimaiVersion: MaimaiVersion = {
  version: '1.0.0',
  version_major: 1,
  version_minor: 0,
  version_patch: 0,
}

function makePlugin(
  id: string,
  overrides: Omit<Partial<PluginInfo>, 'manifest'> & { manifest?: Partial<PluginManifest> } = {},
): PluginInfo {
  const { manifest: manifestOverrides, ...pluginOverrides } = overrides
  return {
    id,
    manifest: {
      manifest_version: 2,
      id,
      name: `插件-${id}`,
      version: '1.2.0',
      description: `${id} 描述`,
      author: { name: '测试作者' },
      license: 'MIT',
      host_application: { min_version: '1.0.0' },
      repository_url: `https://example.com/${id}.git`,
      keywords: ['标签一'],
      plugin_type: 'extension',
      default_locale: 'zh-CN',
      ...manifestOverrides,
    },
    downloads: 0,
    rating: 0,
    review_count: 0,
    installed: true,
    installed_version: '1.0.0',
    published_at: '2026-01-01T00:00:00.000Z',
    updated_at: '2026-01-01T00:00:00.000Z',
    source: 'market',
    ...pluginOverrides,
  }
}

function makeStats(id: string, overrides: Partial<PluginStatsData> = {}): PluginStatsData {
  return {
    plugin_id: id,
    likes: 0,
    dislikes: 0,
    downloads: 0,
    rating: 0,
    rating_count: 0,
    ...overrides,
  }
}

function makeProgress(
  pluginId: string,
  overrides: Partial<PluginLoadProgress> = {},
): PluginLoadProgress {
  return {
    operation: 'update',
    stage: 'loading',
    progress: 55,
    message: '正在拉取更新',
    plugin_id: pluginId,
    total_plugins: 1,
    loaded_plugins: 0,
    ...overrides,
  }
}

function makeTabProps(
  plugins: PluginInfo[],
  overrides: Partial<ComponentProps<typeof UpdatesTab>> = {},
): ComponentProps<typeof UpdatesTab> {
  return {
    plugins,
    searchQuery: '',
    pluginTypeFilter: 'all',
    showCompatibleOnly: false,
    gitStatus,
    maimaiVersion,
    pluginStats: Object.fromEntries(
      plugins.map((plugin) => [plugin.manifest?.id ?? plugin.id, makeStats(plugin.manifest?.id ?? plugin.id)]),
    ),
    loadProgress: null,
    likingPluginIds: new Set<string>(),
    onInstall: vi.fn(),
    onLike: vi.fn(),
    onUpdate: vi.fn(),
    onUninstall: vi.fn(),
    onDetail: vi.fn(),
    checkPluginCompatibility: vi.fn(() => true),
    needsUpdate: vi.fn((plugin: PluginInfo) => plugin.installed === true),
    getStatusBadge: vi.fn(() => null),
    getIncompatibleReason: vi.fn(() => null),
    ...overrides,
  }
}

function renderTab(
  plugins: PluginInfo[],
  overrides: Partial<ComponentProps<typeof UpdatesTab>> = {},
) {
  const props = makeTabProps(plugins, overrides)
  return { ...render(<UpdatesTab {...props} />), props }
}

function getDisplayedPluginNames(): string[] {
  return Array.from(document.querySelectorAll('[data-plugin-market-card="true"]')).map(
    (card) => card.querySelector('.line-clamp-2')?.textContent ?? '',
  )
}

afterEach(() => {
  cleanup()
})

describe('UpdatesTab 空态与可见列表', () => {
  it('没有可更新插件时只渲染空网格', () => {
    const current = makePlugin('current')
    const uninstalled = makePlugin('market', { installed: false })
    const noManifest = {
      id: 'ghost',
      downloads: 1,
      rating: 1,
      review_count: 0,
      installed: true,
      published_at: '',
      updated_at: '',
    } as PluginInfo

    const { container } = renderTab([current, uninstalled, noManifest], {
      needsUpdate: vi.fn(() => false),
    })

    expect(container.querySelectorAll('[data-plugin-market-card="true"]')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: '更新' })).not.toBeInTheDocument()
    expect(container.firstElementChild).toHaveClass('grid')
  })

  it('只展示已安装且 needsUpdate 为真的插件，未安装即使回报可更新也排除', () => {
    const update = makePlugin('update')
    const current = makePlugin('current')
    const uninstalled = makePlugin('market', { installed: false })
    const noManifest = {
      id: 'ghost',
      downloads: 1,
      rating: 1,
      review_count: 0,
      installed: true,
      published_at: '',
      updated_at: '',
    } as PluginInfo

    renderTab([update, current, uninstalled, noManifest], {
      needsUpdate: vi.fn((plugin: PluginInfo) => plugin.id === 'update' || plugin.id === 'market'),
    })

    expect(getDisplayedPluginNames()).toEqual(['插件-update'])
    expect(screen.queryByText('插件-current')).not.toBeInTheDocument()
    expect(screen.queryByText('插件-market')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '更新' })).toBeEnabled()
  })
})

describe('UpdatesTab 过滤与更新操作', () => {
  it('按名称、描述和关键词搜索可更新插件', () => {
    const byName = makePlugin('name-hit', {
      manifest: { name: 'WeatherBot', description: '无关描述', keywords: [] },
    })
    const byDescription = makePlugin('desc-hit', {
      manifest: { name: '其他插件', description: '提供天气预报', keywords: [] },
    })
    const byKeyword = makePlugin('keyword-hit', {
      manifest: { name: '工具', description: '通用工具', keywords: ['天气查询'] },
    })
    const plugins = [byName, byDescription, byKeyword]
    const { rerender } = renderTab(plugins, { searchQuery: 'weather' })

    expect(getDisplayedPluginNames()).toEqual(['WeatherBot'])

    rerender(<UpdatesTab {...makeTabProps(plugins, { searchQuery: '天气预报' })} />)
    expect(getDisplayedPluginNames()).toEqual(['其他插件'])

    rerender(<UpdatesTab {...makeTabProps(plugins, { searchQuery: '天气查询' })} />)
    expect(getDisplayedPluginNames()).toEqual(['工具'])

    rerender(<UpdatesTab {...makeTabProps(plugins, { searchQuery: '完全不存在' })} />)
    expect(getDisplayedPluginNames()).toEqual([])
  })

  it('按类型和兼容性过滤，无麦麦版本时不过滤兼容性', () => {
    const chat = makePlugin('chat', { manifest: { plugin_type: 'chat' } })
    const game = makePlugin('game', { manifest: { plugin_type: 'game' } })
    const incompatible = makePlugin('incompatible', { manifest: { plugin_type: 'chat' } })
    const plugins = [chat, game, incompatible]
    const checkPluginCompatibility = vi.fn((plugin: PluginInfo) => plugin.id !== 'incompatible')

    const { rerender } = renderTab(plugins, {
      pluginTypeFilter: 'chat',
      showCompatibleOnly: true,
      checkPluginCompatibility,
    })
    expect(getDisplayedPluginNames()).toEqual(['插件-chat'])

    rerender(
      <UpdatesTab
        {...makeTabProps(plugins, {
          pluginTypeFilter: 'all',
          showCompatibleOnly: true,
          maimaiVersion: null,
          checkPluginCompatibility,
        })}
      />,
    )
    expect(getDisplayedPluginNames()).toEqual(['插件-chat', '插件-game', '插件-incompatible'])
  })

  it('点击更新把当前插件交给 onUpdate，更新中卡片展示进度', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha')
    const { props, rerender } = renderTab([plugin])

    await user.click(screen.getByRole('button', { name: '更新' }))
    expect(props.onUpdate).toHaveBeenCalledTimes(1)
    expect(props.onUpdate).toHaveBeenCalledWith(plugin)

    rerender(
      <UpdatesTab
        {...makeTabProps([plugin], {
          loadProgress: makeProgress('alpha'),
          onUpdate: props.onUpdate,
        })}
      />,
    )
    expect(screen.getByText('正在更新')).toBeInTheDocument()
    expect(screen.getByText('55%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '更新中' })).toBeDisabled()
  })
})
