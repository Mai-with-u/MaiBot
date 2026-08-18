import type { ReactNode } from 'react'

import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HomeCardManager } from '../home/HomeCardManager'
import { IndexPage } from '../index'
import { backendApi } from '@/lib/http'
import * as configApi from '@/lib/config-api'
import * as expressionApi from '@/lib/expression-api'
import * as systemApi from '@/lib/system-api'
import * as pluginApi from '@/lib/plugin-api'
import { ThemeProviderContext, type ThemeProviderState } from '@/lib/theme-context'
import { DEFAULT_DASHBOARD_STYLE_CONFIG, type DashboardStyle } from '@/lib/theme/tokens'
import { UPDATE_NOTICE_OPEN_EVENT, type UpdateNoticeTarget } from '@/lib/update-notice-events'
import { APP_VERSION } from '@/lib/version'

const originalRandomUUID = globalThis.crypto.randomUUID

// 模块级仪表盘/状态/缓存 TTL 跨用例存活；resetModules 后需复用同一批 mock。
const mocks = vi.hoisted(() => ({
  backendGet: vi.fn(),
  getBotConfigCached: vi.fn(),
  getModelConfigCached: vi.fn(),
  getReviewStats: vi.fn(),
  getLocalCacheStats: vi.fn(),
  getInstalledPlugins: vi.fn(),
  getPluginConfigSchema: vi.fn(),
  getPluginHomeCards: vi.fn(),
  triggerRestart: vi.fn(),
  isRestarting: false,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  window.localStorage.clear()
  mocks.isRestarting = false
  Object.defineProperty(globalThis.crypto, 'randomUUID', {
    configurable: true,
    writable: true,
    value: originalRandomUUID,
  })
})

// i18n 测试环境未初始化，t() 返回 key；mock 为恒等便于断言。
// t/i18n 必须是稳定引用（工厂内创建一次）——否则每渲染返回新 t，
// 会让依赖 [t] 的 fetchHitokoto 失稳、主 effect 无限重跑直至 OOM。
vi.mock('react-i18next', () => {
  const t = (k: string) => k
  const i18n = { resolvedLanguage: 'zh-CN', language: 'zh-CN' }
  return { useTranslation: () => ({ t, i18n }) }
})
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => ({ isRestarting: mocks.isRestarting, triggerRestart: mocks.triggerRestart }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))
vi.mock('@/components/expression-reviewer', () => ({
  ExpressionReviewer: ({
    open,
    onOpenChange,
  }: {
    open: boolean
    onOpenChange?: (open: boolean) => void
  }) =>
    open ? (
      <div data-testid="expression-reviewer">
        <button type="button" onClick={() => onOpenChange?.(false)}>
          close-reviewer
        </button>
      </div>
    ) : null,
}))
// recharts 在 jsdom 无尺寸，显式列出用到的导出 stub 为占位
// （含 @/components/ui/chart.tsx 在模块加载期 `import * as` 访问的成员，避免命名空间缺成员崩溃）
vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: ReactNode }) => <div>{children}</div>
  return {
    __esModule: true,
    ResponsiveContainer: Stub,
    LineChart: Stub,
    Line: Stub,
    BarChart: Stub,
    Bar: Stub,
    PieChart: Stub,
    Pie: Stub,
    Cell: Stub,
    AreaChart: Stub,
    Area: Stub,
    XAxis: Stub,
    YAxis: Stub,
    CartesianGrid: Stub,
    Tooltip: Stub,
    Legend: Stub,
    ReferenceLine: Stub,
  }
})
vi.mock('@/lib/http', () => ({ backendApi: { get: mocks.backendGet } }))
vi.mock('@/lib/config-api', () => ({
  getBotConfigCached: mocks.getBotConfigCached,
  getModelConfigCached: mocks.getModelConfigCached,
}))
vi.mock('@/lib/expression-api', () => ({ getReviewStats: mocks.getReviewStats }))
vi.mock('@/lib/system-api', () => ({ getLocalCacheStats: mocks.getLocalCacheStats }))
vi.mock('@/lib/plugin-api', () => ({
  getInstalledPlugins: mocks.getInstalledPlugins,
  getPluginConfigSchema: mocks.getPluginConfigSchema,
  getPluginHomeCards: mocks.getPluginHomeCards,
}))

const dashboardData = {
  summary: {
    total_requests: 1234,
    total_cost: 12.3,
    total_tokens: 56789,
    input_tokens: 48000,
    output_tokens: 8789,
    cache_hit_tokens: 24000,
    cache_miss_tokens: 24000,
    cache_hit_rate: 0.5,
    chat_cache_hit_tokens: 18000,
    chat_cache_miss_tokens: 12000,
    chat_cache_hit_rate: 0.6,
    online_time: 3600,
    total_messages: 100,
    total_replies: 90,
    avg_response_time: 1.2,
    cost_per_hour: 1,
    tokens_per_hour: 100,
  },
  model_stats: [
    {
      model_name: 'gpt-4',
      request_count: 100,
      total_cost: 5,
      total_tokens: 2000,
      input_tokens: 1600,
      output_tokens: 400,
      cache_hit_tokens: 800,
      cache_miss_tokens: 800,
      cache_hit_rate: 0.5,
      avg_response_time: 2,
    },
  ],
  hourly_data: [
    {
      timestamp: '2025-01-01T00:00:00Z',
      online_seconds: 2700,
      requests: 10,
      cost: 1,
      tokens: 500,
      input_tokens: 400,
      output_tokens: 100,
      cache_hit_tokens: 200,
      cache_miss_tokens: 200,
    },
  ],
  daily_data: [
    {
      timestamp: '2025-01-01T00:00:00Z',
      online_seconds: 3600,
      requests: 240,
      cost: 24,
      tokens: 12000,
      input_tokens: 10000,
      output_tokens: 2000,
      cache_hit_tokens: 5000,
      cache_miss_tokens: 5000,
    },
  ],
  recent_activity: [],
}
const botStatus = {
  running: true,
  uptime: 3600,
  version: '1.0.0',
  start_time: '2025-01-01T00:00:00Z',
}

