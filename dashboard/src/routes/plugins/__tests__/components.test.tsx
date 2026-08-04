import type { ReactNode } from 'react'
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
import { InstallDialog } from '../InstallDialog'
import { MarketplaceTab } from '../MarketplaceTab'
import { PluginCard } from '../PluginCard'
import { UpdatesTab } from '../UpdatesTab'

vi.mock('../PluginIcon', () => ({
  PluginIcon: ({ pluginId }: { pluginId: string }) => (
    <div data-testid={`plugin-icon-${pluginId}`} />
  ),
}))

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    children,
    open,
    onOpenChange,
  }: {
    children: ReactNode
    open: boolean
    onOpenChange: (open: boolean) => void
  }) =>
    open ? (
      <div data-testid="dialog-root">
        <button type="button" onClick={() => onOpenChange(false)}>
          模拟外部关闭
        </button>
        {children}
      </div>
    ) : null,
  DialogContent: ({ children }: { children: ReactNode }) => <section>{children}</section>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: ReactNode }) => <footer>{children}</footer>,
}))

vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    id,
    checked,
    onCheckedChange,
  }: {
    id?: string
    checked?: boolean
    onCheckedChange: (checked: boolean) => void
  }) => (
    <input
      id={id}
      type="checkbox"
      checked={Boolean(checked)}
      onChange={(event) => onCheckedChange(event.target.checked)}
    />
  ),
}))

vi.mock('@/components/ui/tabs', () => ({
  Tabs: ({
    children,
    onValueChange,
  }: {
    children: ReactNode
    onValueChange: (value: string) => void
  }) => (
    <div>
      <button type="button" onClick={() => onValueChange('custom')}>
        切换自定义分支
      </button>
      {children}
    </div>
  ),
  TabsList: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))

vi.mock('@/components/ui/select', () => ({
  Select: ({
    children,
    onValueChange,
  }: {
    children: ReactNode
    onValueChange: (value: string) => void
  }) => (
    <div>
      <button type="button" onClick={() => onValueChange('dev')}>
        选择 dev
      </button>
      {children}
    </div>
  ),
  SelectTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))

function makePlugin(id: string, overrides: Partial<PluginInfo> = {}): PluginInfo {
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
      keywords: ['标签一', '标签二', '标签三', '标签四'],
      plugin_type: 'extension',
      default_locale: 'zh-CN',
    },
    downloads: 12,
    rating: 4.5,
    review_count: 3,
    installed: false,
    published_at: '2026-07-25T00:00:00Z',
    updated_at: '2026-07-25T00:00:00Z',
    source: 'market',
    ...overrides,
  }
}

function makeStats(id: string, overrides: Partial<PluginStatsData> = {}): PluginStatsData {
  return {
    plugin_id: id,
    likes: 7,
    dislikes: 0,
    downloads: 99,
    rating: 4.8,
    rating_count: 10,
    ...overrides,
  }
}

function makeProgress(
  pluginId: string,
  stage: PluginLoadProgress['stage'],
  overrides: Partial<PluginLoadProgress> = {}
): PluginLoadProgress {
  return {
    operation: 'install',
    stage,
    progress: stage === 'success' ? 100 : 40,
    message: '正在克隆仓库',
    plugin_id: pluginId,
    total_plugins: 1,
    loaded_plugins: stage === 'success' ? 1 : 0,
    ...overrides,
  }
}

const gitStatus: GitStatus = { installed: true, version: '2.40.0' }
const maimaiVersion: MaimaiVersion = {
  version: '1.0.0',
  version_major: 1,
  version_minor: 0,
  version_patch: 0,
}

function cardProps(plugin: PluginInfo) {
  return {
    plugin,
    gitStatus,
    maimaiVersion,
    pluginStats: { [plugin.id]: makeStats(plugin.id) },
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
    getIncompatibleReason: vi.fn((_plugin: PluginInfo): string | null => null),
  }
}

afterEach(() => cleanup())

