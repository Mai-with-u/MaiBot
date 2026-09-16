import type { ReactNode } from 'react'

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginMarketplacePage } from '../PluginMarketplacePage'
import type { InstalledPlugin, PluginLoadProgress } from '@/lib/plugin-api'
import type { PluginInfo, PluginManifest } from '@/types/plugin'
import * as pluginApi from '@/lib/plugin-api'
import * as pluginStatsApi from '@/lib/plugin-stats'
import { PLUGIN_MARKET_VIEW_STATE_KEY } from '@/lib/plugin-market-navigation'

// toast 与 navigate 使用 hoisted 稳定引用：toast 位于页面 useEffect 依赖数组中，
// 引用不稳定会导致初始化 effect 反复执行
const { toastMock, navigateMock } = vi.hoisted(() => ({
  toastMock: vi.fn(),
  navigateMock: vi.fn(),
}))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))

// 重启上下文与遮罩层：页面仅作为容器使用，桩掉避免引入 system-api 链路
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))

// 插件市场 API：全部打桩，禁止真实请求与 WebSocket 连接
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

vi.mock('@/lib/plugin-stats', () => ({
  getCachedPluginStatsSummary: vi.fn(),
  getPluginStatsSummary: vi.fn(),
  likePlugin: vi.fn(),
  recordPluginDownload: vi.fn(),
}))

interface MarketplaceTabStubProps {
  plugins: PluginInfo[]
  searchQuery: string
  pluginTypeFilter: string
  hideInstalledPlugins: boolean
  sortBy: string
  pluginStats: Record<string, { likes: number }>
  onInstall: (plugin: PluginInfo) => void
  onLike: (plugin: PluginInfo) => void
  onUpdate: (plugin: PluginInfo) => void
  onUninstall: (plugin: PluginInfo) => void
  onDetail: (plugin: PluginInfo) => void
  checkPluginCompatibility: (plugin: PluginInfo) => boolean
  needsUpdate: (plugin: PluginInfo) => boolean
  getStatusBadge: (plugin: PluginInfo) => ReactNode
  getIncompatibleReason: (plugin: PluginInfo) => string | null
}

// 市场标签页桩：透出页面传入的插件数据、筛选状态与全部编排回调
vi.mock('../MarketplaceTab', () => ({
  MarketplaceTab: (props: MarketplaceTabStubProps) => (
    <div
      data-testid="marketplace-tab"
      data-search={props.searchQuery}
      data-type-filter={props.pluginTypeFilter}
      data-sort-by={props.sortBy}
      data-hide-installed={String(props.hideInstalledPlugins)}
    >
      {props.plugins.map((plugin) => {
        const stats = props.pluginStats[plugin.manifest?.id ?? plugin.id]
        return (
          <div
            key={plugin.id}
            data-testid={`plugin-${plugin.id}`}
            data-installed={String(plugin.installed)}
            data-installed-version={plugin.installed_version ?? ''}
            data-source={plugin.source ?? 'market'}
            data-compatible={String(props.checkPluginCompatibility(plugin))}
            data-needs-update={String(props.needsUpdate(plugin))}
            data-likes={stats ? String(stats.likes) : ''}
          >
            <span data-testid={`badge-${plugin.id}`}>{props.getStatusBadge(plugin)}</span>
            <span data-testid={`reason-${plugin.id}`}>{props.getIncompatibleReason(plugin) ?? ''}</span>
            <button type="button" onClick={() => props.onInstall(plugin)}>{`install-${plugin.id}`}</button>
            <button type="button" onClick={() => props.onLike(plugin)}>{`like-${plugin.id}`}</button>
            <button type="button" onClick={() => props.onUpdate(plugin)}>{`update-${plugin.id}`}</button>
            <button type="button" onClick={() => props.onUninstall(plugin)}>{`uninstall-${plugin.id}`}</button>
            <button type="button" onClick={() => props.onDetail(plugin)}>{`detail-${plugin.id}`}</button>
          </div>
        )
      })}
    </div>
  ),
}))

interface InstallDialogStubProps {
  open: boolean
  plugin: PluginInfo | null
  loadProgress: { stage: string } | null
  onOpenChange: (open: boolean) => void
  onInstall: (branch: string) => void
}

// 安装对话框桩：透出打开状态与安装进度，并提供触发 onInstall/onOpenChange 的按钮
vi.mock('../InstallDialog', () => ({
  InstallDialog: ({ open, plugin, loadProgress, onOpenChange, onInstall }: InstallDialogStubProps) =>
    open ? (
      <div
        data-testid="install-dialog"
        data-plugin-id={plugin?.id ?? ''}
        data-progress-stage={loadProgress?.stage ?? ''}
      >
        <button type="button" onClick={() => onInstall('main')}>confirm-install-main</button>
        <button type="button" onClick={() => onInstall('   ')}>confirm-install-blank</button>
        <button type="button" onClick={() => onOpenChange(false)}>close-install-dialog</button>
      </div>
    ) : null,
}))

// 插件详情页较重（Markdown/统计/图表），仅记录传入的 pluginId 与关闭回调
vi.mock('../../plugin-detail', () => ({
  PluginDetailPage: ({ pluginId, onClose }: { pluginId: string; onClose?: () => void }) => (
    <div data-testid="plugin-detail" data-plugin-id={pluginId}>
      <button type="button" onClick={onClose}>close-detail</button>
    </div>
  ),
}))

// 构造市场插件清单数据
function makeManifest(id: string, overrides: Partial<PluginManifest> = {}): PluginManifest {
  return {
    manifest_version: 2,
    id,
    name: `插件${id}`,
    version: '1.2.0',
    description: `${id} 的描述`,
    author: { name: '测试作者' },
    license: 'MIT',
    host_application: { min_version: '1.0.0' },
    repository_url: `https://example.com/${id}.git`,
    keywords: [],
    plugin_type: 'extension',
    default_locale: 'zh-CN',
    ...overrides,
  }
}