function jsonResponse(data: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    headers: {
      get: (name: string) => (name.toLowerCase() === 'content-type' ? 'application/json' : null),
    },
    json: async () => data,
  })
}

function mockBrowserFetch(
  handlers: {
    hitokoto?: () => ReturnType<typeof jsonResponse>
    github?: () => ReturnType<typeof jsonResponse>
    compatibility?: () => ReturnType<typeof jsonResponse>
  } = {}
) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('version-compatibility')) {
        return (
          handlers.compatibility?.() ??
          jsonResponse({
            status: 'compatible',
            main_program_version: '1.0.0',
            webui_version: APP_VERSION,
            required_webui_version: APP_VERSION,
          })
        )
      }
      if (url.includes('github')) {
        return (
          handlers.github?.() ??
          jsonResponse([{ tag_name: 'v2.0.0', draft: false, prerelease: false, html_url: '' }])
        )
      }
      if (url.includes('hitokoto')) {
        return handlers.hitokoto?.() ?? jsonResponse({ hitokoto: '测试一言', from: '来源' })
      }
      return jsonResponse({})
    }) as never
  )
}

function stubBackendGet(options?: {
  dashboard?: unknown | (() => Promise<unknown>)
  bot?: unknown | (() => Promise<unknown>)
  platform?: unknown | (() => Promise<unknown>)
}) {
  mocks.backendGet.mockImplementation((path: string) => {
    if (path.includes('/statistics/dashboard')) {
      const value = options?.dashboard ?? dashboardData
      return typeof value === 'function' ? value() : Promise.resolve(value)
    }
    if (path.includes('/system/status')) {
      const value = options?.bot ?? botStatus
      return typeof value === 'function' ? value() : Promise.resolve(value)
    }
    if (path.includes('/config/bot')) {
      const value = options?.platform ?? { config: { bot: { qq_account: '123456' } } }
      return typeof value === 'function' ? value() : Promise.resolve(value)
    }
    return Promise.resolve({})
  })
}

/** 清空 useDashboardData / useBotStatus / useLocalCacheMetrics 的模块级缓存。 */
async function renderFreshIndexPage() {
  vi.resetModules()
  const { IndexPage: FreshIndexPage } = await import('../index')
  const view = render(<FreshIndexPage />)
  // 让并行挂载的状态/缓存/一言请求在断言前落盘，避免 act 噪声。
  await act(async () => {
    await Promise.resolve()
  })
  return view
}

function expireModuleCaches(ms: number) {
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + ms)
}

function makeThemeState(dashboardStyle: DashboardStyle): ThemeProviderState {
  return {
    theme: 'light',
    resolvedTheme: 'light',
    setTheme: () => undefined,
    themeConfig: {
      selectedPreset: 'light',
      accentColor: '',
      styleTokenOverrides: {},
      styleCustomCSS: {},
      dashboardStyle,
      styleBackgroundConfig: {},
      styleConfig: DEFAULT_DASHBOARD_STYLE_CONFIG,
    },
    updateThemeConfig: () => undefined,
    resetTheme: () => undefined,
  }
}

const richLocalCacheStats = {
  directories: [
    {
      key: 'images',
      label: 'images',
      path: '/images',
      exists: true,
      file_count: 3,
      total_size: 1536,
      db_records: 0,
    },
    {
      key: 'emoji',
      label: 'emoji',
      path: '/emoji',
      exists: true,
      file_count: 1,
      total_size: 0,
      db_records: 8,
    },
    {
      key: 'logs',
      label: 'logs',
      path: '/logs',
      exists: true,
      file_count: 2,
      total_size: 10 * 1024,
      db_records: 0,
    },
  ],
  database: {
    total_size: 2 * 1024 * 1024,
    files: [{ path: 'a.db' }, { path: 'b.db' }],
    tables: [{ name: 't1' }, { name: 't2' }, { name: 't3' }],
  },
}

beforeEach(() => {
  vi.mocked(backendApi.get).mockImplementation((path: string) => {
    if (path.includes('/system/status')) return Promise.resolve(botStatus) as never
    if (path.includes('/statistics/dashboard')) return Promise.resolve(dashboardData) as never
    if (path.includes('/config/bot')) {
      return Promise.resolve({ config: { bot: { qq_account: '123456' } } }) as never
    }
    return Promise.resolve({}) as never
  })
  vi.mocked(configApi.getBotConfigCached).mockResolvedValue({} as never)
  vi.mocked(configApi.getModelConfigCached).mockResolvedValue({} as never)
  vi.mocked(expressionApi.getReviewStats).mockResolvedValue({ unchecked: 3, passed: 10 } as never)
  vi.mocked(systemApi.getLocalCacheStats).mockResolvedValue({
    directories: [],
    database: { total_size: 0, files: [], tables: [] },
  } as never)
  vi.mocked(pluginApi.getInstalledPlugins).mockResolvedValue([] as never)
  vi.mocked(pluginApi.getPluginHomeCards).mockResolvedValue([])
  mocks.triggerRestart.mockResolvedValue(undefined)
  mockBrowserFetch()
})

