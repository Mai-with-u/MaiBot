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

import { InstalledTab } from '../InstalledTab'

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
    operation: 'uninstall',
    stage: 'loading',
    progress: 40,
    message: '正在删除文件',
    plugin_id: pluginId,
    total_plugins: 1,
    loaded_plugins: 0,
    ...overrides,
  }
}

function makeTabProps(
  plugins: PluginInfo[],
  overrides: Partial<ComponentProps<typeof InstalledTab>> = {},
): ComponentProps<typeof InstalledTab> {
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
    needsUpdate: vi.fn(() => false),
    getStatusBadge: vi.fn(() => null),
    getIncompatibleReason: vi.fn(() => null),
    ...overrides,
  }
}

function renderTab(
  plugins: PluginInfo[],
  overrides: Partial<ComponentProps<typeof InstalledTab>> = {},
) {
  const props = makeTabProps(plugins, overrides)
  return { ...render(<InstalledTab {...props} />), props }
}

function getDisplayedPluginNames(): string[] {
  return Array.from(document.querySelectorAll('[data-plugin-market-card="true"]')).map(
    (card) => card.querySelector('.line-clamp-2')?.textContent ?? '',
  )
}

afterEach(() => {
  cleanup()
})

describe('InstalledTab 空态', () => {
  it('没有插件、全未安装或无 manifest 时只渲染空网格', () => {
    const { rerender, container } = renderTab([])

    expect(container.querySelectorAll('[data-plugin-market-card="true"]')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: '卸载' })).not.toBeInTheDocument()
    expect(container.firstElementChild).toHaveClass('grid')

    const uninstalled = makePlugin('market-only', { installed: false })
    const noManifest = {
      id: 'ghost',
      downloads: 1,
      rating: 1,
      review_count: 0,
      installed: true,
      published_at: '',
      updated_at: '',
    } as PluginInfo

    rerender(<InstalledTab {...makeTabProps([uninstalled, noManifest])} />)
    expect(document.querySelectorAll('[data-plugin-market-card="true"]')).toHaveLength(0)
    expect(screen.queryByText('插件-market-only')).not.toBeInTheDocument()
  })

  it('搜索、类型或兼容性过滤无匹配时保持空列表', () => {
    const chat = makePlugin('chat', { manifest: { plugin_type: 'chat', name: '天气助手' } })
    const game = makePlugin('game', {
      manifest: { plugin_type: 'game', name: '音游', description: '本地节奏游戏', keywords: ['娱乐'] },
    })
    const plugins = [chat, game]
    const { rerender } = renderTab(plugins, { searchQuery: '完全不存在' })
    expect(getDisplayedPluginNames()).toEqual([])

    rerender(<InstalledTab {...makeTabProps(plugins, { pluginTypeFilter: 'search' })} />)
    expect(getDisplayedPluginNames()).toEqual([])

    rerender(
      <InstalledTab
        {...makeTabProps(plugins, {
          showCompatibleOnly: true,
          checkPluginCompatibility: vi.fn(() => false),
        })}
      />,
    )
    expect(getDisplayedPluginNames()).toEqual([])
  })
})

describe('InstalledTab 过滤与操作', () => {
  it('只展示已安装插件，并按名称、描述、关键词搜索', () => {
    const byName = makePlugin('name-hit', {
      manifest: { name: 'WeatherBot', description: '无关描述', keywords: [] },
    })
    const byDescription = makePlugin('desc-hit', {
      manifest: { name: '其他插件', description: '提供天气预报', keywords: [] },
    })
    const byKeyword = makePlugin('keyword-hit', {
      manifest: { name: '工具', description: '通用工具', keywords: ['天气查询'] },
    })
    const uninstalled = makePlugin('market', {
      installed: false,
      manifest: { name: 'WeatherMarket' },
    })
    const plugins = [byName, byDescription, byKeyword, uninstalled]
    const { rerender } = renderTab(plugins, { searchQuery: 'weather' })

    expect(getDisplayedPluginNames()).toEqual(['WeatherBot'])

    rerender(<InstalledTab {...makeTabProps(plugins, { searchQuery: '天气预报' })} />)
    expect(getDisplayedPluginNames()).toEqual(['其他插件'])

    rerender(<InstalledTab {...makeTabProps(plugins, { searchQuery: '天气查询' })} />)
    expect(getDisplayedPluginNames()).toEqual(['工具'])

    rerender(<InstalledTab {...makeTabProps(plugins, { searchQuery: '' })} />)
    expect(getDisplayedPluginNames()).toEqual(['WeatherBot', '其他插件', '工具'])
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
      <InstalledTab
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

  it('把卸载回调交给当前已安装卡片，并透出进行中进度', async () => {
    const user = userEvent.setup()
    const installing = makePlugin('alpha')
    const idle = makePlugin('beta')
    const { props, rerender } = renderTab([installing], {
      loadProgress: makeProgress('alpha'),
    })

    expect(screen.getByText('正在卸载')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '卸载' })).toBeDisabled()

    const idleProps = makeTabProps([idle])
    rerender(<InstalledTab {...idleProps} />)
    await user.click(screen.getByRole('button', { name: '卸载' }))
    expect(idleProps.onUninstall).toHaveBeenCalledWith(idle)
    expect(props.onUninstall).not.toHaveBeenCalled()
  })
})
