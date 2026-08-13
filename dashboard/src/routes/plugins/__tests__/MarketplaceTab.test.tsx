import type { ComponentProps } from 'react'
import type { PluginManifest } from '@/types/plugin'
import type {
  GitStatus,
  MaimaiVersion,
  MarketplaceSortKey,
  PluginInfo,
  PluginLoadProgress,
  PluginStatsData,
} from '../types'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MarketplaceTab } from '../MarketplaceTab'
import { PluginIcon } from '../PluginIcon'

// 与组件内部 renderTime 对齐，保证上新加权、新鲜度加权可复现
const NOW = Date.parse('2026-08-13T12:00:00.000Z')

const gitStatus: GitStatus = { installed: true, version: '2.40.0' }
const maimaiVersion: MaimaiVersion = {
  version: '1.0.0',
  version_major: 1,
  version_minor: 0,
  version_patch: 0,
}

function hoursAgo(hours: number): string {
  return new Date(NOW - hours * 60 * 60 * 1000).toISOString()
}

function daysAgo(days: number): string {
  return new Date(NOW - days * 24 * 60 * 60 * 1000).toISOString()
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
    installed: false,
    published_at: daysAgo(200),
    updated_at: daysAgo(200),
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
  overrides: Partial<PluginLoadProgress> = {}
): PluginLoadProgress {
  return {
    operation: 'install',
    stage: 'loading',
    progress: 40,
    message: '正在克隆仓库',
    plugin_id: pluginId,
    total_plugins: 1,
    loaded_plugins: 0,
    ...overrides,
  }
}

function makeTabProps(
  plugins: PluginInfo[],
  overrides: Partial<ComponentProps<typeof MarketplaceTab>> = {}
) {
  return {
    plugins,
    searchQuery: '',
    pluginTypeFilter: 'all',
    showCompatibleOnly: false,
    hideInstalledPlugins: true,
    sortBy: 'default' as MarketplaceSortKey,
    gitStatus,
    maimaiVersion,
    pluginStats: Object.fromEntries(plugins.map((plugin) => [plugin.id, makeStats(plugin.id)])),
    pluginProgressById: {},
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
  overrides: Partial<ComponentProps<typeof MarketplaceTab>> = {}
) {
  const props = makeTabProps(plugins, overrides)
  return { ...render(<MarketplaceTab {...props} />), props }
}

function getDisplayedPluginNames(): string[] {
  return Array.from(document.querySelectorAll('[data-plugin-market-card="true"]')).map(
    (card) => card.querySelector('.line-clamp-2')?.textContent ?? ''
  )
}

beforeEach(() => {
  vi.spyOn(Date, 'now').mockReturnValue(NOW)
})

afterEach(() => {
  cleanup()
})

describe('MarketplaceTab 搜索与空市场', () => {
  it('按名称、描述和关键词过滤，大小写不敏感', () => {
    const byName = makePlugin('name-hit', {
      manifest: { name: 'WeatherBot', description: '无关描述', keywords: [] },
    })
    const byDescription = makePlugin('desc-hit', {
      manifest: { name: '其他插件', description: '提供天气预报', keywords: [] },
    })
    const byKeyword = makePlugin('keyword-hit', {
      manifest: { name: '工具', description: '通用工具', keywords: ['天气查询'] },
    })
    const miss = makePlugin('miss', {
      manifest: { name: '音乐盒', description: '播放本地音乐', keywords: ['音频'] },
    })
    const plugins = [byName, byDescription, byKeyword, miss]
    const { rerender } = renderTab(plugins, { searchQuery: 'weather' })

    expect(getDisplayedPluginNames()).toEqual(['WeatherBot'])

    rerender(<MarketplaceTab {...makeTabProps(plugins, { searchQuery: '天气预报' })} />)
    expect(getDisplayedPluginNames()).toEqual(['其他插件'])

    rerender(<MarketplaceTab {...makeTabProps(plugins, { searchQuery: '天气查询' })} />)
    expect(getDisplayedPluginNames()).toEqual(['工具'])

    rerender(<MarketplaceTab {...makeTabProps(plugins, { searchQuery: '' })} />)
    expect(getDisplayedPluginNames()).toHaveLength(4)
  })

  it('搜索无匹配或插件列表为空时只渲染空网格', () => {
    const { rerender, container } = renderTab([], { searchQuery: '' })

    expect(container.querySelectorAll('[data-plugin-market-card="true"]')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: '安装' })).not.toBeInTheDocument()
    expect(container.firstElementChild).toHaveClass('grid')

    rerender(
      <MarketplaceTab
        {...makeTabProps([makePlugin('alpha')], { searchQuery: '完全不存在的关键词' })}
      />
    )
    expect(document.querySelectorAll('[data-plugin-market-card="true"]')).toHaveLength(0)
  })
})