function makeMarketPlugin(
  id: string,
  overrides: Partial<PluginInfo> = {},
  manifestOverrides: Partial<PluginManifest> = {}
): PluginInfo {
  return {
    id,
    manifest: makeManifest(id, manifestOverrides),
    downloads: 10,
    rating: 4,
    review_count: 2,
    installed: false,
    published_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

// 构造后端返回的已安装插件数据
function makeInstalledPlugin(id: string, version: string): InstalledPlugin {
  return {
    id,
    manifest: {
      manifest_version: 2,
      id,
      name: `插件${id}`,
      version,
      description: `${id} 的描述`,
      author: { name: '测试作者' },
      license: 'MIT',
      host_application: { min_version: '1.0.0' },
    },
    path: `/plugins/${id}`,
  }
}

function makeProgress(overrides: Partial<PluginLoadProgress> = {}): PluginLoadProgress {
  return {
    operation: 'fetch',
    stage: 'loading',
    progress: 0,
    message: '',
    total_plugins: 0,
    loaded_plugins: 0,
    ...overrides,
  }
}

// 读取「全部插件 N」计数徽章文案
function getCountBadgeText(): string {
  const badge = document.querySelector('[data-plugin-market-count-badge="true"]')
  return badge?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
}

// 捕获页面注册的 WebSocket 进度/错误回调，供用例主动驱动
let progressHandler: ((progress: PluginLoadProgress) => void) | null = null
let wsErrorHandler: ((error: Error) => void) | null = null

beforeEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  progressHandler = null
  wsErrorHandler = null

  vi.mocked(pluginApi.getCachedPluginList).mockReturnValue(null)
  vi.mocked(pluginApi.checkGitStatus).mockResolvedValue({ installed: true, version: 'git version 2.44.0' })
  vi.mocked(pluginApi.getMaimaiVersion).mockResolvedValue({
    version: '1.2.0',
    version_major: 1,
    version_minor: 2,
    version_patch: 0,
  })
  vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
    makeMarketPlugin('plugin-a', {}, { description: '天气查询插件' }),
    makeMarketPlugin('plugin-b', {}, { description: '音乐播放插件' }),
  ])
  vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([])
  vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(true)
  vi.mocked(pluginApi.checkPluginInstalled).mockImplementation((pluginId, installedPlugins) =>
    installedPlugins.some((item) => item.id === pluginId)
  )
  vi.mocked(pluginApi.getInstalledPluginVersion).mockImplementation((pluginId, installedPlugins) => {
    const found = installedPlugins.find((item) => item.id === pluginId)
    if (!found) return undefined
    return 'manifest' in found ? found.manifest.version : found.version
  })
  vi.mocked(pluginApi.connectPluginProgressWebSocket).mockImplementation(async (onProgress, onError) => {
    progressHandler = onProgress
    wsErrorHandler = onError ?? null
    return async () => {}
  })
  vi.mocked(pluginApi.installPlugin).mockResolvedValue({ success: true, message: '' })
  vi.mocked(pluginApi.uninstallPlugin).mockResolvedValue({ success: true, message: '' })
  vi.mocked(pluginApi.updatePlugin).mockResolvedValue({
    success: true,
    message: '',
    old_version: '1.0.0',
    new_version: '1.2.0',
  })

  vi.mocked(pluginStatsApi.getCachedPluginStatsSummary).mockReturnValue(null)
  vi.mocked(pluginStatsApi.getPluginStatsSummary).mockResolvedValue({})
  vi.mocked(pluginStatsApi.likePlugin).mockResolvedValue({
    success: true,
    likes: 1,
    dislikes: 0,
    liked: true,
    disliked: false,
  })
  vi.mocked(pluginStatsApi.recordPluginDownload).mockResolvedValue({
    success: true,
    counted: true,
    downloads: 11,
  })
})

afterEach(() => {
  cleanup()
})

async function renderPage(props: { embedded?: boolean } = {}) {
  render(<PluginMarketplacePage {...props} />)
  await screen.findByTestId('marketplace-tab')
}

describe('PluginMarketplacePage 初始加载与数据合并', () => {
  it('并发拉取 Git 状态、麦麦版本、市场清单与已安装列表并渲染插件', async () => {
    await renderPage()

    expect(pluginApi.checkGitStatus).toHaveBeenCalled()
    expect(pluginApi.getMaimaiVersion).toHaveBeenCalled()
    expect(pluginApi.fetchPluginList).toHaveBeenCalled()
    expect(pluginApi.getInstalledPlugins).toHaveBeenCalledWith()
    expect(pluginStatsApi.getPluginStatsSummary).toHaveBeenCalledWith({ forceRefresh: false })

    expect(screen.getByTestId('plugin-plugin-a')).toBeInTheDocument()
    expect(screen.getByTestId('plugin-plugin-b')).toBeInTheDocument()
    expect(getCountBadgeText()).toBe('全部插件 2')
    expect(screen.queryByRole('heading', { name: '插件市场' })).not.toBeInTheDocument()
  })

  it('合并已安装信息：市场插件标记已安装版本，本地独有插件追加为 local 来源', async () => {
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      makeInstalledPlugin('plugin-a', '1.0.0'),
      makeInstalledPlugin('local-x', '0.5.0'),
    ])

    await renderPage()

    const pluginA = screen.getByTestId('plugin-plugin-a')
    expect(pluginA).toHaveAttribute('data-installed', 'true')
    expect(pluginA).toHaveAttribute('data-installed-version', '1.0.0')

    const localX = screen.getByTestId('plugin-local-x')
    expect(localX).toHaveAttribute('data-source', 'local')
    expect(localX).toHaveAttribute('data-installed', 'true')

    // 计数排除 local 来源与已安装插件：仅剩 plugin-b
    expect(getCountBadgeText()).toBe('全部插件 1')
  })

  it('存在缓存清单时先渲染缓存内容，拉取完成后替换为最新清单', async () => {
    vi.mocked(pluginApi.getCachedPluginList).mockReturnValue([makeMarketPlugin('cached-x')])
    vi.mocked(pluginStatsApi.getCachedPluginStatsSummary).mockReturnValue({
      'cached-x': { plugin_id: 'cached-x', likes: 3, dislikes: 0, downloads: 9, rating: 5, rating_count: 2 },
    })

    render(<PluginMarketplacePage />)

    // 缓存路径为同步执行：渲染后立即可见缓存插件与缓存统计
    const cached = screen.getByTestId('plugin-cached-x')
    expect(cached).toHaveAttribute('data-likes', '3')

    // 拉取完成后替换为最新市场清单
    await screen.findByTestId('plugin-plugin-a')
    await waitFor(() => expect(screen.queryByTestId('plugin-cached-x')).not.toBeInTheDocument())
    // 存在缓存统计时强制刷新统计
    expect(pluginStatsApi.getPluginStatsSummary).toHaveBeenCalledWith({ forceRefresh: true })
  })

  it('市场清单拉取失败时显示错误卡片并弹出提示', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockRejectedValue(new Error('网络中断'))

    render(<PluginMarketplacePage />)

    expect(await screen.findByText('网络中断')).toBeInTheDocument()
    expect(screen.getByText('加载失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: '加载失败',
      description: '网络中断',
      variant: 'destructive',
    })
    expect(screen.queryByTestId('marketplace-tab')).not.toBeInTheDocument()
  })

  it('Git 未安装时显示警告卡片，并阻断安装与更新操作', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.checkGitStatus).mockResolvedValue({
      installed: false,
      error: '未检测到 git 可执行文件',
    })

    await renderPage()

    // 警告卡片与初始 toast
    expect(screen.getByText('Git 未安装')).toBeInTheDocument()
    expect(screen.getByText('未检测到 git 可执行文件')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: 'Git 未安装',
      description: '未检测到 git 可执行文件',
      variant: 'destructive',
    })

    // 安装被阻断，不打开安装对话框
    await user.click(screen.getByText('install-plugin-a'))
    expect(toastMock).toHaveBeenCalledWith({
      title: '无法安装',
      description: 'Git 未安装',
      variant: 'destructive',
    })
    expect(screen.queryByTestId('install-dialog')).not.toBeInTheDocument()

    // 更新同样被阻断
    await user.click(screen.getByText('update-plugin-a'))
    expect(toastMock).toHaveBeenCalledWith({
      title: '无法更新',
      description: 'Git 未安装',
      variant: 'destructive',
    })
    expect(pluginApi.updatePlugin).not.toHaveBeenCalled()
  })
})

