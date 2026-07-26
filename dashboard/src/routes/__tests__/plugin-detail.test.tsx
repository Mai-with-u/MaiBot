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

function renderPage() {
  render(<PluginDetailPage />, { wrapper: makeWrapper() })
}

beforeEach(() => {
  routerState.search = { pluginId: 'plug-1' }
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
    expect(screen.getByText('MIT')).toBeInTheDocument()
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
    expect(screen.getByText('Unknown')).toBeInTheDocument()
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

describe('PluginDetailPage README 与更新日志', () => {
  it('未安装插件从仓库地址解析 owner/repo 拉取远程 README', async () => {
    vi.mocked(httpLib.backendApi.post).mockResolvedValue({
      success: true,
      data: '# 远程说明文档',
    })
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '# 远程说明文档')
      ).toBe(true)
    })
    // .git 后缀会被剥离
    expect(httpLib.backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/fetch-raw', {
      body: { owner: 'owner', repo: 'repo', branch: 'main', file_path: 'README.md' },
      errorMessage: '获取 README 失败',
      signal: expect.any(AbortSignal),
    })
  })

  it('已安装插件优先读取本地 README', async () => {
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('2.0.0')
    vi.mocked(httpLib.backendApi.get).mockResolvedValue({ success: true, data: '本地说明内容' })
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '本地说明内容')
      ).toBe(true)
    })
    expect(httpLib.backendApi.get).toHaveBeenCalledWith(
      '/api/webui/plugins/local-readme/plug-1',
      { signal: expect.any(AbortSignal) }
    )
  })

  it('远程 README 拉取失败时展示占位文案，内联多行更新日志直接渲染', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({ changelog: '## v2\n- 新增功能' }),
    ])
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      const markdownTexts = screen.getAllByTestId('markdown').map((node) => node.textContent)
      // README：fetch-raw 返回失败 → 占位文案
      expect(markdownTexts).toContain('该插件暂无 README 文档')
      // changelog：包含换行的内联文本无需请求即直接展示
      expect(markdownTexts.some((text) => text?.includes('新增功能'))).toBe(true)
    })
  })
})
