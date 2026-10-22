/**
 * PluginDetailPage 特征化测试
 *
 * 插件详情页：路由参数缺失 / 加载态 / 市场详情渲染 / 本地已安装回退 /
 * 安装-更新-卸载操作 / Git 未安装与版本不兼容禁用 / README 与更新日志加载。
 * plugin-api、http、plugin-stats 全量打桩；react-query 由测试内 QueryClient 真实驱动。
 */
import type { ReactNode } from 'react'
import type { InstalledPlugin } from '@/lib/plugin-api'
import type { PluginInfo } from '@/types/plugin'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PluginDetailPage } from '../plugin-detail'
import * as httpLib from '@/lib/http'
import * as pluginApi from '@/lib/plugin-api'
import * as pluginStatsLib from '@/lib/plugin-stats'

const { navigateMock, routerState, toastMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  routerState: { search: {} as { pluginId?: string } },
  toastMock: vi.fn(),
}))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
  useSearch: () => routerState.search,
}))
vi.mock('@/lib/http', () => ({
  backendApi: { get: vi.fn(), post: vi.fn() },
}))
vi.mock('@/lib/plugin-api', () => ({
  checkGitStatus: vi.fn(),
  checkPluginInstalled: vi.fn(),
  fetchPluginList: vi.fn(),
  fetchPluginDetail: vi.fn(),
  fetchPluginDocument: vi.fn(),
  getCachedPluginList: vi.fn(),
  getInstalledPluginVersion: vi.fn(),
  getInstalledPlugins: vi.fn(),
  getMaimaiVersion: vi.fn(),
  installPlugin: vi.fn(),
  isPluginCompatible: vi.fn(),
  uninstallPlugin: vi.fn(),
  updatePlugin: vi.fn(),
}))
vi.mock('@/lib/plugin-stats', () => ({ recordPluginDownload: vi.fn() }))
// 展示型子组件打桩：只透出关键内容，避免引入 markdown 渲染与统计请求链路
vi.mock('@/components/markdown-renderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}))
vi.mock('@/components/plugin-stats', () => ({
  PluginStats: ({ pluginId }: { pluginId: string }) => (
    <div data-testid="plugin-stats">{pluginId}</div>
  ),
}))
vi.mock('@/routes/plugins/PluginIcon', () => ({
  PluginIcon: () => <div data-testid="plugin-icon" />,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** 构造一个市场插件 */
function makePlugin(overrides: Partial<PluginInfo> = {}): PluginInfo {
  return {
    id: 'plug-1',
    manifest: {
      manifest_version: 1,
      id: 'plug-1',
      name: '测试插件',
      version: '2.0.0',
      description: '插件描述文本',
      author: { name: '作者甲', url: 'https://author.example' },
      license: 'MIT',
      host_application: { min_version: '0.10.0' },
      repository_url: 'https://github.com/owner/repo.git',
      keywords: ['聊天'],
      plugin_type: 'chat',
      default_locale: 'zh-CN',
    },
    downloads: 5,
    rating: 0,
    review_count: 0,
    installed: false,
    published_at: '',
    updated_at: '',
    ...overrides,
  }
}

/** 构造一个本地已安装插件 */
function makeInstalledPlugin(): InstalledPlugin {
  return {
    id: 'plug-1',
    manifest: {
      manifest_version: 1,
      id: 'plug-1',
      name: '本地插件',
      version: '1.5.0',
      description: '本地插件描述',
      author: { name: '作者乙' },
      // 特征化：manifest 未声明 license 时 buildLocalPluginInfo 回填为 Unknown
      license: '',
      host_application: { min_version: '0.10.0' },
    },
    path: '/plugins/plug-1',
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function renderPage(props: Parameters<typeof PluginDetailPage>[0] = {}) {
  render(<PluginDetailPage {...props} />, { wrapper: makeWrapper() })
}

beforeEach(() => {
  routerState.search = { pluginId: 'plug-1' }
  vi.mocked(pluginApi.fetchPluginDetail).mockImplementation(async (plugin) => plugin)
  vi.mocked(pluginApi.fetchPluginDocument).mockResolvedValue('')
  vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([makePlugin()])
  vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([])
  vi.mocked(pluginApi.checkGitStatus).mockResolvedValue({ installed: true, version: '2.40.0' })
  vi.mocked(pluginApi.getMaimaiVersion).mockResolvedValue({
    version: '0.11.0',
    version_major: 0,
    version_minor: 11,
    version_patch: 0,
  })
  vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(true)
  vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(false)
  vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue(undefined)
  vi.mocked(pluginApi.installPlugin).mockResolvedValue(undefined as never)
  vi.mocked(pluginApi.uninstallPlugin).mockResolvedValue(undefined as never)
  vi.mocked(pluginApi.updatePlugin).mockResolvedValue({
    old_version: '1.0.0',
    new_version: '2.0.0',
  } as never)
  vi.mocked(pluginStatsLib.recordPluginDownload).mockResolvedValue(undefined as never)
  vi.mocked(httpLib.backendApi.get).mockResolvedValue({ success: false })
  vi.mocked(httpLib.backendApi.post).mockResolvedValue({ success: false })
})

/** 等待市场插件详情渲染完成 */
async function waitDetailReady() {
  await waitFor(() => {
    expect(screen.getByText('插件描述文本')).toBeInTheDocument()
  })
}

function makeReleasePlugin(): PluginInfo {
  const plugin = makePlugin()
  plugin.releases = {
    id: 'plug-1', manifest_id: 'plug-1', repositoryUrl: 'https://github.com/owner/repo',
    mode: 'releases', recommended_version: '2.0.0',
    versions: ['2.0.0', '1.0.0'].map((version) => ({
      version, tag: `v${version}`, commit: version.startsWith('2') ? 'a'.repeat(40) : 'b'.repeat(40),
      prerelease: false, yanked: false, compatible: true, reasons: [],
      manifest: { ...plugin.manifest, version, sdk: { min_version: '2.0.0', max_version: '2.99.99' } },
      release_notes: `${version} 发布说明`,
    })),
  }
  return plugin
}

describe('插件发布版本选择', () => {
  it('选择历史版本后，提交明确版本并按 commit 获取文档', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([makeReleasePlugin()])
    renderPage()
    await waitDetailReady()
    fireEvent.change(screen.getByRole('combobox', { name: '选择发布版本' }), { target: { value: '1.0.0' } })
    fireEvent.click(screen.getByRole('button', { name: '安装' }))
    await waitFor(() => expect(pluginApi.installPlugin).toHaveBeenCalledWith(
      'plug-1', 'https://github.com/owner/repo.git', 'main', { version: '1.0.0' }
    ))
    fireEvent.mouseDown(screen.getByRole('tab', { name: '更新日志' }), { button: 0, ctrlKey: false })
    expect(screen.getByText('1.0.0 发布说明')).toBeInTheDocument()
    expect(pluginApi.fetchPluginDocument).toHaveBeenCalledWith('plug-1', 'readme', expect.objectContaining({
      branch: 'b'.repeat(40), signal: expect.any(AbortSignal),
    }))
  })

  it('没有兼容版本时禁止安装并显示具体原因', async () => {
    const plugin = makeReleasePlugin()
    plugin.releases!.recommended_version = null
    plugin.releases!.versions.forEach((release) => { release.compatible = false; release.reasons = ['SDK 版本不兼容'] })
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([plugin])
    renderPage()
    await waitDetailReady()
    expect(screen.getByRole('button', { name: '安装' })).toBeDisabled()
    // 发布版本选项末尾附带更新日期，夹具未提供 published_at 时显示「时间未知」
    expect(screen.getByRole('option', { name: '2.0.0 · SDK 版本不兼容 · 更新于 时间未知' })).toBeDisabled()
    expect(pluginApi.installPlugin).not.toHaveBeenCalled()
  })
})

describe('PluginDetailPage 路由与加载态', () => {
  it('缺少 pluginId 时展示错误卡片，点击返回跳转插件列表', () => {
    routerState.search = {}
    renderPage()

    expect(screen.getByText('缺少插件 ID')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回插件列表' }))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugins' })
  })

  it('详情请求未返回时显示加载动画', () => {
    vi.mocked(pluginApi.fetchPluginList).mockReturnValue(new Promise(() => {}))
    renderPage()

    expect(screen.getAllByRole('status', { name: '加载中' }).length).toBeGreaterThan(0)
    expect(screen.queryByText('测试插件')).not.toBeInTheDocument()
  })

  it('市场与本地都找不到插件时展示「未找到该插件」', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([])
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('未找到该插件')).toBeInTheDocument()
    })
    expect(screen.getByText('加载失败')).toBeInTheDocument()
  })
})