describe('PluginMarketplacePage 兼容性与状态徽章', () => {
  it('manifest v1 插件在麦麦 1.x 下判定不兼容并阻断安装', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('legacy-a', {}, { manifest_version: 1 }),
    ])

    await renderPage()

    const entry = screen.getByTestId('plugin-legacy-a')
    expect(entry).toHaveAttribute('data-compatible', 'false')
    expect(screen.getByTestId('badge-legacy-a')).toHaveTextContent('不兼容')
    expect(screen.getByTestId('reason-legacy-a')).toHaveTextContent(
      '该插件使用旧版 manifest (v1)，已不被麦麦 1.2.0 支持'
    )

    await user.click(screen.getByText('install-legacy-a'))
    expect(toastMock).toHaveBeenCalledWith({
      title: '无法安装',
      description: '该插件使用旧版 manifest (v1)，已不被麦麦 1.2.0 支持',
      variant: 'destructive',
    })
    expect(screen.queryByTestId('install-dialog')).not.toBeInTheDocument()
  })

  it('host_application 版本范围不满足时给出范围提示文案', async () => {
    vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(false)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('range-a', {}, { host_application: { min_version: '2.0.0', max_version: '3.0.0' } }),
    ])

    await renderPage()

    expect(screen.getByTestId('badge-range-a')).toHaveTextContent('不兼容')
    expect(screen.getByTestId('reason-range-a')).toHaveTextContent(
      '不兼容当前版本 (需要 2.0.0 - 3.0.0，当前 1.2.0)'
    )
    expect(pluginApi.isPluginCompatible).toHaveBeenCalledWith(
      '2.0.0',
      '3.0.0',
      expect.objectContaining({ version: '1.2.0' })
    )
  })

  it('版本徽章：市场版本较新显示可更新，同版本或本地较新显示已安装', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('up-a'),
      makeMarketPlugin('same-b'),
      makeMarketPlugin('newer-c'),
    ])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      makeInstalledPlugin('up-a', '1.0.0'),
      makeInstalledPlugin('same-b', '1.2.0'),
      makeInstalledPlugin('newer-c', '2.0.0'),
    ])

    await renderPage()

    // 市场 1.2.0 > 本地 1.0.0：可更新
    expect(screen.getByTestId('badge-up-a')).toHaveTextContent('可更新')
    expect(screen.getByTestId('plugin-up-a')).toHaveAttribute('data-needs-update', 'true')
    // 版本一致：已安装
    expect(screen.getByTestId('badge-same-b')).toHaveTextContent('已安装')
    expect(screen.getByTestId('plugin-same-b')).toHaveAttribute('data-needs-update', 'false')
    // 本地 2.0.0 > 市场 1.2.0：仍显示已安装
    expect(screen.getByTestId('badge-newer-c')).toHaveTextContent('已安装')
    expect(screen.getByTestId('plugin-newer-c')).toHaveAttribute('data-needs-update', 'false')
  })
})

describe('PluginMarketplacePage 安装流程', () => {
  it('安装成功：调用 installPlugin 并记录下载、刷新已安装状态', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByText('install-plugin-a'))
    expect(screen.getByTestId('install-dialog')).toHaveAttribute('data-plugin-id', 'plugin-a')

    // 安装完成后强制刷新拿到新安装列表
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    await user.click(screen.getByText('confirm-install-main'))

    await waitFor(() =>
      expect(pluginApi.installPlugin).toHaveBeenCalledWith(
        'plugin-a',
        'https://example.com/plugin-a.git',
        'main'
      )
    )
    expect(pluginStatsApi.recordPluginDownload).toHaveBeenCalledWith('plugin-a')
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装成功',
        description: '插件plugin-a 已成功安装',
      })
    )
    expect(pluginApi.getInstalledPlugins).toHaveBeenCalledWith({ forceRefresh: true })

    const pluginA = screen.getByTestId('plugin-plugin-a')
    await waitFor(() => expect(pluginA).toHaveAttribute('data-installed', 'true'))
    expect(pluginA).toHaveAttribute('data-installed-version', '1.2.0')
  })

  it('分支名称为空白时提示且不调用 installPlugin', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByText('install-plugin-a'))
    await user.click(screen.getByText('confirm-install-blank'))

    expect(toastMock).toHaveBeenCalledWith({
      title: '分支名称不能为空',
      variant: 'destructive',
    })
    expect(pluginApi.installPlugin).not.toHaveBeenCalled()
  })

  it('安装失败：弹出错误提示并将进度置为 error', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.installPlugin).mockRejectedValue(new Error('git clone 失败'))
    await renderPage()

    await user.click(screen.getByText('install-plugin-a'))
    await user.click(screen.getByText('confirm-install-main'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装失败',
        description: 'git clone 失败',
        variant: 'destructive',
      })
    )
    expect(screen.getByTestId('install-dialog')).toHaveAttribute('data-progress-stage', 'error')
  })

  it('空闲时可关闭安装对话框，安装进行中禁止关闭', async () => {
    const user = userEvent.setup()
    await renderPage()

    // 未开始安装时可以正常关闭
    await user.click(screen.getByText('install-plugin-a'))
    await user.click(screen.getByText('close-install-dialog'))
    expect(screen.queryByTestId('install-dialog')).not.toBeInTheDocument()

    // 安装进行中（Promise 挂起）时关闭被拦截
    vi.mocked(pluginApi.installPlugin).mockImplementation(() => new Promise(() => {}))
    await user.click(screen.getByText('install-plugin-a'))
    await user.click(screen.getByText('confirm-install-main'))
    await waitFor(() =>
      expect(screen.getByTestId('install-dialog')).toHaveAttribute('data-progress-stage', 'loading')
    )
    await user.click(screen.getByText('close-install-dialog'))
    expect(screen.getByTestId('install-dialog')).toBeInTheDocument()
  })
})