describe('MarketplaceTab 安装入口', () => {
  it('点击安装按钮把当前插件交给 onInstall', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha')
    const { props } = renderTab([plugin])

    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(props.onInstall).toHaveBeenCalledTimes(1)
    expect(props.onInstall).toHaveBeenCalledWith(plugin)
  })

  it('任一插件处于安装 loading 时禁用全部安装按钮', () => {
    const alpha = makePlugin('alpha')
    const beta = makePlugin('beta')
    const { rerender } = renderTab([alpha, beta], {
      pluginProgressById: { alpha: makeProgress('alpha') },
    })

    const installingButtons = screen.getAllByRole('button', { name: /安装/ })
    expect(installingButtons).toHaveLength(2)
    expect(installingButtons.every((button) => button.hasAttribute('disabled'))).toBe(true)

    // 更新进度不算“正在安装”，不应禁用其他卡片的安装入口
    rerender(
      <MarketplaceTab
        {...makeTabProps([alpha, beta], {
          pluginProgressById: {
            alpha: makeProgress('alpha', { operation: 'update', stage: 'loading' }),
          },
        })}
      />
    )
    expect(screen.getAllByRole('button', { name: '安装' })).toHaveLength(2)
    expect(
      screen.getAllByRole('button', { name: '安装' }).every((button) => button.hasAttribute('disabled'))
    ).toBe(false)
  })
})

describe('MarketplaceTab 过滤', () => {
  it('跳过无 manifest、本地来源，以及隐藏已安装开关打开时的已安装插件', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const visible = makePlugin('visible')
    const installed = makePlugin('installed', { installed: true })
    const local = makePlugin('local', { source: 'local' })
    const noManifest = {
      id: 'ghost',
      downloads: 1,
      rating: 1,
      review_count: 0,
      installed: false,
      published_at: '',
      updated_at: '',
    } as PluginInfo

    renderTab([visible, installed, local, noManifest])

    expect(getDisplayedPluginNames()).toEqual(['插件-visible'])
    expect(warnSpy).toHaveBeenCalledWith('[过滤] 跳过无 manifest 的插件:', 'ghost')
  })

  it('关闭隐藏已安装后展示市场已安装插件，本地插件仍排除', () => {
    const installedMarket = makePlugin('installed-market', { installed: true })
    const local = makePlugin('local-only', { source: 'local', installed: true })
    renderTab([installedMarket, local], { hideInstalledPlugins: false })

    expect(getDisplayedPluginNames()).toEqual(['插件-installed-market'])
    expect(screen.queryByText('插件-local-only')).not.toBeInTheDocument()
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
      <MarketplaceTab
        {...makeTabProps(plugins, {
          pluginTypeFilter: 'all',
          showCompatibleOnly: true,
          maimaiVersion: null,
          checkPluginCompatibility,
        })}
      />
    )
    expect(getDisplayedPluginNames()).toEqual([
      '插件-chat',
      '插件-game',
      '插件-incompatible',
    ])
  })
})