describe('IndexPage 特征化', () => {
  it('初始加载调用各数据源 API（仪表盘/状态/审核统计/本地缓存/配置）', async () => {
    render(<IndexPage />)
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith(
        '/api/webui/statistics/dashboard',
        expect.objectContaining({ query: { hours: 24 } })
      )
    )
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith(expect.stringContaining('/system/status'))
    )
    expect(expressionApi.getReviewStats).toHaveBeenCalled()
    expect(systemApi.getLocalCacheStats).toHaveBeenCalled()
    expect(configApi.getBotConfigCached).toHaveBeenCalled()
  })

  it('一言通过原生 fetch 拉取', async () => {
    render(<IndexPage />)
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('hitokoto')))
  })

  it('缺少 randomUUID 时仍可停用默认一言、维护自定义列表，并在列表为空时留空', async () => {
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      configurable: true,
      writable: true,
      value: undefined,
    })
    const user = userEvent.setup()
    render(<IndexPage />)

    await screen.findByText(/测试一言/)
    await user.click(screen.getByRole('button', { name: 'home.hitokoto.edit' }))
    await user.click(screen.getByRole('switch', { name: 'home.hitokoto.editor.defaultSource' }))
    await user.click(screen.getByText('home.hitokoto.editor.empty'))
    await user.type(
      screen.getByRole('textbox', { name: 'home.hitokoto.editor.content' }),
      '自定义的一言'
    )
    await user.type(
      screen.getByRole('textbox', { name: 'home.hitokoto.editor.source' }),
      '自定义出处'
    )
    await user.click(screen.getByRole('button', { name: 'common.save' }))

    expect(await screen.findByText(/自定义的一言/)).toHaveTextContent('自定义出处')
    expect(
      JSON.parse(localStorage.getItem('maibot-home-hitokoto-settings-v1') ?? '{}')
    ).toMatchObject({
      defaultEnabled: false,
      customItems: [
        { id: expect.any(String), content: '自定义的一言', source: '自定义出处' },
      ],
    })

    await user.click(screen.getByRole('button', { name: 'home.hitokoto.edit' }))
    await user.click(screen.getByRole('button', { name: 'home.hitokoto.editor.remove' }))
    await user.click(screen.getByRole('button', { name: 'common.save' }))

    await waitFor(() => expect(screen.queryByText(/自定义的一言/)).not.toBeInTheDocument())
    expect(document.querySelector('[data-home-hitokoto="true"] p')).not.toBeInTheDocument()
  })

  it('运行状态与精简运行时长纵向排列，并与功能灯分层展示', async () => {
    render(<IndexPage />)

    const runtimeLabel = await screen.findByText('home.botStatus.running')
    expect(runtimeLabel).toHaveAttribute('data-maibot-runtime-label', 'true')
    expect(runtimeLabel).toHaveClass('text-primary')
    expect(runtimeLabel).not.toHaveClass('text-green-600')
    expect(document.querySelector('[data-maibot-activity-orbit="true"]')).toHaveAttribute(
      'data-state',
      'running'
    )
    const runtimeUptime = screen.getByText('home.botStatus.uptime')
    expect(runtimeUptime).toHaveAttribute('data-maibot-runtime-uptime', 'true')
    expect(runtimeUptime).toHaveClass('text-xs', 'text-left', 'tabular-nums', 'whitespace-nowrap')
    expect(runtimeLabel).toHaveClass('whitespace-nowrap')
    expect(runtimeLabel.parentElement).toHaveClass('flex-col', 'items-start')
    expect(screen.queryByText('home.botStatus.uptimeLabel')).not.toBeInTheDocument()

    const featureLights = document.querySelector('[data-maibot-feature-lights="true"]')
    expect(featureLights).toHaveClass('grid-cols-2')
    expect(within(featureLights as HTMLElement).getAllByRole('status')).toHaveLength(2)
    for (const light of featureLights?.querySelectorAll(
      '[data-dashboard-feature-status-light="true"]'
    ) ?? []) {
      expect(light).toHaveClass('rounded-full', 'border-0')
    }
  })

  it('活动卡片可点击翻转到最近在线图表，并提供轻微悬停高亮', async () => {
    const user = userEvent.setup()
    render(<IndexPage />)

    const statusCard = await screen.findByRole('button', {
      name: 'home.botStatus.showRecentOnline',
    })
    const frontFace = statusCard.querySelector('[data-maibot-status-face="front"]')
    const backFace = statusCard.querySelector('[data-maibot-status-face="back"]')

    expect(frontFace).toHaveAttribute('aria-hidden', 'false')
    expect(backFace).toHaveAttribute('aria-hidden', 'true')
    expect(statusCard.parentElement).toHaveClass('overflow-visible')
    expect(statusCard.querySelector('[data-maibot-status-glow="true"]')).toBeInTheDocument()
    expect(statusCard.querySelector('[data-maibot-status-rotor="true"]')).toHaveClass(
      '[transform-style:preserve-3d]'
    )
    for (const face of [frontFace, backFace]) {
      expect(face).not.toHaveClass('overflow-hidden')
      expect(face?.querySelector('[data-maibot-status-surface="true"]')).toHaveClass(
        'overflow-hidden'
      )
    }

    await user.click(statusCard)

    expect(statusCard).toHaveAccessibleName('home.botStatus.showStatus')
    expect(statusCard).toHaveAttribute('aria-pressed', 'true')
    expect(frontFace).toHaveAttribute('aria-hidden', 'true')
    expect(backFace).toHaveAttribute('aria-hidden', 'false')
    expect(screen.getByText('home.botStatus.recentOnline')).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'home.botStatus.recentOnlineChart' })
    ).toBeInTheDocument()
  })

  it('首页使用精简版本行且不再显示标题和版本卡片', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem(
      'maibot-home-card-layout-v1',
      JSON.stringify({
        order: [
          'builtin:bot-status',
          'builtin:quick-actions',
          'builtin:stats-overview',
          'builtin:storage',
        ],
        hidden: [],
        rowModes: {},
      })
    )
    render(<IndexPage />)

    expect(await screen.findByText('V1.0.0')).toBeInTheDocument()
    expect(screen.getByText(`V${APP_VERSION}`)).toBeInTheDocument()
    expect(
      await screen.findByRole('link', { name: /home\.versionCard\.updateAvailable V2\.0\.0/ })
    ).toBeInTheDocument()
    expect(screen.queryByText('home.title')).not.toBeInTheDocument()
    expect(screen.queryByText('home.versionCard.title')).not.toBeInTheDocument()
    expect(screen.queryByText('MaiBot 数据导入导出')).not.toBeInTheDocument()

    await screen.findByText('home.storage.manage')
    expect(screen.queryByText('home.quickActions.title')).not.toBeInTheDocument()
    expect(screen.queryByText('home.storage.title')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'home.quickActions.customize' })
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    const quickActionsCard = document.querySelector('[data-home-card-id="builtin:quick-actions"]')
    expect(quickActionsCard).toBeInTheDocument()
    await user.click(
      within(quickActionsCard as HTMLElement).getByRole('button', {
        name: 'home.cards.editCard',
      })
    )
    const customizeButton = screen.getByRole('button', { name: 'home.quickActions.customize' })
    expect(customizeButton).toBeInTheDocument()
    expect(document.querySelector('[data-home-storage-details="true"]')).toHaveClass(
      'lg:grid-cols-2'
    )
    const storageRows = document.querySelectorAll('[data-home-storage-row="true"]')
    expect(storageRows).toHaveLength(4)
    for (const row of storageRows) {
      expect(row).toHaveClass('grid', 'items-baseline')
      expect(row.querySelector('[data-home-storage-progress="true"]')).toBeInTheDocument()
    }
    const cardIds = Array.from(document.querySelectorAll('[data-home-card-id]')).map((card) =>
      card.getAttribute('data-home-card-id')
    )
    expect(cardIds.slice(0, 3)).toEqual([
      'builtin:bot-status',
      'builtin:quick-actions',
      'builtin:storage',
    ])
  })

  it('存储管理入口使用完整文案和方向箭头', async () => {
    render(<IndexPage />)

    expect(await screen.findByText('home.storage.manage')).toBeInTheDocument()
    expect(document.querySelector('[data-home-storage-action="true"]')).toBeInTheDocument()
    expect(document.querySelector('[data-home-storage-action-line="true"]')).not.toBeInTheDocument()
  })

  it('插件首页卡片可以隐藏卡面标题', async () => {
    render(
      <HomeCardManager
        cards={[]}
        pluginCards={[
          {
            id: 'plugin:test:titleless',
            name: 'titleless',
            plugin_id: 'test',
            title: '仅用于管理的标题',
            show_title: false,
            description: '',
            content: '无标题卡片内容',
            link_url: '',
            link_label: '',
            icon: '',
            width: 'medium',
            order: 1000,
            enabled: true,
          },
        ]}
      />
    )

    expect(await screen.findByText('无标题卡片内容')).toBeInTheDocument()
    expect(screen.queryByText('仅用于管理的标题')).not.toBeInTheDocument()
  })

  it('切换时间范围以新的 hours 重新拉取仪表盘', async () => {
    const user = userEvent.setup()
    render(<IndexPage />)
    // 每张统计积木都拥有独立的轻量时间范围按钮。
    const sevenDayButtons = await screen.findAllByRole('button', { name: /home\.timeRange\.7d/ })
    await user.click(sevenDayButtons[0])
    await waitFor(() =>
      expect(backendApi.get).toHaveBeenCalledWith(
        '/api/webui/statistics/dashboard',
        expect.objectContaining({ query: { hours: 168 } })
      )
    )
  })

  it('统计卡片隐藏描述并分别显示全部与聊天缓存命中率', async () => {
    render(<IndexPage />)

    expect(await screen.findByText('50.00%')).toBeInTheDocument()
    expect(screen.getByText('60.00%')).toBeInTheDocument()
    expect(screen.getByText('home.cache.all')).toBeInTheDocument()
    expect(screen.getByText('home.cache.chat')).toBeInTheDocument()
    expect(screen.queryByText('home.cache.description')).not.toBeInTheDocument()
    expect(screen.queryByText('home.stats.overviewDesc')).not.toBeInTheDocument()
    expect(screen.queryByText('home.charts.requestTrendDescCompact')).not.toBeInTheDocument()
    expect(document.querySelectorAll('[data-home-summary-primary="true"]')).toHaveLength(2)
    expect(document.querySelector('[data-home-summary-secondary="true"]')?.children).toHaveLength(4)
  })
})