describe('PluginMarketplacePage 卸载与更新', () => {
  it('卸载成功：调用 uninstallPlugin 并刷新为未安装状态', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    await renderPage()
    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-installed', 'true')

    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([])
    await user.click(screen.getByText('uninstall-plugin-a'))

    await waitFor(() => expect(pluginApi.uninstallPlugin).toHaveBeenCalledWith('plugin-a'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '卸载成功',
        description: '插件plugin-a 已成功卸载',
      })
    )
    expect(pluginApi.getInstalledPlugins).toHaveBeenCalledWith({ forceRefresh: true })
    await waitFor(() =>
      expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-installed', 'false')
    )
  })

  it('更新成功：调用 updatePlugin 并提示新旧版本', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.0.0')])
    await renderPage()
    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-needs-update', 'true')

    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    await user.click(screen.getByText('update-plugin-a'))

    await waitFor(() =>
      expect(pluginApi.updatePlugin).toHaveBeenCalledWith(
        'plugin-a',
        'https://example.com/plugin-a.git',
        'main'
      )
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '更新成功',
        description: '插件plugin-a 已从 1.0.0 更新到 1.2.0',
      })
    )
    await waitFor(() =>
      expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-installed-version', '1.2.0')
    )
  })

  it('卸载失败：弹出错误提示', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    vi.mocked(pluginApi.uninstallPlugin).mockRejectedValue(new Error('文件被占用'))
    await renderPage()

    await user.click(screen.getByText('uninstall-plugin-a'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '卸载失败',
        description: '文件被占用',
        variant: 'destructive',
      })
    )
  })
})

describe('PluginMarketplacePage 点赞', () => {
  it('点赞成功后按插件 ID 更新统计数据', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginStatsApi.likePlugin).mockResolvedValue({
      success: true,
      likes: 6,
      dislikes: 1,
      liked: true,
      disliked: false,
    })
    await renderPage()

    await user.click(screen.getByText('like-plugin-a'))

    await waitFor(() => expect(pluginStatsApi.likePlugin).toHaveBeenCalledWith('plugin-a'))
    await waitFor(() =>
      expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-likes', '6')
    )
  })

  it('点赞失败时弹出错误提示且不更新统计', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginStatsApi.likePlugin).mockResolvedValue({
      success: false,
      error: '请求过于频繁',
    })
    await renderPage()

    await user.click(screen.getByText('like-plugin-a'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '点赞失败',
        description: '请求过于频繁',
        variant: 'destructive',
      })
    )
    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-likes', '')
  })
})

describe('PluginMarketplacePage 视图状态与交互', () => {
  it('重启提示可关闭并写入 localStorage，已关闭时不再显示', async () => {
    const user = userEvent.setup()
    await renderPage()

    expect(screen.getByText('重启麦麦')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '我知道了' }))
    expect(screen.queryByRole('button', { name: '我知道了' })).not.toBeInTheDocument()
    expect(window.localStorage.getItem('plugins-restart-notice-dismissed')).toBe('true')

    // 已关闭状态下重新进入页面不再显示提示
    cleanup()
    await renderPage()
    expect(screen.queryByRole('button', { name: '我知道了' })).not.toBeInTheDocument()
  })

  it('搜索输入过滤计数并写入会话存储', async () => {
    const user = userEvent.setup()
    await renderPage()
    expect(getCountBadgeText()).toBe('全部插件 2')

    await user.type(screen.getByPlaceholderText('搜索插件...'), '天气')

    // 仅描述包含「天气」的 plugin-a 命中
    expect(getCountBadgeText()).toBe('全部插件 1')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-search', '天气')
    const saved = JSON.parse(window.sessionStorage.getItem(PLUGIN_MARKET_VIEW_STATE_KEY) ?? '{}')
    expect(saved.searchQuery).toBe('天气')
  })

  it('恢复会话存储中的视图状态，非法值回退为默认值', async () => {
    window.sessionStorage.setItem(
      PLUGIN_MARKET_VIEW_STATE_KEY,
      JSON.stringify({
        searchQuery: '预置搜索',
        pluginTypeFilter: 'chat',
        marketplaceSortBy: 'downloads',
        showInstalledPlugins: true,
      })
    )
    await renderPage()

    expect(screen.getByPlaceholderText('搜索插件...')).toHaveValue('预置搜索')
    const tab = screen.getByTestId('marketplace-tab')
    expect(tab).toHaveAttribute('data-type-filter', 'chat')
    expect(tab).toHaveAttribute('data-sort-by', 'downloads')
    // showInstalledPlugins=true 时不隐藏已安装插件
    expect(tab).toHaveAttribute('data-hide-installed', 'false')

    // 非法的类型/排序值回退为默认值
    cleanup()
    window.sessionStorage.setItem(
      PLUGIN_MARKET_VIEW_STATE_KEY,
      JSON.stringify({ pluginTypeFilter: 'bogus', marketplaceSortBy: 'bogus' })
    )
    await renderPage()
    const fallbackTab = screen.getByTestId('marketplace-tab')
    expect(fallbackTab).toHaveAttribute('data-type-filter', 'all')
    expect(fallbackTab).toHaveAttribute('data-sort-by', 'default')
  })

  it('设置按钮跳转镜像设置页，embedded 模式跳转 embed 路由', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('button', { name: '设置' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugin-mirrors' })

    cleanup()
    navigateMock.mockClear()
    await renderPage({ embedded: true })
    await user.click(screen.getByRole('button', { name: '设置' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugin-mirrors/embed' })
  })

  it('显示已安装开关切换后计数包含已安装插件', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    await renderPage()

    expect(getCountBadgeText()).toBe('全部插件 1')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-hide-installed', 'true')

    await user.click(screen.getByRole('switch'))

    expect(getCountBadgeText()).toBe('全部插件 2')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-hide-installed', 'false')
  })

  it('点击详情打开插件详情对话框并可关闭', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByText('detail-plugin-a'))
    const detail = await screen.findByTestId('plugin-detail')
    expect(detail).toHaveAttribute('data-plugin-id', 'plugin-a')

    await user.click(screen.getByText('close-detail'))
    await waitFor(() => expect(screen.queryByTestId('plugin-detail')).not.toBeInTheDocument())
  })
})

