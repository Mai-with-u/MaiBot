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

import { PluginCard } from '../PluginCard'

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
    installed: false,
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

function makeCardProps(
  plugin: PluginInfo,
  overrides: Partial<ComponentProps<typeof PluginCard>> = {},
): ComponentProps<typeof PluginCard> {
  return {
    plugin,
    gitStatus,
    maimaiVersion,
    pluginStats: { [plugin.manifest.id ?? plugin.id]: makeStats(plugin.manifest.id ?? plugin.id) },
    loadProgress: null,
    likingPluginIds: new Set<string>(),
    onInstall: vi.fn(),
    onLike: vi.fn(),
    onUpdate: vi.fn(),
    onUninstall: vi.fn(),
    onDetail: vi.fn(),
    checkPluginCompatibility: vi.fn(() => true),
    needsUpdate: vi.fn(() => false),
    getStatusBadge: vi.fn(() => <span>状态徽标</span>),
    getIncompatibleReason: vi.fn(() => null),
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
})

describe('PluginCard 未安装状态', () => {
  it('展示安装入口，点击后把当前插件交给 onInstall', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha')
    const props = makeCardProps(plugin)
    render(<PluginCard {...props} />)

    const installButton = screen.getByRole('button', { name: '安装' })
    expect(installButton).toBeEnabled()
    await user.click(installButton)
    expect(props.onInstall).toHaveBeenCalledTimes(1)
    expect(props.onInstall).toHaveBeenCalledWith(plugin)
  })

  it('Git 未安装或 gitStatus 为空时禁用安装，并优先提示 Git', () => {
    const plugin = makePlugin('alpha')
    const { rerender } = render(
      <PluginCard
        {...makeCardProps(plugin, {
          gitStatus: { installed: false },
          checkPluginCompatibility: vi.fn(() => false),
          getIncompatibleReason: vi.fn(() => '版本过低'),
        })}
      />
    )

    expect(screen.getByRole('button', { name: '安装' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '安装' })).toHaveAttribute('title', 'Git 未安装')

    rerender(<PluginCard {...makeCardProps(plugin, { gitStatus: null })} />)
    expect(screen.getByRole('button', { name: '安装' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '安装' })).toHaveAttribute('title', 'Git 未安装')
  })

  it('不兼容时禁用安装：有原因用原因，无原因用默认文案', () => {
    const plugin = makePlugin('alpha')
    const { rerender } = render(
      <PluginCard
        {...makeCardProps(plugin, {
          checkPluginCompatibility: vi.fn(() => false),
          getIncompatibleReason: vi.fn(() => '版本过低'),
        })}
      />
    )
    expect(screen.getByRole('button', { name: '安装' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '安装' })).toHaveAttribute('title', '版本过低')

    rerender(
      <PluginCard
        {...makeCardProps(plugin, {
          checkPluginCompatibility: vi.fn(() => false),
          getIncompatibleReason: vi.fn(() => null),
        })}
      />
    )
    expect(screen.getByRole('button', { name: '安装' })).toHaveAttribute(
      'title',
      '插件与当前麦麦版本不兼容',
    )
  })

  it('无麦麦版本时不因兼容性禁用安装；其他插件安装中仍禁用', () => {
    const plugin = makePlugin('alpha')
    const { rerender } = render(
      <PluginCard
        {...makeCardProps(plugin, {
          maimaiVersion: null,
          checkPluginCompatibility: vi.fn(() => false),
        })}
      />
    )
    expect(screen.getByRole('button', { name: '安装' })).toBeEnabled()

    rerender(<PluginCard {...makeCardProps(plugin)} isAnyPluginInstalling />)
    expect(screen.getByRole('button', { name: '安装' })).toBeDisabled()
  })

  it('本插件处于安装 loading 时按钮标记为正在安装', () => {
    const plugin = makePlugin('alpha')
    render(
      <PluginCard
        {...makeCardProps(plugin, {
          loadProgress: makeProgress('alpha', { operation: 'install', stage: 'loading' }),
        })}
      />
    )

    expect(screen.getByRole('button', { name: '正在安装' })).toBeInTheDocument()
    expect(screen.getByText('正在安装')).toBeInTheDocument()
    expect(screen.getByText('40%')).toBeInTheDocument()
  })
})