describe('IndexPage 仪表盘错误与加载', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
  })

  it('首次加载失败时展示整页重试，点击后恢复首页', async () => {
    const user = userEvent.setup()
    let dashboardCalls = 0
    stubBackendGet({
      dashboard: () => {
        dashboardCalls += 1
        return dashboardCalls === 1
          ? Promise.reject(new Error('统计服务挂了'))
          : Promise.resolve(dashboardData)
      },
    })

    await renderFreshIndexPage()

    expect(await screen.findByText('仪表盘加载失败')).toBeInTheDocument()
    expect(screen.getByText('统计服务挂了')).toBeInTheDocument()
    expect(document.querySelector('[data-home-page="true"]')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /重新加载/ }))

    expect(await screen.findByText('home.botStatus.running')).toBeInTheDocument()
    expect(document.querySelector('[data-home-page="true"]')).toBeInTheDocument()
    expect(screen.queryByText('仪表盘加载失败')).not.toBeInTheDocument()
  })

  it('首次加载抛出非 Error 时使用兜底失败文案', async () => {
    stubBackendGet({
      dashboard: () => Promise.reject('down'),
    })

    await renderFreshIndexPage()

    expect(await screen.findByText('仪表盘加载失败')).toBeInTheDocument()
    expect(screen.getByText('仪表盘数据加载失败，请稍后重试')).toBeInTheDocument()
  })

  it('已有数据时刷新失败改为横幅重试，不卸载整页', async () => {
    const user = userEvent.setup()
    render(<IndexPage />)
    await screen.findByText('home.botStatus.running')
    cleanup()

    expireModuleCaches(6 * 60_000)
    stubBackendGet({
      dashboard: () => Promise.reject(new Error('刷新失败')),
    })
    render(<IndexPage />)

    expect(await screen.findByText('刷新失败')).toBeInTheDocument()
    expect(document.querySelector('[data-home-page="true"]')).toBeInTheDocument()
    expect(screen.queryByText('仪表盘加载失败')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /重新加载/ }))
    await waitFor(() =>
      expect(mocks.backendGet.mock.calls.filter(([path]) => String(path).includes('/statistics/dashboard')).length).toBeGreaterThan(1)
    )
  })

  it('仪表盘尚未返回时展示加载动画和进度', async () => {
    stubBackendGet({
      dashboard: () => new Promise(() => undefined),
    })

    await renderFreshIndexPage()

    expect(await screen.findByRole('status', { name: '加载中' })).toBeInTheDocument()
    expect(screen.getByText('0%')).toBeInTheDocument()
    expect(document.querySelector('[data-home-page="true"]')).not.toBeInTheDocument()
  })
})