describe('PluginMarketplacePage WebSocket 进度', () => {
  it('fetch loading 进度显示加载指示条与消息', async () => {
    await renderPage()
    expect(progressHandler).not.toBeNull()

    act(() => {
      progressHandler?.(makeProgress({ progress: 30, message: '正在同步插件清单' }))
    })

    expect(screen.getByText('加载插件市场')).toBeInTheDocument()
    expect(screen.getByText('正在同步插件清单')).toBeInTheDocument()
  })

  it('fetch error 进度显示错误卡片并结束加载', async () => {
    await renderPage()

    act(() => {
      progressHandler?.(makeProgress({ stage: 'error', message: '失败', error: '镜像源不可用' }))
    })

    // 进度卡片与主区域错误卡片均展示错误信息
    expect(screen.getAllByText('镜像源不可用').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument()
    expect(screen.queryByText('加载插件市场')).not.toBeInTheDocument()
  })

  it('WebSocket 连接失败时弹出提示', async () => {
    await renderPage()
    expect(wsErrorHandler).not.toBeNull()

    act(() => {
      wsErrorHandler?.(new Error('连接被拒绝'))
    })

    expect(toastMock).toHaveBeenCalledWith({
      title: 'WebSocket 连接失败',
      description: '无法实时显示加载进度',
      variant: 'destructive',
    })
  })
})

function polyfillPointerCapture() {
  const proto = Element.prototype as Element & {
    hasPointerCapture?: (pointerId: number) => boolean
    setPointerCapture?: (pointerId: number) => void
    releasePointerCapture?: (pointerId: number) => void
  }
  if (typeof proto.hasPointerCapture !== 'function') {
    proto.hasPointerCapture = () => false
  }
  if (typeof proto.setPointerCapture !== 'function') {
    proto.setPointerCapture = () => {}
  }
  if (typeof proto.releasePointerCapture !== 'function') {
    proto.releasePointerCapture = () => {}
  }
}

async function selectMarketplaceOption(label: string, optionName: string) {
  polyfillPointerCapture()
  const trigger = screen.getByRole('combobox', { name: label })
  fireEvent.pointerDown(trigger)
  fireEvent.click(trigger)
  const option = await screen.findByRole('option', { name: optionName })
  fireEvent.pointerDown(option)
  fireEvent.click(option)
}

describe('PluginMarketplacePage 错误态与空市场', () => {
  it('市场清单为空时仍渲染标签页且计数为 0', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([])

    await renderPage()

    expect(screen.getByTestId('marketplace-tab')).toBeInTheDocument()
    expect(screen.queryByTestId('plugin-plugin-a')).not.toBeInTheDocument()
    expect(getCountBadgeText()).toBe('全部插件 0')
  })

  it('仅有本地插件时计数不计入市场插件', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('local-only', '0.1.0')])

    await renderPage()

    expect(screen.getByTestId('plugin-local-only')).toHaveAttribute('data-source', 'local')
    expect(getCountBadgeText()).toBe('全部插件 0')
  })

  it('清单尚未返回时显示加载指示', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockImplementation(() => new Promise(() => {}))

    render(<PluginMarketplacePage />)

    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.queryByTestId('marketplace-tab')).not.toBeInTheDocument()
  })

  it('加载失败后点击重新加载会刷新页面', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.fetchPluginList).mockRejectedValue(new Error('网络中断'))
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })

    render(<PluginMarketplacePage />)
    await user.click(await screen.findByRole('button', { name: '重新加载' }))

    expect(reload).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('市场清单以非 Error 拒绝时使用默认加载失败文案', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockRejectedValue('upstream 500')

    render(<PluginMarketplacePage />)

    expect(await screen.findByRole('heading', { name: '加载失败' })).toBeInTheDocument()
    expect(screen.getByText('加载失败', { selector: 'p' })).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({
      title: '加载失败',
      description: '加载失败',
      variant: 'destructive',
    })
  })

  it('Git 未安装且无错误信息时展示默认说明', async () => {
    vi.mocked(pluginApi.checkGitStatus).mockResolvedValue({ installed: false })

    await renderPage()

    expect(screen.getByText('Git 未安装')).toBeInTheDocument()
    expect(screen.getAllByText('请先安装 Git 才能使用插件安装功能').length).toBeGreaterThan(0)
    expect(toastMock).toHaveBeenCalledWith({
      title: 'Git 未安装',
      description: '请先安装 Git 才能使用插件安装功能',
      variant: 'destructive',
    })
  })

  it('统计刷新失败时仍渲染市场清单并记录警告', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(pluginStatsApi.getPluginStatsSummary).mockRejectedValue(new Error('统计服务不可用'))

    await renderPage()

    expect(screen.getByTestId('plugin-plugin-a')).toBeInTheDocument()
    await waitFor(() =>
      expect(warnSpy).toHaveBeenCalledWith('刷新插件统计失败:', expect.any(Error))
    )
  })

  it('WebSocket 订阅失败时记录错误且不影响清单渲染', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(pluginApi.connectPluginProgressWebSocket).mockRejectedValue(new Error('订阅失败'))

    await renderPage()

    expect(screen.getByTestId('plugin-plugin-a')).toBeInTheDocument()
    await waitFor(() =>
      expect(errorSpy).toHaveBeenCalledWith('WebSocket subscribe error:', expect.any(Error))
    )
  })

  it('fetch 进度缺少 error 字段时回退为加载失败', async () => {
    await renderPage()

    act(() => {
      progressHandler?.(makeProgress({ stage: 'error', message: '失败' }))
    })

    expect(await screen.findByRole('heading', { name: '加载失败' })).toBeInTheDocument()
    expect(screen.getByText('加载失败', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument()
  })

  it('fetch 进度消息为空时展示默认加载文案', async () => {
    await renderPage()

    act(() => {
      progressHandler?.(makeProgress({ message: '' }))
    })

    expect(screen.getByText('加载插件市场')).toBeInTheDocument()
    expect(screen.getByText('正在获取插件清单')).toBeInTheDocument()
  })
})

