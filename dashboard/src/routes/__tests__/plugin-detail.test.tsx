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

function renderPage(props: Parameters<typeof PluginDetailPage>[0] = {}) {
  render(<PluginDetailPage {...props} />, { wrapper: makeWrapper() })
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

  it('README 与更新日志请求失败时分别展示失败文案', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          changelog: 'docs/CHANGELOG.md',
        },
      }),
    ])
    vi.mocked(httpLib.backendApi.post).mockImplementation(async (_url, options) => {
      const filePath = (options as { body?: { file_path?: string } } | undefined)?.body?.file_path
      if (filePath === 'README.md') {
        throw new Error('readme down')
      }
      throw new Error('changelog down')
    })
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      const markdownTexts = screen.getAllByTestId('markdown').map((node) => node.textContent)
      expect(markdownTexts).toContain('加载 README 失败')
      expect(markdownTexts).toContain('加载更新日志失败')
    })
  })

  it('无仓库地址时 README/更新日志为空占位', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          repository_url: undefined,
          keywords: [],
        },
      }),
    ])
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(screen.getByText('暂无说明文档')).toBeInTheDocument()
      expect(screen.getByText('暂无更新日志')).toBeInTheDocument()
    })
    expect(screen.queryByRole('link', { name: /GitHub/ })).not.toBeInTheDocument()
    expect(screen.queryByText('标签')).not.toBeInTheDocument()
    expect(httpLib.backendApi.post).not.toHaveBeenCalled()
  })

  it('无法解析的仓库地址展示 README 解析失败文案', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          repository_url: 'https://gitlab.com/owner/repo',
          changelog: 'CHANGELOG.md',
        },
      }),
    ])
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '无法解析仓库地址')
      ).toBe(true)
      expect(screen.getByText('暂无更新日志')).toBeInTheDocument()
    })
  })

  it('已安装本地 README 失败后回退远程文档', async () => {
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('2.0.0')
    vi.mocked(httpLib.backendApi.get).mockImplementation(async (url) => {
      if (String(url).includes('local-readme')) {
        throw new Error('本地不存在')
      }
      return { success: false }
    })
    vi.mocked(httpLib.backendApi.post).mockResolvedValue({
      success: true,
      data: '# 远程回退说明',
    })
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '# 远程回退说明')
      ).toBe(true)
    })
    expect(httpLib.backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/fetch-raw', {
      body: { owner: 'owner', repo: 'repo', branch: 'main', file_path: 'README.md' },
      errorMessage: '获取 README 失败',
      signal: expect.any(AbortSignal),
    })
  })

  it('已安装插件优先读取本地更新日志', async () => {
    vi.mocked(pluginApi.checkPluginInstalled).mockReturnValue(true)
    vi.mocked(pluginApi.getInstalledPluginVersion).mockReturnValue('2.0.0')
    vi.mocked(httpLib.backendApi.get).mockImplementation(async (url) => {
      if (String(url).includes('local-changelog')) {
        return { success: true, data: '本地更新日志正文' }
      }
      if (String(url).includes('local-readme')) {
        return { success: true, data: '本地说明内容' }
      }
      return { success: false }
    })
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      const markdownTexts = screen.getAllByTestId('markdown').map((node) => node.textContent)
      expect(markdownTexts).toContain('本地说明内容')
      expect(markdownTexts).toContain('本地更新日志正文')
    })
    expect(httpLib.backendApi.get).toHaveBeenCalledWith(
      '/api/webui/plugins/local-changelog/plug-1',
      { signal: expect.any(AbortSignal) }
    )
  })

  it('单行 # / - 内联更新日志无需请求即可渲染', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({ changelog: '# 仅标题日志' }),
    ])
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '# 仅标题日志')
      ).toBe(true)
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

  it('https 更新日志走 custom_url 拉取', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({ changelog: 'https://cdn.example/CHANGELOG.md' }),
    ])
    vi.mocked(httpLib.backendApi.post).mockImplementation(async (_url, options) => {
      const body = (options as { body?: { file_path?: string; custom_url?: string } } | undefined)
        ?.body
      if (body?.custom_url) {
        return { success: true, data: '# 远程更新日志' }
      }
      return { success: true, data: '# 远程说明文档' }
    })
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '# 远程更新日志')
      ).toBe(true)
    })
    expect(httpLib.backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/fetch-raw', {
      body: {
        owner: 'custom',
        repo: 'custom',
        branch: 'main',
        file_path: 'CHANGELOG.md',
        custom_url: 'https://cdn.example/CHANGELOG.md',
      },
      errorMessage: '获取更新日志失败',
      signal: expect.any(AbortSignal),
    })
  })

  it('相对路径更新日志按仓库文件拉取', async () => {
    vi.mocked(pluginApi.fetchPluginList).mockResolvedValue([
      makePlugin({
        manifest: {
          ...makePlugin().manifest,
          changelog: 'docs/CHANGELOG.md',
        },
      }),
    ])
    vi.mocked(httpLib.backendApi.post).mockImplementation(async (_url, options) => {
      const filePath = (options as { body?: { file_path?: string } } | undefined)?.body?.file_path
      if (filePath === 'docs/CHANGELOG.md') {
        return { success: true, data: '# 仓库更新日志' }
      }
      return { success: true, data: '# 远程说明文档' }
    })
    renderPage()
    await waitDetailReady()

    await waitFor(() => {
      expect(
        screen.getAllByTestId('markdown').some((node) => node.textContent === '# 仓库更新日志')
      ).toBe(true)
    })
    expect(httpLib.backendApi.post).toHaveBeenCalledWith('/api/webui/plugins/fetch-raw', {
      body: {
        owner: 'owner',
        repo: 'repo',
        branch: 'main',
        file_path: 'docs/CHANGELOG.md',
      },
      errorMessage: '获取更新日志失败',
      signal: expect.any(AbortSignal),
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
    renderPage()

    await waitFor(() => {
      expect(screen.getAllByText('本地插件').length).toBeGreaterThan(0)
    })
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(screen.getAllByText('通用扩展').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: /访问/ })).toHaveAttribute(
      'href',
      'https://local.example'
    )
    expect(screen.getByRole('link', { name: /GitHub/ })).toHaveAttribute(
      'href',
      'https://github.com/local/repo.git'
    )
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