describe('IndexPage 平台账号引导', () => {
  it.skip('缺少 bot 配置时视为未配置并展示引导', async () => {
    stubBackendGet({
      platform: { config: {} },
    })
    render(<IndexPage />)

    expect(await screen.findByText('home.platformGuide.title')).toBeInTheDocument()
    expect(screen.getByText('home.platformGuide.description')).toBeInTheDocument()
    expect(screen.getByText('home.platformGuide.action')).toBeInTheDocument()
  })

  it.skip('qq_account 为 0 或空字符串时展示引导', async () => {
    stubBackendGet({
      platform: { config: { bot: { qq_account: 0 } } },
    })
    const view = render(<IndexPage />)
    expect(await screen.findByText('home.platformGuide.title')).toBeInTheDocument()
    view.unmount()

    stubBackendGet({
      platform: { config: { bot: { qq_account: '   ' } } },
    })
    render(<IndexPage />)
    expect(await screen.findByText('home.platformGuide.title')).toBeInTheDocument()
  })

  it('platforms 解析到有效账号后不展示引导', async () => {
    stubBackendGet({
      platform: {
        config: { bot: { qq_account: '0', platforms: ['qq:0', 'napcat:123456:extra'] } },
      },
    })
    render(<IndexPage />)

    await screen.findByText('home.botStatus.running')
    expect(screen.queryByText('home.platformGuide.title')).not.toBeInTheDocument()
  })

  it.skip('platforms 仅含未配置账号时仍展示引导', async () => {
    stubBackendGet({
      platform: { config: { bot: { platforms: ['qq:', 'napcat:0', null] } } },
    })
    render(<IndexPage />)

    expect(await screen.findByText('home.platformGuide.title')).toBeInTheDocument()
  })

  it.skip('读取平台配置失败时不展示引导', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    stubBackendGet({
      platform: () => Promise.reject(new Error('配置服务不可用')),
    })
    render(<IndexPage />)

    await screen.findByText('home.botStatus.running')
    expect(screen.queryByText('home.platformGuide.title')).not.toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalledWith('读取平台账号配置失败:', expect.any(Error))
  })
})