describe('PluginMarketplacePage 筛选', () => {
  it('按类型筛选与排序写入会话存储并更新计数', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('plugin-a', {}, { plugin_type: 'extension' }),
      makeMarketPlugin('plugin-b', {}, { plugin_type: 'chat' }),
    ])
    await renderPage()
    expect(getCountBadgeText()).toBe('全部插件 2')

    await selectMarketplaceOption('类型筛选', '聊天')
    expect(getCountBadgeText()).toBe('全部插件 1')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-type-filter', 'chat')

    await selectMarketplaceOption('类型筛选', '适配器')
    expect(getCountBadgeText()).toBe('全部插件 0')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-type-filter', 'adapter')

    await selectMarketplaceOption('类型筛选', '全部类型')
    expect(getCountBadgeText()).toBe('全部插件 2')

    await selectMarketplaceOption('排序', '下载最多')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-sort-by', 'downloads')
    const saved = JSON.parse(window.sessionStorage.getItem(PLUGIN_MARKET_VIEW_STATE_KEY) ?? '{}')
    expect(saved.pluginTypeFilter).toBe('all')
    expect(saved.marketplaceSortBy).toBe('downloads')
  })

  it('按名称与关键词搜索过滤计数', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('plugin-a', {}, { name: '天气助手', keywords: [] }),
      makeMarketPlugin('plugin-b', {}, { name: '音乐盒', description: '播放本地歌曲', keywords: ['量子纠缠'] }),
    ])
    await renderPage()

    await user.clear(screen.getByPlaceholderText('搜索插件...'))
    await user.type(screen.getByPlaceholderText('搜索插件...'), '天气助手')
    expect(getCountBadgeText()).toBe('全部插件 1')

    await user.clear(screen.getByPlaceholderText('搜索插件...'))
    await user.type(screen.getByPlaceholderText('搜索插件...'), '量子')
    expect(getCountBadgeText()).toBe('全部插件 1')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-search', '量子')
  })

  it('兼容性筛选排除不兼容插件，关闭后计入；无 manifest 的插件始终排除', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('plugin-a'),
      makeMarketPlugin('legacy-a', {}, { manifest_version: 1 }),
      { ...makeMarketPlugin('ghost'), manifest: undefined as unknown as PluginInfo['manifest'] },
    ])

    await renderPage()
    // 默认仅显示兼容插件：legacy / ghost 均不计入
    expect(getCountBadgeText()).toBe('全部插件 1')

    cleanup()
    window.localStorage.setItem('plugins-market-compatible-only', 'false')
    await renderPage()
    // 关闭兼容性筛选后计入 legacy，ghost 仍因缺少 manifest 被排除
    expect(getCountBadgeText()).toBe('全部插件 2')
  })
})

describe('PluginMarketplacePage 安装失败与仓库回退', () => {
  it('安装抛出非 Error 时提示未知错误', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.installPlugin).mockRejectedValue('disk full')
    await renderPage()

    await user.click(screen.getByText('install-plugin-a'))
    await user.click(screen.getByText('confirm-install-main'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装失败',
        description: '未知错误',
        variant: 'destructive',
      })
    )
    expect(screen.getByTestId('install-dialog')).toHaveAttribute('data-progress-stage', 'error')
  })

  it('缺少 repository_url 时回退 urls.repository，且无 manifest.id 时不记录下载', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('plugin-a', {}, {
        id: undefined,
        repository_url: undefined,
        urls: { repository: 'https://example.com/alt.git' },
      }),
    ])
    await renderPage()

    await user.click(screen.getByText('install-plugin-a'))
    await user.click(screen.getByText('confirm-install-main'))

    await waitFor(() =>
      expect(pluginApi.installPlugin).toHaveBeenCalledWith(
        'plugin-a',
        'https://example.com/alt.git',
        'main'
      )
    )
    expect(pluginStatsApi.recordPluginDownload).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装成功',
        description: '插件plugin-a 已成功安装',
      })
    )
  })

  it('记录下载失败不影响安装成功，成功进度超时后清除', async () => {
    const user = userEvent.setup()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(pluginStatsApi.recordPluginDownload).mockRejectedValue(new Error('统计写入失败'))
    await renderPage()

    await user.click(screen.getByText('install-plugin-a'))
    await user.click(screen.getByText('confirm-install-main'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装成功',
        description: '插件plugin-a 已成功安装',
      })
    )
    await waitFor(() =>
      expect(warnSpy).toHaveBeenCalledWith('Failed to record download:', expect.any(Error))
    )
    expect(screen.getByTestId('install-dialog')).toHaveAttribute('data-progress-stage', 'success')
    await waitFor(
      () => expect(screen.getByTestId('install-dialog')).toHaveAttribute('data-progress-stage', ''),
      { timeout: 3500 }
    )
  })
})

describe('PluginMarketplacePage 更新路径', () => {
  it('更新失败：弹出错误提示并将插件进度置为 error', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.updatePlugin).mockRejectedValue(new Error('fast-forward 失败'))
    await renderPage()

    await user.click(screen.getByText('update-plugin-a'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '更新失败',
        description: 'fast-forward 失败',
        variant: 'destructive',
      })
    )
  })

  it('更新抛出非 Error 时提示未知错误', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.updatePlugin).mockRejectedValue('conflict')
    await renderPage()

    await user.click(screen.getByText('update-plugin-a'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '更新失败',
        description: '未知错误',
        variant: 'destructive',
      })
    )
  })

  it('不兼容插件不允许更新，且 needsUpdate 为 false', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('legacy-a', {}, { manifest_version: 1, version: '2.0.0' }),
    ])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('legacy-a', '1.0.0')])
    await renderPage()

    expect(screen.getByTestId('badge-legacy-a')).toHaveTextContent('不兼容')
    expect(screen.getByTestId('plugin-legacy-a')).toHaveAttribute('data-needs-update', 'false')

    await user.click(screen.getByText('update-legacy-a'))
    expect(toastMock).toHaveBeenCalledWith({
      title: '无法更新',
      description: '该插件使用旧版 manifest (v1)，已不被麦麦 1.2.0 支持',
      variant: 'destructive',
    })
    expect(pluginApi.updatePlugin).not.toHaveBeenCalled()
  })

  it('缺少 repository_url 时更新回退 urls.repository', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('plugin-a', {}, {
        repository_url: undefined,
        urls: { repository: 'https://example.com/alt.git' },
      }),
    ])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.0.0')])
    await renderPage()

    await user.click(screen.getByText('update-plugin-a'))

    await waitFor(() =>
      expect(pluginApi.updatePlugin).toHaveBeenCalledWith(
        'plugin-a',
        'https://example.com/alt.git',
        'main'
      )
    )
  })

  it('更新进行中再次点击更新会被忽略', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.updatePlugin).mockImplementation(() => new Promise(() => {}))
    await renderPage()

    await user.click(screen.getByText('update-plugin-a'))
    await user.click(screen.getByText('update-plugin-a'))

    expect(pluginApi.updatePlugin).toHaveBeenCalledTimes(1)
  })

  it('卸载进度为 loading 时忽略卸载点击', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    await renderPage()

    act(() => {
      progressHandler?.(makeProgress({
        operation: 'uninstall',
        stage: 'loading',
        plugin_id: 'plugin-a',
        message: '正在卸载',
      }))
    })

    await user.click(screen.getByText('uninstall-plugin-a'))
    expect(pluginApi.uninstallPlugin).not.toHaveBeenCalled()
  })

  it('卸载抛出非 Error 时提示未知错误', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    vi.mocked(pluginApi.uninstallPlugin).mockRejectedValue('busy')
    await renderPage()

    await user.click(screen.getByText('uninstall-plugin-a'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '卸载失败',
        description: '未知错误',
        variant: 'destructive',
      })
    )
  })
})