describe('PluginDetailPage 详情渲染', () => {
  it('渲染市场插件的名称/版本/作者/许可证/仓库链接与统计组件', async () => {
    renderPage()
    await waitDetailReady()

    // 名称出现两处：页头副标题 + 详情卡标题
    expect(screen.getAllByText('测试插件')).toHaveLength(2)
    expect(screen.getAllByText('v2.0.0').length).toBeGreaterThan(0)
    expect(screen.getByText('作者甲')).toBeInTheDocument()
    expect(screen.getAllByText('MIT').length).toBeGreaterThan(0)
    // host_application 无 max_version 时展示「最新版本」
    expect(screen.getByText(/0\.10\.0\s*- 最新版本/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /GitHub/ })).toHaveAttribute(
      'href',
      'https://github.com/owner/repo.git'
    )
    expect(screen.getByTestId('plugin-stats')).toHaveTextContent('plug-1')
    // 未安装且 Git 可用：安装按钮可点
    expect(screen.getByRole('button', { name: '安装' })).toBeEnabled()
  })

  it('市场找不到但本地已安装时回退到本地 manifest（license 回填 Unknown）', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([makeInstalledPlugin()])
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('1.5.0')
    renderPage()

    await waitFor(() => {
      expect(screen.getAllByText('本地插件').length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0)
    expect(screen.getByText(/已安装/)).toBeInTheDocument()
    // 本地版本与 manifest 版本一致：不出现「可更新」徽标
    expect(screen.queryByText('可更新')).not.toBeInTheDocument()
  })

  it('版本不兼容时展示不兼容徽标并禁用安装', async () => {
    vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(false)
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(screen.getByText('不兼容')).toBeInTheDocument()
    })
    const installButton = screen.getByRole('button', { name: '安装' })
    expect(installButton).toBeDisabled()
    expect(installButton).toHaveAttribute('title', expect.stringContaining('不兼容当前版本'))
  })

  it('Git 未安装时安装按钮禁用并给出原因', async () => {
    vi.mocked(pluginApi.checkGitStatus).mockResolvedValue({ installed: false })
    renderPage()
    await waitDetailReady()

    const installButton = screen.getByRole('button', { name: '安装' })
    await waitFor(() => {
      expect(installButton).toBeDisabled()
    })
    expect(installButton).toHaveAttribute('title', 'Git 未安装')
  })
})