describe('IndexPage 功能灯与运行时状态', () => {
  it('记忆与视觉开启时功能灯为启用态', async () => {
    vi.mocked(configApi.getBotConfigCached).mockResolvedValue({
      config: { a_memorix: { plugin: { enabled: true } } },
    } as never)
    vi.mocked(configApi.getModelConfigCached).mockResolvedValue({
      config: { model_task_config: { vlm: { model_list: ['gpt-4v'] } } },
    } as never)
    render(<IndexPage />)

    const visual = await screen.findByRole('status', {
      name: 'home.botStatus.visualEnabled：home.botStatus.enabled',
    })
    const memory = screen.getByRole('status', {
      name: 'home.botStatus.memoryEnabled：home.botStatus.enabled',
    })
    expect(visual).toHaveAttribute('data-enabled', 'true')
    expect(memory).toHaveAttribute('data-enabled', 'true')
  })

  it('配置缺失时功能灯保持关闭', async () => {
    render(<IndexPage />)

    const visual = await screen.findByRole('status', {
      name: 'home.botStatus.visualEnabled：home.botStatus.disabled',
    })
    expect(visual).toHaveAttribute('data-enabled', 'false')
    expect(
      screen.getByRole('status', { name: 'home.botStatus.memoryEnabled：home.botStatus.disabled' })
    ).toHaveAttribute('data-enabled', 'false')
  })

  it('机器人已停止时使用危险色标签', async () => {
    expireModuleCaches(31_000)
    stubBackendGet({
      bot: { ...botStatus, running: false },
    })
    render(<IndexPage />)

    const runtimeLabel = await screen.findByText('home.botStatus.stopped')
    expect(runtimeLabel).toHaveClass('text-destructive')
    expect(document.querySelector('[data-maibot-activity-orbit="true"]')).toHaveAttribute(
      'data-state',
      'stopped'
    )
  })

  it('状态接口失败且无缓存时展示未知态', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    stubBackendGet({
      bot: () => Promise.reject(new Error('状态不可用')),
    })
    await renderFreshIndexPage()

    const runtimeLabel = await screen.findByText('home.botStatus.unknown')
    expect(runtimeLabel).toHaveClass('text-muted-foreground')
    expect(document.querySelector('[data-maibot-activity-orbit="true"]')).toHaveAttribute(
      'data-state',
      'unknown'
    )
    expect(document.querySelector('[data-maibot-runtime-uptime="true"]')).not.toBeInTheDocument()
  })

  it('状态尚未返回时展示加载轨道', async () => {
    stubBackendGet({
      bot: () => new Promise(() => undefined),
    })
    await renderFreshIndexPage()

    const runtimeLabel = await screen.findByText('home.botStatus.loading')
    expect(runtimeLabel).toHaveClass('text-muted-foreground')
    expect(document.querySelector('[data-maibot-activity-orbit="true"]')).toHaveAttribute(
      'data-state',
      'loading'
    )
  })
})

describe('IndexPage 存储卡片', () => {
  it('可在体积与数量模式间切换，并格式化字节', async () => {
    const user = userEvent.setup()
    vi.mocked(systemApi.getLocalCacheStats).mockResolvedValue(richLocalCacheStats as never)
    expireModuleCaches(16 * 60_000)
    render(<IndexPage />)

    // 总量与数据库条目都是 2.0 MB
    expect(await screen.findAllByText('2.0 MB')).toHaveLength(2)
    expect(screen.getByText('1.5 KB')).toBeInTheDocument()
    expect(screen.getByText('10 KB')).toBeInTheDocument()
    expect(screen.getByText('0 B')).toBeInTheDocument()
    expect(document.querySelectorAll('[data-home-storage-progress="true"]')).toHaveLength(4)

    await user.click(screen.getByRole('button', { name: 'home.storage.switchDisplay' }))

    expect(screen.getByText('home.storage.countSummary')).toBeInTheDocument()
    expect(screen.getByText('home.storage.filesAndRecords')).toBeInTheDocument()
    expect(screen.getByText('home.storage.databaseDetail')).toBeInTheDocument()
    expect(document.querySelector('[data-home-storage-progress="true"]')).not.toBeInTheDocument()
    const countRows = document.querySelectorAll('[data-home-storage-row="true"]')
    expect(countRows[0]).toHaveClass('flex')
    expect(countRows[0]).not.toHaveClass('grid')
  })

  it('本地缓存读取中展示占位文案', async () => {
    vi.mocked(systemApi.getLocalCacheStats).mockImplementation(() => new Promise(() => undefined))
    await renderFreshIndexPage()

    expect(await screen.findByText('home.storage.reading')).toBeInTheDocument()
    expect(screen.getByText('home.storage.readingDescription')).toBeInTheDocument()
  })

  it('本地缓存失败后展示不可用', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.mocked(systemApi.getLocalCacheStats).mockRejectedValue(new Error('缓存统计失败'))
    await renderFreshIndexPage()

    expect(await screen.findByText('home.storage.unavailable')).toBeInTheDocument()
    expect(screen.getByText('-')).toBeInTheDocument()
  })
})

describe('IndexPage 默认隐藏的统计卡片', () => {
  it('可通过添加面板恢复花费、模型分布、模型明细和日统计卡片', async () => {
    const user = userEvent.setup()
    render(<IndexPage />)
    await screen.findByText('home.botStatus.running')

    expect(document.querySelector('[data-home-card-id="builtin:cost-trend"]')).not.toBeInTheDocument()
    expect(
      document.querySelector('[data-home-card-id="builtin:model-distribution"]')
    ).not.toBeInTheDocument()
    expect(document.querySelector('[data-home-card-id="builtin:model-details"]')).not.toBeInTheDocument()
    expect(
      document.querySelector('[data-home-card-id="builtin:daily-statistics"]')
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'home.cards.add' }))
    expect(screen.getByText('home.charts.costTrend')).toBeInTheDocument()
    expect(screen.getByText('home.charts.modelDistribution')).toBeInTheDocument()
    expect(screen.getByText('home.charts.modelDetails')).toBeInTheDocument()
    expect(screen.getByText('home.charts.dailyStats')).toBeInTheDocument()

    for (const button of screen.getAllByRole('button', { name: 'home.cards.dialog.restore' })) {
      await user.click(button)
    }
    await user.click(screen.getByRole('button', { name: 'home.cards.dialog.cancel' }))

    expect(document.querySelector('[data-home-card-id="builtin:cost-trend"]')).toBeInTheDocument()
    expect(
      document.querySelector('[data-home-card-id="builtin:model-distribution"]')
    ).toBeInTheDocument()
    expect(document.querySelector('[data-home-card-id="builtin:model-details"]')).toBeInTheDocument()
    expect(
      document.querySelector('[data-home-card-id="builtin:daily-statistics"]')
    ).toBeInTheDocument()
    expect(screen.getByLabelText('home.charts.costTrend')).toBeInTheDocument()
    expect(screen.getByLabelText('home.charts.dailyStats')).toBeInTheDocument()
    expect(screen.getByLabelText('home.charts.tokenUsage')).toBeInTheDocument()
    expect(screen.getByText('¥5.00')).toBeInTheDocument()
    expect(screen.getByText('home.charts.requestCount:')).toBeInTheDocument()
    expect(screen.getAllByText('gpt-4').length).toBeGreaterThan(0)
  })

  it('首页会渲染已启用的插件卡片', async () => {
    vi.mocked(pluginApi.getPluginHomeCards).mockResolvedValue([
      {
        id: 'plugin:demo:card',
        name: 'demo',
        plugin_id: 'demo',
        title: '插件卡片标题',
        description: '插件卡片描述',
        content: '插件卡片正文',
        link_url: '/plugins',
        link_label: '打开插件',
        icon: '',
        width: 'medium',
        order: 1,
        enabled: true,
      },
    ])
    render(<IndexPage />)

    expect(await screen.findByText('插件卡片标题')).toBeInTheDocument()
    expect(screen.getByText('插件卡片正文')).toBeInTheDocument()
    expect(document.querySelector('[data-home-card-id="plugin:demo:card"]')).toBeInTheDocument()
  })
})