describe('MarketplaceTab 排序与推荐', () => {
  it('默认排序：24 小时内上新权重大于下载量，随后按衰减时间排序', () => {
    const fresh = makePlugin('fresh', {
      published_at: hoursAgo(24),
      updated_at: hoursAgo(24),
    })
    const decaying = makePlugin('decay', {
      published_at: hoursAgo(36),
      updated_at: hoursAgo(36),
    })
    const launchedOut = makePlugin('launched-out', {
      published_at: hoursAgo(48),
      updated_at: hoursAgo(48),
    })
    const popular = makePlugin('popular', {
      downloads: 99999,
      published_at: daysAgo(200),
      updated_at: daysAgo(200),
    })

    renderTab([popular, launchedOut, decaying, fresh], {
      pluginStats: {
        fresh: makeStats('fresh'),
        decay: makeStats('decay'),
        'launched-out': makeStats('launched-out'),
        popular: makeStats('popular', {
          downloads: 99999,
          likes: 500,
          rating: 5,
          rating_count: 200,
        }),
      },
    })

    // 48 小时后上新加权归零，高下载量旧插件会重新排到它前面
    expect(getDisplayedPluginNames()).toEqual([
      '插件-fresh',
      '插件-decay',
      '插件-popular',
      '插件-launched-out',
    ])
  })

  it('默认排序：旧插件按下载、点赞、评分归一化分数排列', () => {
    const highDownload = makePlugin('high-dl')
    const highLike = makePlugin('high-like')
    const highRating = makePlugin('high-rating')

    renderTab([highRating, highLike, highDownload], {
      pluginStats: {
        'high-dl': makeStats('high-dl', { downloads: 10000 }),
        'high-like': makeStats('high-like', { likes: 10000 }),
        'high-rating': makeStats('high-rating', { rating: 5, rating_count: 100 }),
      },
    })

    expect(getDisplayedPluginNames()).toEqual([
      '插件-high-dl',
      '插件-high-like',
      '插件-high-rating',
    ])
  })

  it('默认排序：无有效时间时用 marketplace_order 提供新鲜度加权', () => {
    const highOrder = makePlugin('high-order', {
      published_at: 'not-a-date',
      updated_at: '',
      marketplace_order: 10,
    })
    const lowOrder = makePlugin('low-order', {
      published_at: '',
      updated_at: 'also-invalid',
      marketplace_order: 1,
    })

    renderTab([lowOrder, highOrder], { pluginStats: {} })
    expect(getDisplayedPluginNames()).toEqual(['插件-high-order', '插件-low-order'])
  })

  it('默认排序：超过 120 天不再获得时间新鲜度，零分时按名称兜底', () => {
    const almostExpired = makePlugin('almost', {
      published_at: daysAgo(119),
      updated_at: daysAgo(119),
    })
    const expired = makePlugin('expired', {
      published_at: daysAgo(120),
      updated_at: daysAgo(120),
    })
    const { rerender } = renderTab([expired, almostExpired], { pluginStats: {} })
    expect(getDisplayedPluginNames()).toEqual(['插件-almost', '插件-expired'])

    const zeta = makePlugin('zeta', { published_at: '', updated_at: '', marketplace_order: 0 })
    const alpha = makePlugin('alpha', { published_at: '', updated_at: '', marketplace_order: 0 })
    rerender(<MarketplaceTab {...makeTabProps([zeta, alpha], { pluginStats: {} })} />)
    expect(getDisplayedPluginNames()).toEqual(['插件-alpha', '插件-zeta'])
  })

  it('按最新、点赞、评分排序，未知排序键回退到新鲜度再按名称', () => {
    const newest = makePlugin('newest', { published_at: hoursAgo(1), updated_at: hoursAgo(1) })
    const updatedOnly = makePlugin('updated', {
      published_at: 'bad',
      updated_at: hoursAgo(10),
    })
    const ordered = makePlugin('ordered', {
      published_at: '',
      updated_at: 'nope',
      marketplace_order: 99,
    })
    const leftover = makePlugin('leftover', {
      published_at: '',
      updated_at: '',
      marketplace_order: 1,
    })
    const plugins = [leftover, ordered, updatedOnly, newest]
    const { rerender } = renderTab(plugins, { sortBy: 'latest', pluginStats: {} })
    expect(getDisplayedPluginNames()).toEqual([
      '插件-newest',
      '插件-updated',
      '插件-ordered',
      '插件-leftover',
    ])

    rerender(
      <MarketplaceTab
        {...makeTabProps(plugins, {
          sortBy: 'likes',
          pluginStats: {
            newest: makeStats('newest', { likes: 3 }),
            updated: makeStats('updated', { likes: 10 }),
          },
        })}
      />
    )
    expect(getDisplayedPluginNames()).toEqual([
      '插件-updated',
      '插件-newest',
      '插件-ordered',
      '插件-leftover',
    ])

    rerender(
      <MarketplaceTab
        {...makeTabProps(
          [
            makePlugin('stats-rating', { rating: 1 }),
            makePlugin('plugin-rating', { rating: 4.8 }),
            makePlugin('low-rating', { rating: 2 }),
          ],
          {
            sortBy: 'rating',
            pluginStats: {
              'stats-rating': makeStats('stats-rating', { rating: 4.2 }),
              'low-rating': makeStats('low-rating', { rating: 3 }),
            },
          }
        )}
      />
    )
    expect(getDisplayedPluginNames()).toEqual([
      '插件-plugin-rating',
      '插件-stats-rating',
      '插件-low-rating',
    ])

    rerender(
      <MarketplaceTab
        {...makeTabProps(
          [
            makePlugin('zeta', { published_at: hoursAgo(5) }),
            makePlugin('alpha', { published_at: hoursAgo(5) }),
          ],
          { sortBy: 'not-a-key' as MarketplaceSortKey, pluginStats: {} }
        )}
      />
    )
    expect(getDisplayedPluginNames()).toEqual(['插件-alpha', '插件-zeta'])
  })

  it('缺名称、下载和评分时分别回退到 id 与 0', () => {
    const unnamedLate = makePlugin('zeta-id', {
      manifest: { name: '' },
      downloads: undefined as unknown as number,
      rating: undefined as unknown as number,
      published_at: '',
      updated_at: '',
    })
    const unnamedEarly = makePlugin('alpha-id', {
      manifest: { name: '' },
      downloads: undefined as unknown as number,
      rating: undefined as unknown as number,
      published_at: '',
      updated_at: '',
    })

    renderTab([unnamedLate, unnamedEarly], { pluginStats: {}, sortBy: 'rating' })

    // 卡片标题在 name 为空时展示 id；评分都是 0 后按 id 字母序
    expect(getDisplayedPluginNames()).toEqual(['alpha-id', 'zeta-id'])
  })

  it('统计可回退到 plugin.id，下载量排序使用 stats 覆盖插件字段', () => {
    const alias = makePlugin('runtime-id', {
      downloads: 1,
      manifest: { id: 'manifest-id' },
    })
    const direct = makePlugin('direct', { downloads: 50 })

    renderTab([alias, direct], {
      sortBy: 'downloads',
      pluginStats: {
        'runtime-id': makeStats('runtime-id', { downloads: 80 }),
        direct: makeStats('direct', { downloads: 20 }),
      },
    })

    expect(getDisplayedPluginNames()).toEqual(['插件-runtime-id', '插件-direct'])
  })

  it('默认排序且数量超过 4 时，把最新的 4 个插件提前为推荐位', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.42)
    const plugins = [1, 2, 3, 4, 5, 6].map((day) =>
      makePlugin(`d${day}`, {
        published_at: daysAgo(day),
        updated_at: daysAgo(day),
      })
    )

    const { unmount } = renderTab(plugins)
    const firstRender = getDisplayedPluginNames()
    expect(firstRender).toHaveLength(6)
    expect(new Set(firstRender.slice(0, 4))).toEqual(
      new Set(['插件-d1', '插件-d2', '插件-d3', '插件-d4'])
    )
    expect(firstRender.slice(4)).toEqual(['插件-d5', '插件-d6'])

    unmount()
    renderTab(plugins)
    expect(getDisplayedPluginNames()).toEqual(firstRender)
  })

  it('推荐候选新鲜度相同时按 marketplace_order 取前 4 个', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.11)
    const plugins = [5, 4, 3, 2, 1].map((order) =>
      makePlugin(`order-${order}`, {
        published_at: daysAgo(10),
        updated_at: daysAgo(10),
        marketplace_order: order,
      })
    )

    renderTab(plugins)
    const names = getDisplayedPluginNames()
    expect(new Set(names.slice(0, 4))).toEqual(
      new Set(['插件-order-5', '插件-order-4', '插件-order-3', '插件-order-2'])
    )
    expect(names[4]).toBe('插件-order-1')
  })

  it('非默认排序不会插入推荐位，按对应字段顺序展示', () => {
    const plugins = [1, 2, 3, 4, 5].map((index) =>
      makePlugin(`p${index}`, {
        downloads: index,
        published_at: daysAgo(index),
      })
    )

    renderTab(plugins, {
      sortBy: 'downloads',
      pluginStats: Object.fromEntries(
        plugins.map((plugin, index) => [plugin.id, makeStats(plugin.id, { downloads: index + 1 })])
      ),
    })

    expect(getDisplayedPluginNames()).toEqual([
      '插件-p5',
      '插件-p4',
      '插件-p3',
      '插件-p2',
      '插件-p1',
    ])
  })
})