describe('PluginCard', () => {
  it('展示统计、标签和版本，并触发点赞、详情和安装', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha')
    const props = cardProps(plugin)
    render(<PluginCard {...props} />)

    const card = screen.getByText('插件-alpha').closest('[data-dashboard-card="true"]')
    expect(card).toHaveAttribute('data-plugin-market-card', 'true')
    expect(screen.getByText('插件-alpha')).toHaveClass('line-clamp-2')
    expect(screen.getByText('插件-alpha').parentElement).toHaveClass('h-[4.125rem]')
    const pluginIcon = screen.getByTestId('plugin-icon-alpha')
    const pluginTypeLabel = screen.getByText('通用扩展')
    expect(pluginIcon.nextElementSibling).toBe(pluginTypeLabel)
    expect(pluginTypeLabel).toHaveAttribute('data-plugin-type-label', 'true')
    expect(pluginTypeLabel).toHaveClass('text-primary', 'text-xs')
    expect(pluginTypeLabel.parentElement).toHaveClass('h-[4.125rem]', 'w-12')
    expect(pluginTypeLabel).not.toHaveAttribute('data-dashboard-badge')
    expect(screen.getByText('alpha 描述')).toHaveClass('line-clamp-3', 'min-h-[3.09375rem]')
    expect(document.querySelector('[data-plugin-stat-value="downloads"]')).toHaveClass('text-primary')
    expect(document.querySelector('[data-plugin-stat-value="rating"]')).toHaveClass('text-primary')
    expect(document.querySelector('[data-plugin-stat-value="likes"]')).toHaveClass('text-primary')
    expect(document.querySelector('[data-plugin-stat-value="reviews"]')).toHaveClass('text-primary')
    expect(screen.getByText('+1')).toBeInTheDocument()
    expect(screen.getByText('v1.2.0 · 测试作者')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '点赞' }))
    await user.click(screen.getByRole('button', { name: '查看详情' }))
    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(props.onLike).toHaveBeenCalledWith(plugin)
    expect(props.onDetail).toHaveBeenCalledWith(plugin)
    expect(props.onInstall).toHaveBeenCalledWith(plugin)
  })

  it('统计数值为零时不使用主题橙色', () => {
    const plugin = makePlugin('zero', { downloads: 0, rating: 0 })
    const props = cardProps(plugin)
    props.pluginStats = {
      zero: makeStats('zero', { downloads: 0, rating: 0, rating_count: 0, likes: 0 }),
    }
    render(<PluginCard {...props} />)

    expect(document.querySelector('[data-plugin-stat-value="downloads"]')).not.toHaveClass('text-primary')
    expect(document.querySelector('[data-plugin-stat-value="rating"]')).not.toHaveClass('text-primary')
    expect(document.querySelector('[data-plugin-stat-value="likes"]')).not.toHaveClass('text-primary')
    expect(document.querySelector('[data-plugin-stat-value="reviews"]')).not.toHaveClass('text-primary')
  })

  it('不兼容或其他插件正在安装时禁用安装并暴露原因', () => {
    const plugin = makePlugin('alpha')
    const props = cardProps(plugin)
    props.checkPluginCompatibility.mockReturnValue(false)
    props.getIncompatibleReason.mockReturnValue('版本过低')
    render(<PluginCard {...props} isAnyPluginInstalling />)

    expect(screen.getByRole('button', { name: '安装' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '安装' })).toHaveAttribute('title', '版本过低')
  })

  it('已安装插件按更新状态触发更新或卸载', async () => {
    const user = userEvent.setup()
    const plugin = makePlugin('alpha', { installed: true, installed_version: '1.0.0' })
    const props = cardProps(plugin)
    props.needsUpdate.mockReturnValue(true)
    const { rerender } = render(<PluginCard {...props} />)

    await user.click(screen.getByRole('button', { name: '更新' }))
    expect(props.onUpdate).toHaveBeenCalledWith(plugin)

    props.needsUpdate.mockReturnValue(false)
    rerender(<PluginCard {...props} />)
    await user.click(screen.getByRole('button', { name: '卸载' }))
    expect(props.onUninstall).toHaveBeenCalledWith(plugin)
  })

  it('显示安装进度成功和失败状态', () => {
    const plugin = makePlugin('alpha')
    const props = cardProps(plugin)
    const { rerender } = render(
      <PluginCard {...props} loadProgress={makeProgress('alpha', 'success')} />
    )
    expect(screen.getByText('安装完成')).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()

    rerender(
      <PluginCard {...props} loadProgress={makeProgress('alpha', 'error', { error: '克隆失败' })} />
    )
    expect(screen.getByText('安装失败')).toBeInTheDocument()
    expect(screen.getByText('克隆失败')).toBeInTheDocument()
  })
})