describe('IndexPage 一言与版本条', () => {
  it('默认一言接口失败时回退到内置文案', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    mockBrowserFetch({
      hitokoto: () => jsonResponse({}, false, 500),
    })
    render(<IndexPage />)

    expect(await screen.findByText(/home\.hitokotoFallback/)).toHaveTextContent(
      'home.hitokotoFallbackFrom'
    )
  })

  it('默认一言缺少 from 时依次使用 from_who 与未知来源', async () => {
    mockBrowserFetch({
      hitokoto: () => jsonResponse({ hitokoto: '谁说的', from_who: '作者' }),
    })
    const first = render(<IndexPage />)
    expect(await screen.findByText(/谁说的/)).toHaveTextContent('作者')
    first.unmount()

    mockBrowserFetch({
      hitokoto: () => jsonResponse({ hitokoto: '无出处' }),
    })
    render(<IndexPage />)
    expect(await screen.findByText(/无出处/)).toHaveTextContent('home.unknownSource')
  })

  it('一言加载中展示骨架', async () => {
    mockBrowserFetch({
      hitokoto: () => new Promise(() => undefined),
    })
    render(<IndexPage />)

    await screen.findByText('home.botStatus.running')
    const hitokoto = document.querySelector('[data-home-hitokoto="true"]')
    expect(hitokoto?.querySelector('.animate-pulse')).toBeInTheDocument()
    expect(hitokoto?.querySelector('p')).not.toBeInTheDocument()
  })

  it('modern 主题下一言使用虚线边框与斜体', async () => {
    render(
      <ThemeProviderContext.Provider value={makeThemeState('modern')}>
        <IndexPage />
      </ThemeProviderContext.Provider>
    )

    const quote = await screen.findByText(/测试一言/)
    expect(quote).toHaveClass('italic', 'text-sm')
    expect(document.querySelector('[data-home-hitokoto="true"]')).toHaveClass('border-dashed')
  })

  it('future-retro 主题下一言使用衬线字重而不是斜体', async () => {
    render(
      <ThemeProviderContext.Provider value={makeThemeState('future-retro')}>
        <IndexPage />
      </ThemeProviderContext.Provider>
    )

    const quote = await screen.findByText(/测试一言/)
    expect(quote).toHaveClass('font-medium')
    expect(quote).not.toHaveClass('italic')
    expect(quote).toHaveStyle({ fontFamily: '"MaiRetroQuote", "Noto Serif SC", "SimSun", serif' })
    expect(document.querySelector('[data-home-hitokoto="true"]')).not.toHaveClass('border-dashed')
  })

  it('自定义列表开启且默认源失败时仍展示自定义一言', async () => {
    window.localStorage.setItem(
      'maibot-home-hitokoto-settings-v1',
      JSON.stringify({
        defaultEnabled: true,
        customItems: [{ id: 'c1', content: '本地备用', source: '手帐' }],
      })
    )
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    mockBrowserFetch({
      hitokoto: () => jsonResponse({}, false, 503),
    })
    render(<IndexPage />)

    expect(await screen.findByText(/本地备用/)).toHaveTextContent('手帐')
  })

  it('损坏的一言本地设置回退到默认远程源', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    window.localStorage.setItem('maibot-home-hitokoto-settings-v1', '{not-json')
    render(<IndexPage />)

    expect(await screen.findByText(/测试一言/)).toBeInTheDocument()
    expect(errorSpy).toHaveBeenCalledWith('读取一言设置失败:', expect.any(Error))
  })

  it('版本不匹配时高亮命令条并展示提示', async () => {
    mockBrowserFetch({
      compatibility: () =>
        jsonResponse({
          status: 'webui_outdated',
          main_program_version: '2.0.0',
          webui_version: APP_VERSION,
          required_webui_version: '9.9.9',
        }),
    })
    render(<IndexPage />)

    expect(await screen.findByText('home.versionCard.mismatch')).toBeInTheDocument()
    expect(document.querySelector('[data-home-command-strip="true"]')).toHaveClass('text-amber-600')
  })

  it('机器人版本为空时显示未知，且版本相等不提示更新', async () => {
    expireModuleCaches(31_000)
    stubBackendGet({
      bot: { ...botStatus, version: '' },
    })
    mockBrowserFetch({
      github: () =>
        jsonResponse([{ tag_name: 'v1.0.0', draft: false, prerelease: false, html_url: '' }]),
    })
    render(<IndexPage />)

    expect(await screen.findByText('home.versionCard.unknown')).toBeInTheDocument()
    expect(screen.queryByText(/home\.versionCard\.updateAvailable/)).not.toBeInTheDocument()
  })

  it('GitHub 发布列表跳过 draft/prerelease，并比较出可更新版本', async () => {
    mockBrowserFetch({
      github: () =>
        jsonResponse([
          { tag_name: 'v9.0.0', draft: true, prerelease: false, html_url: 'https://example.test/draft' },
          { tag_name: 'v8.0.0', draft: false, prerelease: true, html_url: 'https://example.test/pre' },
          {
            tag_name: 'v1.2.0',
            draft: false,
            prerelease: false,
            html_url: 'https://example.test/stable',
          },
        ]),
    })
    render(<IndexPage />)

    const updateLink = await screen.findByRole('link', {
      name: /home\.versionCard\.updateAvailable V1\.2\.0/,
    })
    expect(updateLink).toHaveAttribute('href', 'https://example.test/stable')
  })

  it('点击版本按钮派发麦麦与控制台更新通知', async () => {
    const user = userEvent.setup()
    const targets: UpdateNoticeTarget[] = []
    const onNotice = (event: Event) => {
      targets.push((event as CustomEvent<UpdateNoticeTarget>).detail)
    }
    window.addEventListener(UPDATE_NOTICE_OPEN_EVENT, onNotice)
    render(<IndexPage />)
    await screen.findByText('V1.0.0')

    const versionButtons = document.querySelectorAll('[data-home-version-button="true"]')
    await user.click(versionButtons[0] as HTMLElement)
    await user.click(versionButtons[1] as HTMLElement)
    window.removeEventListener(UPDATE_NOTICE_OPEN_EVENT, onNotice)

    expect(targets).toEqual(['maibot', 'console'])
  })
})