describe('PluginDetailPage 安装/更新/卸载', () => {
  it('点击安装调用 installPlugin 并记录下载统计', async () => {
    renderPage()
    await waitDetailReady()

    fireEvent.click(screen.getByRole('button', { name: '安装' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装成功',
        description: '测试插件 已成功安装',
      })
    })
    expect(pluginApi.installPlugin).toHaveBeenCalledWith(
      'plug-1',
      'https://github.com/owner/repo.git',
      'main'
    )
    expect(pluginStatsLib.recordPluginDownload).toHaveBeenCalledWith('plug-1')
  })

  it('已安装且版本落后时提供更新按钮，成功后提示新旧版本', async () => {
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('1.0.0')
    renderPage()
    await waitDetailReady()

    expect(screen.getByText('可更新')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '更新' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '更新成功',
        description: '测试插件 已从 1.0.0 更新到 2.0.0',
      })
    })
    expect(pluginApi.updatePlugin).toHaveBeenCalledWith(
      'plug-1',
      'https://github.com/owner/repo.git',
      'main'
    )
  })

  it('点击卸载调用 uninstallPlugin 并提示成功', async () => {
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('2.0.0')
    renderPage()
    await waitDetailReady()

    fireEvent.click(screen.getByRole('button', { name: '卸载' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '卸载成功',
        description: '测试插件 已成功卸载',
      })
    })
    expect(pluginApi.uninstallPlugin).toHaveBeenCalledWith('plug-1')
  })
})