describe('PluginIcon', () => {
  it('emoji 图标渲染字符并应用背景色', () => {
    const { container } = render(
      <PluginIcon
        pluginId="emoji-plugin"
        manifest={{
          display: { icon: { type: 'emoji', value: '🎯', background: '#ff00aa' } },
        }}
      />
    )

    expect(screen.getByText('🎯')).toHaveAttribute('aria-hidden', 'true')
    expect(container.firstElementChild).toHaveStyle({ backgroundColor: 'rgb(255, 0, 170)' })
  })

  it('lucide 命中、别名、未知值回退到 fallback 或插件类型图标', () => {
    const { rerender, container } = render(
      <PluginIcon
        pluginId="lucide-bot"
        manifest={{ display: { icon: { type: 'lucide', value: '  BoT  ' } } }}
        iconClassName="icon-size"
      />
    )
    expect(container.querySelector('svg.lucide-bot')).toHaveClass('h-5', 'w-5', 'icon-size')

    rerender(
      <PluginIcon
        pluginId="lucide-alias"
        manifest={{ display: { icon: { type: 'lucide', value: 'bar-chart-3' } } }}
      />
    )
    // lucide-react 0.556 把 BarChart3 重导出为 ChartColumn
    expect(container.querySelector('svg.lucide-chart-column')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="lucide-fallback"
        manifest={{
          plugin_type: 'game',
          display: { icon: { type: 'lucide', value: 'not-an-icon', fallback: 'shield' } },
        }}
      />
    )
    expect(container.querySelector('svg.lucide-shield')).not.toBeNull()

    rerender(
      <PluginIcon
        pluginId="lucide-type"
        manifest={{
          plugin_type: 'game',
          display: { icon: { type: 'lucide', value: 'missing-icon' } },
        }}
      />
    )
    expect(container.querySelector('svg.lucide-gamepad-2')).not.toBeNull()
  })

  it('无图标时按插件类型回退，空白类型用扩展，未知类型用其他', () => {
    const { rerender, container } = render(
      <PluginIcon pluginId="adapter" manifest={{ plugin_type: 'adapter' }} />
    )
    expect(container.querySelector('svg.lucide-plug')).not.toBeNull()

    rerender(<PluginIcon pluginId="blank-type" manifest={{ plugin_type: '   ' }} />)
    expect(container.querySelector('svg.lucide-puzzle')).not.toBeNull()

    rerender(<PluginIcon pluginId="unknown-type" manifest={{ plugin_type: 'not-real' }} />)
    expect(container.querySelector('svg.lucide-package')).not.toBeNull()

    rerender(<PluginIcon pluginId="no-manifest" />)
    expect(container.querySelector('svg.lucide-puzzle')).not.toBeNull()
  })

  it('已安装本地图标走插件 API，失败后回退到类型图标', () => {
    const { container } = render(
      <PluginIcon
        pluginId="hello/world"
        installed
        manifest={{
          plugin_type: 'chat',
          display: { icon: { type: 'local', value: 'icon.png', fallback: 'search' } },
        }}
      />
    )

    const image = container.querySelector('img')
    expect(image).toHaveAttribute('src', '/api/webui/plugins/icon/hello%2Fworld')
    expect(image).toHaveAttribute('alt', '')

    fireEvent.error(image!)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg.lucide-search')).not.toBeNull()
  })

  it('未安装时使用市场图标地址，缺失地址则直接回退', () => {
    const { rerender, container } = render(
      <PluginIcon
        pluginId="market-icon"
        marketplaceIconUrl="  https://cdn.example/icon.png  "
        manifest={{
          plugin_type: 'search',
          display: { icon: { type: 'local', value: 'icon.png' } },
        }}
      />
    )
    expect(container.querySelector('img')).toHaveAttribute('src', 'https://cdn.example/icon.png')

    rerender(
      <PluginIcon
        pluginId="missing-market-icon"
        marketplaceIconUrl="   "
        manifest={{
          plugin_type: 'search',
          display: { icon: { type: 'local', value: 'icon.png' } },
        }}
      />
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg.lucide-search')).not.toBeNull()
  })
})