describe('IndexPage 快捷操作与审核器', () => {
  it('默认快捷操作可重启、打开审核器，关闭后刷新统计', async () => {
    const user = userEvent.setup()
    render(<IndexPage />)
    await screen.findByText('home.botStatus.running')

    expect(document.querySelector('[data-quick-action-badge="true"]')).toHaveTextContent('3')
    expect(screen.getByRole('link', { name: /home\.quickActions\.viewLogs/ })).toHaveAttribute(
      'href',
      '/logs'
    )

    await user.click(screen.getByRole('button', { name: 'home.quickActions.restart' }))
    expect(mocks.triggerRestart).toHaveBeenCalledTimes(1)

    // 角标会拼进 accessible name（expressionReview3）
    await user.click(screen.getByRole('button', { name: /home\.quickActions\.expressionReview/ }))
    expect(await screen.findByTestId('expression-reviewer')).toBeInTheDocument()
    expect(expressionApi.getReviewStats).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'close-reviewer' }))
    await waitFor(() => expect(screen.queryByTestId('expression-reviewer')).not.toBeInTheDocument())
    await waitFor(() => expect(expressionApi.getReviewStats).toHaveBeenCalledTimes(2))
  })

  it('未审核数超过 99 时角标显示 99+', async () => {
    vi.mocked(expressionApi.getReviewStats).mockResolvedValue({
      unchecked: 128,
      passed: 1,
    } as never)
    render(<IndexPage />)

    await screen.findByText('home.botStatus.running')
    expect(document.querySelector('[data-quick-action-badge="true"]')).toHaveTextContent('99+')
  })

  it('快捷操作为空时引导添加，对话框支持搜索、勾选与恢复默认', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('maibot-home-quick-shortcuts', JSON.stringify(['unknown:none']))
    render(<IndexPage />)

    expect(await screen.findByText('home.quickActions.empty')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'home.quickActions.add' }))

    await user.type(
      screen.getByPlaceholderText('home.quickActions.dialog.searchPlaceholder'),
      'zzzz-no-match'
    )
    expect(screen.getByText('home.quickActions.dialog.noMatches')).toBeInTheDocument()

    await user.clear(screen.getByPlaceholderText('home.quickActions.dialog.searchPlaceholder'))
    await user.click(screen.getByRole('checkbox', { name: /home\.quickActions\.restart/ }))
    await user.click(screen.getByRole('button', { name: 'home.quickActions.dialog.done' }))
    expect(screen.getByRole('button', { name: 'home.quickActions.restart' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'home.cards.edit' }))
    const quickActionsCard = document.querySelector('[data-home-card-id="builtin:quick-actions"]')
    await user.click(
      within(quickActionsCard as HTMLElement).getByRole('button', { name: 'home.cards.editCard' })
    )
    await user.click(screen.getByRole('button', { name: 'home.quickActions.customize' }))
    await user.click(screen.getByRole('button', { name: 'home.quickActions.dialog.restoreDefault' }))
    await user.click(screen.getByRole('button', { name: 'home.quickActions.dialog.done' }))
    await user.click(screen.getByRole('button', { name: 'home.cards.done' }))

    expect(screen.getByRole('button', { name: 'home.quickActions.restart' })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /home\.quickActions\.expressionReview/ })
    ).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('maibot-home-quick-shortcuts') ?? '[]')).toEqual([
      'action:restart',
      'action:expression-review',
      'route:logs',
    ])
  })

  it('重启进行中时按钮禁用并旋转图标', async () => {
    mocks.isRestarting = true
    render(<IndexPage />)

    const restartButton = await screen.findByRole('button', {
      name: 'home.quickActions.restarting',
    })
    expect(restartButton).toBeDisabled()
    expect(restartButton.querySelector('svg')).toHaveClass('animate-spin')
  })
})