describe('PluginCard 已安装状态', () => {
  it('无需更新时展示卸载，点击后交给 onUninstall', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha', { installed: true, installed_version: '1.2.0' })
    const props = makeCardProps(plugin, { needsUpdate: vi.fn(() => false) })
    render(<PluginCard {...props} />)

    expect(screen.queryByRole('button', { name: '安装' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '更新' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '卸载' }))
    expect(props.onUninstall).toHaveBeenCalledWith(plugin)
  })

  it('Git 未安装或操作进行中时禁用卸载', () => {
    const plugin = makePlugin('alpha', { installed: true })
    const { rerender } = render(
      <PluginCard {...makeCardProps(plugin, { gitStatus: { installed: false } })} />
    )
    expect(screen.getByRole('button', { name: '卸载' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '卸载' })).toHaveAttribute('title', 'Git 未安装')

    rerender(
      <PluginCard
        {...makeCardProps(plugin, {
          loadProgress: makeProgress('alpha', { operation: 'uninstall', stage: 'loading' }),
        })}
      />
    )
    expect(screen.getByRole('button', { name: '卸载' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '卸载' })).toHaveAttribute('title', '插件操作进行中')
    expect(screen.getByText('正在卸载')).toBeInTheDocument()
  })

  it('需要更新时展示更新入口，并在操作中改为更新中', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha', { installed: true, installed_version: '1.0.0' })
    const props = makeCardProps(plugin, { needsUpdate: vi.fn(() => true) })
    const { rerender } = render(<PluginCard {...props} />)

    expect(screen.queryByRole('button', { name: '卸载' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '更新' }))
    expect(props.onUpdate).toHaveBeenCalledWith(plugin)

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', { operation: 'update', stage: 'loading' })}
      />
    )
    expect(screen.getByRole('button', { name: '更新中' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '更新中' })).toHaveAttribute('title', '插件操作进行中')
    expect(screen.getByText('正在更新')).toBeInTheDocument()
  })

  it('需要更新但不兼容或 Git 缺失时禁用更新', () => {
    const plugin = makePlugin('alpha', { installed: true })
    const { rerender } = render(
      <PluginCard
        {...makeCardProps(plugin, {
          needsUpdate: vi.fn(() => true),
          gitStatus: { installed: false },
          checkPluginCompatibility: vi.fn(() => false),
          getIncompatibleReason: vi.fn(() => '版本过低'),
        })}
      />
    )
    expect(screen.getByRole('button', { name: '更新' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '更新' })).toHaveAttribute('title', 'Git 未安装')

    rerender(
      <PluginCard
        {...makeCardProps(plugin, {
          needsUpdate: vi.fn(() => true),
          checkPluginCompatibility: vi.fn(() => false),
          getIncompatibleReason: vi.fn(() => null),
        })}
      />
    )
    expect(screen.getByRole('button', { name: '更新' })).toHaveAttribute(
      'title',
      '插件与当前麦麦版本不兼容',
    )
  })
})

describe('PluginCard 点赞与详情', () => {
  it('未点赞、已点赞和点赞中状态切换，并触发详情', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha')
    const props = makeCardProps(plugin, {
      pluginStats: { alpha: makeStats('alpha', { likes: 3, liked: false }) },
    })
    const { rerender } = render(<PluginCard {...props} />)

    const likeButton = screen.getByRole('button', { name: '点赞' })
    expect(likeButton).toBeEnabled()
    await user.click(likeButton)
    expect(props.onLike).toHaveBeenCalledWith(plugin)

    rerender(
      <PluginCard
        {...props}
        pluginStats={{ alpha: makeStats('alpha', { likes: 4, liked: true }) }}
      />,
    )
    expect(screen.getByRole('button', { name: '取消点赞' })).toBeEnabled()

    rerender(
      <PluginCard
        {...props}
        likingPluginIds={new Set(['alpha'])}
        pluginStats={{ alpha: makeStats('alpha', { likes: 4, liked: true }) }}
      />,
    )
    expect(screen.getByRole('button', { name: '取消点赞' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '查看详情' }))
    expect(props.onDetail).toHaveBeenCalledWith(plugin)
  })
})

describe('PluginCard 进度展示', () => {
  it('按操作展示成功和失败文案，失败时回退错误信息', () => {
    const plugin = makePlugin('alpha')
    const props = makeCardProps(plugin)
    const { rerender } = render(
      <PluginCard {...props} loadProgress={makeProgress('alpha', { stage: 'success', progress: 100 })} />
    )
    expect(screen.getByText('安装完成')).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', { operation: 'uninstall', stage: 'success', progress: 100 })}
      />
    )
    expect(screen.getByText('卸载完成')).toBeInTheDocument()

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', { operation: 'update', stage: 'success', progress: 100 })}
      />
    )
    expect(screen.getByText('更新完成')).toBeInTheDocument()

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', { stage: 'error', error: '克隆失败' })}
      />
    )
    expect(screen.getByText('安装失败')).toBeInTheDocument()
    expect(screen.getByText('克隆失败')).toBeInTheDocument()
    expect(screen.queryByText('40%')).not.toBeInTheDocument()

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', {
          operation: 'uninstall',
          stage: 'error',
          error: undefined,
          message: '',
        })}
      />
    )
    expect(screen.getByText('卸载失败')).toBeInTheDocument()
    expect(screen.getByText('操作失败')).toBeInTheDocument()

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', { operation: 'update', stage: 'error', error: '冲突' })}
      />
    )
    expect(screen.getByText('更新失败')).toBeInTheDocument()
    expect(screen.getByText('冲突')).toBeInTheDocument()
  })

  it('fetch、idle 或其他插件进度不渲染进度条，镜像详情会展示', () => {
    const plugin = makePlugin('alpha')
    const props = makeCardProps(plugin)
    const { rerender } = render(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', { operation: 'fetch', stage: 'loading' })}
      />
    )
    expect(screen.queryByText('正在安装')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '安装' })).toBeInTheDocument()

    rerender(
      <PluginCard {...props} loadProgress={makeProgress('alpha', { stage: 'idle', message: '待命' })} />
    )
    expect(screen.queryByText('待命')).not.toBeInTheDocument()

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('beta', { operation: 'install', stage: 'loading' })}
      />
    )
    expect(screen.queryByText('正在安装')).not.toBeInTheDocument()

    rerender(
      <PluginCard
        {...props}
        loadProgress={makeProgress('alpha', {
          stage: 'loading',
          mirror_name: 'ghproxy',
          mirror_index: 2,
          total_mirrors: 3,
          attempt: 1,
          max_attempts: 2,
        })}
      />
    )
    expect(screen.getByText('镜像源 2/3：ghproxy · 尝试 1/2')).toBeInTheDocument()
  })
})