describe('PluginDetailPage 错误与空态', () => {
  it('详情请求抛出 Error 时展示具体错误信息', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockRejectedValue(new Error('市场不可用'))
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('市场不可用')).toBeInTheDocument()
    })
    expect(screen.getByText('加载失败')).toBeInTheDocument()
  })

  it('详情请求抛出非 Error 时回退为「加载失败」', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockRejectedValue('boom')
    renderPage()

    await waitFor(() => {
      expect(screen.getAllByText('加载失败').length).toBeGreaterThanOrEqual(2)
    })
  })
})

describe('PluginDetailPage 配置信息卡与交互', () => {
  it('对话框模式隐藏页标题，返回调用 onClose', async () => {
    const onClose = vi.fn()
    renderPage({ mode: 'dialog', onClose, pluginId: 'plug-1' })
    await waitDetailReady()

    expect(screen.queryByRole('heading', { name: '插件详情' })).not.toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button')[0])
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('embedded 模式返回跳转嵌入插件列表', async () => {
    renderPage({ embedded: true, pluginId: 'plug-1' })
    await waitDetailReady()

    fireEvent.click(screen.getAllByRole('button')[0])
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugins/embed' })
  })

  it('pluginId 属性优先于 search，并可用 marketplace_id 命中市场插件', async () => {
    routerState.search = { pluginId: 'other-id' }
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        id: 'plug-1',
        marketplace_id: 'market-alias',
      }),
    ])
    renderPage({ pluginId: 'market-alias' })
    await waitDetailReady()

    expect(screen.getAllByText('测试插件').length).toBeGreaterThan(0)
  })

  it('展示主页、版本上限、作者链接，无关键词时不渲染标签卡', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          homepage_url: 'https://home.example',
          keywords: [],
          host_application: { min_version: '0.10.0', max_version: '0.12.0' },
        },
      }),
    ])
    renderPage()
    await waitDetailReady()

    expect(screen.getByRole('link', { name: /访问/ })).toHaveAttribute(
      'href',
      'https://home.example'
    )
    expect(screen.getByText(/0\.10\.0\s*- 0\.12\.0/)).toBeInTheDocument()
    expect(screen.queryByText('标签')).not.toBeInTheDocument()
  })

  it('不兼容且声明 max_version 时安装按钮 title 含版本区间', async () => {
    vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(false)
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          host_application: { min_version: '0.10.0', max_version: '0.12.0' },
        },
      }),
    ])
    renderPage()
    await waitDetailReady()

    const installButton = screen.getByRole('button', { name: '安装' })
    expect(installButton).toBeDisabled()
    expect(installButton).toHaveAttribute(
      'title',
      expect.stringContaining('需要 0.10.0 - 0.12.0')
    )
  })

  it('麦麦版本未就绪时不展示不兼容徽标', async () => {
    vi.mocked(pluginApi.getMaimaiVersion).mockReturnValue(new Promise(() => {}))
    vi.mocked(pluginApi.isPluginCompatible).mockReturnValue(false)
    renderPage()
    await waitDetailReady()

    expect(screen.queryByText('不兼容')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '安装' })).toBeEnabled()
  })

  it('无 manifest.id 时不渲染统计组件，安装成功也不记录下载', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          id: undefined,
        },
      }),
    ])
    renderPage()
    await waitDetailReady()

    expect(screen.queryByTestId('plugin-stats')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '安装' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装成功',
        description: '测试插件 已成功安装',
      })
    })
    expect(pluginStatsLib.recordPluginDownload).not.toHaveBeenCalled()
  })

  it('下载统计失败仍提示安装成功', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.mocked(pluginStatsLib.recordPluginDownload).mockRejectedValue(new Error('stats fail'))
    renderPage()
    await waitDetailReady()

    fireEvent.click(screen.getByRole('button', { name: '安装' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '安装成功',
        description: '测试插件 已成功安装',
      })
    })
    await waitFor(() => {
      expect(warnSpy).toHaveBeenCalled()
    })
  })

  it('缺少 repository_url 时用 urls.repository 安装', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          repository_url: undefined,
          urls: { repository: 'https://github.com/alt/repo.git' },
        },
      }),
    ])
    renderPage()
    await waitDetailReady()

    fireEvent.click(screen.getByRole('button', { name: '安装' }))

    await waitFor(() => {
      expect(pluginApi.installPlugin).toHaveBeenCalledWith(
        'plug-1',
        'https://github.com/alt/repo.git',
        'main'
      )
    })
  })

  it('安装进行中展示加载文案', async () => {
    vi.mocked(pluginApi.installPlugin).mockReturnValue(new Promise(() => {}))
    renderPage()
    await waitDetailReady()

    fireEvent.click(screen.getByRole('button', { name: '安装' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /安装中/ })).toBeDisabled()
    })
  })

  it('Git 未安装时已安装插件的卸载按钮禁用', async () => {
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('2.0.0')
    vi.mocked(pluginApi.checkGitStatus).mockResolvedValue({ installed: false })
    renderPage()
    await waitDetailReady()

    const uninstallButton = screen.getByRole('button', { name: '卸载' })
    await waitFor(() => {
      expect(uninstallButton).toBeDisabled()
    })
    expect(uninstallButton).toHaveAttribute('title', 'Git 未安装')
  })

  it('本地已安装插件用 urls 回填主页与仓库，license 为空时显示 Unknown', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([])
    vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([
      {
        ...makeInstalledPlugin(),
        changelog: '- 本地条目',
        manifest: {
          ...makeInstalledPlugin().manifest,
          homepage_url: undefined,
          repository_url: undefined,
          urls: {
            homepage: 'https://local.example',
            repository: 'https://github.com/local/repo.git',
          },
          keywords: undefined,
          plugin_type: undefined,
          description: '',
        },
      },
    ])
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('1.5.0')
    vi.mocked(pluginApi.fetchPluginDocument).mockImplementation(async (_id, kind) =>
      kind === 'changelog' ? '- 本地条目' : '')
    renderPage()

    await waitFor(() => {
      expect(screen.getAllByText('本地插件').length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0)
    expect(screen.getAllByText('通用扩展').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: /访问/ })).toHaveAttribute(
      'href',
      'https://local.example'
    )
    expect(screen.getByRole('link', { name: /GitHub/ })).toHaveAttribute(
      'href',
      'https://github.com/local/repo.git'
    )
    fireEvent.mouseDown(screen.getByRole('tab', { name: '更新日志' }), { button: 0, ctrlKey: false })
    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '- 本地条目')
      ).toBe(true)
    })
  })

  it('加载态点击返回跳转插件列表', () => {
    vi.mocked(pluginApi.fetchPluginList).mockReturnValue(new Promise(() => {}))
    renderPage()

    fireEvent.click(screen.getAllByRole('button')[0])
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugins' })
  })
})