describe('PluginMarketplacePage 点赞互斥与缺省错误', () => {
  it('点赞请求进行中重复点击会被忽略', async () => {
    const user = userEvent.setup()
    let resolveLike: (value: {
      success: boolean
      likes?: number
      dislikes?: number
      liked?: boolean
      disliked?: boolean
    }) => void = () => {}
    vi.mocked(pluginStatsApi.likePlugin).mockImplementation(
      () => new Promise((resolve) => {
        resolveLike = resolve
      })
    )
    await renderPage()

    await user.click(screen.getByText('like-plugin-a'))
    await user.click(screen.getByText('like-plugin-a'))
    expect(pluginStatsApi.likePlugin).toHaveBeenCalledTimes(1)

    resolveLike({ success: true, likes: 4, dislikes: 0, liked: true, disliked: false })
    await waitFor(() =>
      expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-likes', '4')
    )
  })

  it('点赞失败且无 error 字段时使用默认提示', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginStatsApi.likePlugin).mockResolvedValue({ success: false })
    await renderPage()

    await user.click(screen.getByText('like-plugin-a'))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '点赞失败',
        description: '无法提交点赞',
        variant: 'destructive',
      })
    )
  })
})

describe('PluginMarketplacePage 合并、兼容性边界与进度清理', () => {
  it('按 manifest.id 合并已安装信息，并补齐本地稀疏清单字段', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([makeMarketPlugin('plugin-a')])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      {
        ...makeInstalledPlugin('runtime-a', '1.0.0'),
        manifest: { ...makeInstalledPlugin('runtime-a', '1.0.0').manifest, id: 'plugin-a' },
      },
      // 本地安装记录的清单可能缺少 manifest_version / description / license，页面需补齐默认值
      {
        id: 'local-sparse',
        path: '/plugins/local-sparse',
        changelog: '修复崩溃',
        manifest: {
          name: '稀疏插件',
          version: '0.1.0',
          author: { name: '本地' },
          host_application: { min_version: '1.0.0' },
          urls: {
            homepage: 'https://example.com/home',
            repository: 'https://example.com/sparse.git',
          },
        },
      } as unknown as InstalledPlugin,
    ])

    await renderPage()

    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-installed', 'true')
    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-installed-version', '1.0.0')
    expect(screen.getByTestId('plugin-local-sparse')).toHaveAttribute('data-source', 'local')
    expect(screen.getByTestId('plugin-local-sparse')).toHaveAttribute('data-installed', 'true')
  })

  it('未声明 host_application 的 v2 插件视为兼容', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('no-host', {}, {
        host_application: undefined as unknown as PluginInfo['manifest']['host_application'],
      }),
    ])

    await renderPage()

    expect(screen.getByTestId('plugin-no-host')).toHaveAttribute('data-compatible', 'true')
    expect(screen.getByTestId('reason-no-host')).toHaveTextContent('')
  })

  it('仅声明最低版本时不兼容原因使用 min+ 文案', async () => {
    vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(false)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('min-a', {}, { host_application: { min_version: '2.0.0' } }),
    ])

    await renderPage()

    expect(screen.getByTestId('reason-min-a')).toHaveTextContent(
      '不兼容当前版本 (需要 2.0.0+，当前 1.2.0)'
    )
  })

  it('缺少 min_version 时不兼容原因回退为未知', async () => {
    vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(false)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('unknown-min', {}, {
        host_application: { min_version: '', max_version: '3.0.0' },
      }),
    ])

    await renderPage()

    expect(screen.getByTestId('reason-unknown-min')).toHaveTextContent(
      '不兼容当前版本 (需要 未知 - 3.0.0，当前 1.2.0)'
    )
  })

  it('缺少 manifest_version 时按 v1 判定不兼容', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('no-ver', {}, { manifest_version: undefined as unknown as number }),
    ])

    await renderPage()

    expect(screen.getByTestId('plugin-no-ver')).toHaveAttribute('data-compatible', 'false')
    expect(screen.getByTestId('reason-no-ver')).toHaveTextContent(
      '该插件使用旧版 manifest (v1)，已不被麦麦 1.2.0 支持'
    )
  })

  it('麦麦版本缺失时一律视为兼容并允许安装', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getMaimaiVersion).mockResolvedValue(null as never)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('legacy-a', {}, { manifest_version: 1 }),
    ])

    await renderPage()

    expect(screen.getByTestId('plugin-legacy-a')).toHaveAttribute('data-compatible', 'true')
    await user.click(screen.getByText('install-legacy-a'))
    expect(screen.getByTestId('install-dialog')).toHaveAttribute('data-plugin-id', 'legacy-a')
  })

  it('版本数字段相同但字符串不同时 needsUpdate 为 false', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makeMarketPlugin('same-extra', {}, { version: '1.2.00' }),
    ])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      makeInstalledPlugin('same-extra', '1.2.0'),
    ])

    await renderPage()

    expect(screen.getByTestId('plugin-same-extra')).toHaveAttribute('data-needs-update', 'false')
    expect(screen.getByTestId('badge-same-extra')).toHaveTextContent('已安装')
  })

  it('已安装但缺少本地版本时徽章按 0.0.0 比较，needsUpdate 仍为 false', async () => {
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue(undefined)

    await renderPage()

    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-installed', 'true')
    expect(screen.getByTestId('badge-plugin-a')).toHaveTextContent('可更新')
    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-needs-update', 'false')
  })

  it('非法会话字段回退搜索词与显示已安装开关', async () => {
    window.sessionStorage.setItem(
      PLUGIN_MARKET_VIEW_STATE_KEY,
      JSON.stringify({
        searchQuery: 123,
        showInstalledPlugins: 'yes',
        pluginTypeFilter: 'all',
        marketplaceSortBy: 'default',
      })
    )

    await renderPage()

    expect(screen.getByPlaceholderText('搜索插件...')).toHaveValue('')
    expect(screen.getByTestId('marketplace-tab')).toHaveAttribute('data-hide-installed', 'true')
  })

  it('恢复并持久化滚动位置', async () => {
    window.sessionStorage.setItem('plugins-market-scroll-top', '88')
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      cb(0)
      return 1
    })

    await renderPage()

    const viewport = document.querySelector('[data-dashboard-scrollbar-viewport="true"]') as HTMLDivElement
    expect(viewport.scrollTop).toBe(88)

    viewport.scrollTop = 42
    act(() => {
      viewport.dispatchEvent(new Event('scroll'))
    })
    expect(window.sessionStorage.getItem('plugins-market-scroll-top')).toBe('42')
  })

  it('无效或非正滚动位置不恢复', async () => {
    window.sessionStorage.setItem('plugins-market-scroll-top', 'not-a-number')
    await renderPage()
    const viewport = document.querySelector('[data-dashboard-scrollbar-viewport="true"]') as HTMLDivElement
    expect(viewport.scrollTop).toBe(0)

    cleanup()
    window.sessionStorage.setItem('plugins-market-scroll-top', '-8')
    await renderPage()
    expect(
      (document.querySelector('[data-dashboard-scrollbar-viewport="true"]') as HTMLDivElement).scrollTop
    ).toBe(0)
  })

  it('插件列表替换时取消未执行的滚动恢复动画帧', async () => {
    window.sessionStorage.setItem('plugins-market-scroll-top', '50')
    const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame')
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 77)

    let resolveFetch: ((value: PluginInfo[]) => void) | null = null
    vi.mocked(pluginApi.getCachedPluginList).mockReturnValue([makeMarketPlugin('cached-x')])
    vi.mocked(pluginApi.fetchPluginList).mockImplementation(
      () => new Promise((resolve) => {
        resolveFetch = resolve
      })
    )

    render(<PluginMarketplacePage />)
    await screen.findByTestId('plugin-cached-x')

    await act(async () => {
      resolveFetch?.([makeMarketPlugin('plugin-a'), makeMarketPlugin('plugin-b')])
    })
    await screen.findByTestId('plugin-plugin-a')

    expect(cancelSpy).toHaveBeenCalledWith(77)
  })

  it('非 fetch 进度会阻塞卸载，成功进度超时后清除', async () => {
    const user = userEvent.setup()
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin('plugin-a', '1.2.0')])
    await renderPage()

    act(() => {
      progressHandler?.(makeProgress({
        operation: 'uninstall',
        stage: 'loading',
        plugin_id: 'plugin-a',
        message: '正在卸载',
      }))
    })
    await user.click(screen.getByText('uninstall-plugin-a'))
    expect(pluginApi.uninstallPlugin).not.toHaveBeenCalled()

    vi.useFakeTimers()
    try {
      act(() => {
        progressHandler?.(makeProgress({
          operation: 'fetch',
          stage: 'success',
          progress: 100,
          message: '完成',
        }))
        progressHandler?.(makeProgress({
          operation: 'uninstall',
          stage: 'success',
          plugin_id: 'plugin-a',
          progress: 100,
        }))
      })
      act(() => {
        vi.advanceTimersByTime(2000)
      })
    } finally {
      vi.useRealTimers()
    }

    await user.click(screen.getByText('uninstall-plugin-a'))
    await waitFor(() => expect(pluginApi.uninstallPlugin).toHaveBeenCalledWith('plugin-a'))
  })

  it('按 Escape 关闭插件详情对话框', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(screen.getByText('detail-plugin-a'))
    expect(await screen.findByTestId('plugin-detail')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByTestId('plugin-detail')).not.toBeInTheDocument())
  })

  it('组件卸载后不再处理延迟的成功进度回调', async () => {
    await renderPage()
    vi.useFakeTimers()
    try {
      act(() => {
        progressHandler?.(makeProgress({
          operation: 'fetch',
          stage: 'success',
          progress: 100,
          message: '完成',
        }))
        progressHandler?.(makeProgress({
          operation: 'install',
          stage: 'success',
          plugin_id: 'plugin-a',
          progress: 100,
        }))
      })
      cleanup()
      act(() => {
        vi.advanceTimersByTime(2000)
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('卸载页面时取消尚未完成的进度订阅，卸载后的进度回调不再弹 toast', async () => {
    let resolveWs: ((unsubscribe: () => Promise<void>) => void) | null = null
    const unsubscribe = vi.fn(async () => {})
    vi.mocked(pluginApi.connectPluginProgressWebSocket).mockImplementation(async (onProgress, onError) => {
      progressHandler = onProgress
      wsErrorHandler = onError ?? null
      return new Promise((resolve) => {
        resolveWs = resolve
      })
    })

    const { unmount } = render(<PluginMarketplacePage />)
    await screen.findByTestId('marketplace-tab')
    toastMock.mockClear()
    unmount()

    await act(async () => {
      resolveWs?.(unsubscribe)
    })
    expect(unsubscribe).toHaveBeenCalled()

    act(() => {
      wsErrorHandler?.(new Error('卸载后的错误'))
      progressHandler?.(makeProgress({ stage: 'error', error: '卸载后的失败' }))
    })
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('数据加载完成前卸载页面时不再写入状态', async () => {
    let resolveFetch: ((value: PluginInfo[]) => void) | null = null
    vi.mocked(pluginApi.fetchPluginList).mockImplementation(
      () => new Promise((resolve) => {
        resolveFetch = resolve
      })
    )
    vi.mocked(pluginApi.checkGitStatus).mockResolvedValue({
      installed: false,
      error: '不应弹出',
    })

    const { unmount } = render(<PluginMarketplacePage />)
    unmount()
    toastMock.mockClear()

    await act(async () => {
      resolveFetch?.([makeMarketPlugin('plugin-a')])
    })

    expect(toastMock).not.toHaveBeenCalled()
    expect(screen.queryByTestId('marketplace-tab')).not.toBeInTheDocument()
  })

  it('缓存统计只映射能解析到的插件，缺统计的插件保持空 likes', async () => {
    vi.mocked(pluginApi.getCachedPluginList).mockReturnValue([
      makeMarketPlugin('plugin-a'),
      makeMarketPlugin('plugin-b'),
    ])
    vi.mocked(pluginStatsApi.getCachedPluginStatsSummary).mockReturnValue({
      'plugin-a': {
        plugin_id: 'plugin-a-stats',
        likes: 9,
        dislikes: 0,
        downloads: 3,
        rating: 5,
        rating_count: 1,
      },
    })

    render(<PluginMarketplacePage />)

    expect(screen.getByTestId('plugin-plugin-a')).toHaveAttribute('data-likes', '9')
    expect(screen.getByTestId('plugin-plugin-b')).toHaveAttribute('data-likes', '')
    await screen.findByTestId('plugin-plugin-a')
  })
})