describe('PluginCard 展示回退', () => {
  it('缺名称、描述、版本和作者时使用占位文案', () => {
    const plugin = makePlugin('alpha', {
      manifest: {
        name: '',
        description: '',
        version: '',
        author: { name: '' },
        host_application: undefined as unknown as PluginManifest['host_application'],
        keywords: [],
      },
    })
    render(<PluginCard {...makeCardProps(plugin, { getStatusBadge: vi.fn(() => null) })} />)

    expect(screen.getByText('alpha')).toBeInTheDocument()
    expect(screen.getByText('无描述')).toBeInTheDocument()
    expect(screen.getByText('vunknown · Unknown')).toBeInTheDocument()
    expect(screen.queryByText(/^支持:/)).not.toBeInTheDocument()
  })

  it('关键词超过 3 个时截断，并展示版本支持区间', () => {
    const plugin = makePlugin('alpha', {
      manifest: {
        keywords: ['一', '二', '三', '四', '五'],
        host_application: { min_version: '1.0.0', max_version: '2.0.0' },
      },
    })
    const { rerender } = render(<PluginCard {...makeCardProps(plugin)} />)

    expect(screen.getByText('一')).toBeInTheDocument()
    expect(screen.getByText('二')).toBeInTheDocument()
    expect(screen.getByText('三')).toBeInTheDocument()
    expect(screen.queryByText('四')).not.toBeInTheDocument()
    expect(screen.getByText('+2')).toBeInTheDocument()
    expect(screen.getByText('1.0.0 - 2.0.0')).toBeInTheDocument()

    rerender(
      <PluginCard
        {...makeCardProps(
          makePlugin('alpha', {
            manifest: { host_application: { min_version: '1.2.0' } },
          }),
        )}
      />
    )
    expect(screen.getByText('1.2.0 - 最新版本')).toBeInTheDocument()
  })

  it('统计按 manifest.id 取值，缺失时回退到插件字段', () => {
    const aliased = makePlugin('runtime-id', {
      downloads: 12,
      rating: 3.2,
      review_count: 4,
      manifest: { id: 'manifest-id' },
    })
    const { rerender } = render(
      <PluginCard
        {...makeCardProps(aliased, {
          pluginStats: {
            'manifest-id': makeStats('manifest-id', {
              downloads: 99,
              rating: 4.8,
              likes: 7,
              rating_count: 10,
            }),
          },
        })}
      />
    )

    expect(document.querySelector('[data-plugin-stat-value="downloads"]')).toHaveTextContent('99')
    expect(document.querySelector('[data-plugin-stat-value="rating"]')).toHaveTextContent('4.8')
    expect(document.querySelector('[data-plugin-stat-value="likes"]')).toHaveTextContent('7')
    expect(document.querySelector('[data-plugin-stat-value="reviews"]')).toHaveTextContent('10')
    expect(screen.getByText('状态徽标')).toBeInTheDocument()

    rerender(
      <PluginCard
        {...makeCardProps(aliased, {
          pluginStats: { 'runtime-id': makeStats('runtime-id', { downloads: 80, likes: 5 }) },
        })}
      />
    )
    expect(document.querySelector('[data-plugin-stat-value="downloads"]')).toHaveTextContent('12')
    expect(document.querySelector('[data-plugin-stat-value="rating"]')).toHaveTextContent('3.2')
    expect(document.querySelector('[data-plugin-stat-value="likes"]')).toHaveTextContent('0')
    expect(document.querySelector('[data-plugin-stat-value="reviews"]')).toHaveTextContent('4')
  })
})