describe('公共插件文档展示', () => {
  it('README 与更新日志都委托公共服务，展示返回内容', async () => {
    vi.mocked(pluginApi.fetchPluginDocument).mockImplementation(async (_id, kind) =>
      kind === 'readme' ? '# 公共服务 README' : '# 公共服务更新日志')
    renderPage()
    await waitDetailReady()
    await waitFor(() => expect(screen.getByText('# 公共服务 README')).toBeInTheDocument())
    fireEvent.mouseDown(screen.getByRole('tab', { name: '更新日志' }), { button: 0, ctrlKey: false })
    await waitFor(() => expect(screen.getByText('# 公共服务更新日志')).toBeInTheDocument())
    expect(pluginApi.fetchPluginDocument).toHaveBeenCalledWith('plug-1', 'readme', expect.objectContaining({
      preferLocal: false, signal: expect.any(AbortSignal),
    }))
  })

  it('服务错误在文档区域显示，未声明文档显示空态', async () => {
    vi.mocked(pluginApi.fetchPluginDocument).mockRejectedValue(new Error('offline'))
    renderPage()
    await waitDetailReady()
    await waitFor(() => expect(screen.getByText('加载 README 失败')).toBeInTheDocument())
    fireEvent.mouseDown(screen.getByRole('tab', { name: '更新日志' }), { button: 0, ctrlKey: false })
    await waitFor(() => expect(screen.getByText('加载更新日志失败')).toBeInTheDocument())
  })
})