describe('InstallDialog', () => {
  it('默认从 main 分支安装，并允许未开始时取消', async () => {
    const user = userEvent.setup()
    const onInstall = vi.fn()
    const onOpenChange = vi.fn()
    render(
      <InstallDialog
        open
        plugin={makePlugin('alpha')}
        loadProgress={null}
        onOpenChange={onOpenChange}
        onInstall={onInstall}
      />
    )
    expect(screen.getByText('安装 插件-alpha')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(onInstall).toHaveBeenCalledWith('main')
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('高级模式可提交自定义分支，但忽略纯空白分支', async () => {
    const user = userEvent.setup()
    const onInstall = vi.fn()
    render(
      <InstallDialog
        open
        plugin={makePlugin('alpha')}
        loadProgress={null}
        onOpenChange={vi.fn()}
        onInstall={onInstall}
      />
    )
    await user.click(screen.getByLabelText('高级选项'))
    await user.click(screen.getByRole('button', { name: '切换自定义分支' }))
    const input = screen.getByPlaceholderText('输入分支名称，例如: feature/new-feature')
    await user.type(input, '   ')
    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(onInstall).not.toHaveBeenCalled()

    await user.clear(input)
    await user.type(input, 'feature/new-ui')
    await user.click(screen.getByRole('button', { name: '安装' }))
    expect(onInstall).toHaveBeenCalledWith('feature/new-ui')
  })

  it('安装中禁止关闭，成功后只提供关闭按钮', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const plugin = makePlugin('alpha')
    const { rerender } = render(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('alpha', 'loading')}
        onOpenChange={onOpenChange}
        onInstall={vi.fn()}
      />
    )
    expect(screen.getByText('正在安装')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '安装中' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '模拟外部关闭' }))
    expect(onOpenChange).not.toHaveBeenCalled()

    rerender(
      <InstallDialog
        open
        plugin={plugin}
        loadProgress={makeProgress('alpha', 'success')}
        onOpenChange={onOpenChange}
        onInstall={vi.fn()}
      />
    )
    expect(screen.queryByRole('button', { name: '安装' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument()
  })
})

describe('插件标签页过滤', () => {
  function tabProps(plugins: PluginInfo[]) {
    return {
      plugins,
      searchQuery: '',
      pluginTypeFilter: 'all',
      showCompatibleOnly: false,
      gitStatus,
      maimaiVersion,
      pluginStats: Object.fromEntries(plugins.map((plugin) => [plugin.id, makeStats(plugin.id)])),
      loadProgress: null,
      likingPluginIds: new Set<string>(),
      onInstall: vi.fn(),
      onLike: vi.fn(),
      onUpdate: vi.fn(),
      onUninstall: vi.fn(),
      onDetail: vi.fn(),
      checkPluginCompatibility: vi.fn((plugin: PluginInfo) => plugin.id !== 'bad'),
      needsUpdate: vi.fn((plugin: PluginInfo) => plugin.id === 'update'),
      getStatusBadge: vi.fn(() => null),
      getIncompatibleReason: vi.fn(() => null),
    }
  }

  it('InstalledTab 只展示已安装并满足搜索、类型和兼容性条件的插件', () => {
    const installed = makePlugin('installed', { installed: true })
    const bad = makePlugin('bad', { installed: true })
    const uninstalled = makePlugin('uninstalled')
    const props = tabProps([installed, bad, uninstalled])
    render(<InstalledTab {...props} showCompatibleOnly searchQuery="installed" />)

    expect(screen.getByText('插件-installed')).toBeInTheDocument()
    expect(screen.queryByText('插件-bad')).not.toBeInTheDocument()
    expect(screen.queryByText('插件-uninstalled')).not.toBeInTheDocument()
  })

  it('UpdatesTab 只展示已安装且版本需要更新的插件', () => {
    const update = makePlugin('update', { installed: true })
    const current = makePlugin('current', { installed: true })
    render(<UpdatesTab {...tabProps([update, current])} />)
    expect(screen.getByText('插件-update')).toBeInTheDocument()
    expect(screen.queryByText('插件-current')).not.toBeInTheDocument()
  })

  it('MarketplaceTab 排除本地插件和已安装插件，并按下载量排序', () => {
    const low = makePlugin('low', { downloads: 1 })
    const high = makePlugin('high', { downloads: 100 })
    const local = makePlugin('local', { source: 'local' })
    const installed = makePlugin('installed', { installed: true })
    const props = tabProps([low, high, local, installed])
    props.pluginStats = {
      low: makeStats('low', { downloads: 1 }),
      high: makeStats('high', { downloads: 100 }),
    }
    render(
      <MarketplaceTab {...props} hideInstalledPlugins sortBy="downloads" pluginProgressById={{}} />
    )

    const names = screen.getAllByText(/^插件-(high|low)$/).map((element) => element.textContent)
    expect(names).toEqual(['插件-high', '插件-low'])
    expect(screen.queryByText('插件-local')).not.toBeInTheDocument()
    expect(screen.queryByText('插件-installed')).not.toBeInTheDocument()
  })
})
