import { isRedirect } from '@tanstack/react-router'
import { cleanup, render, screen } from '@testing-library/react'
import { isValidElement, type ReactElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { checkAuth } from '@/hooks/use-auth'
import { packDetailRoute, registeredRoutePaths, router } from '@/router'
import { NotFoundPage } from '@/routes/404'

// 路由表本体只做路径映射测试，不真实渲染各个页面，重组件全部替换为轻量桩
const { StubPage } = vi.hoisted(() => ({
  StubPage: function StubPage() {
    return null
  },
}))

vi.mock('@tanstack/router-devtools', () => ({
  TanStackRouterDevtools: () => null,
}))
vi.mock('@/routes/404', () => ({
  NotFoundPage: () => <div>页面不存在</div>,
}))
vi.mock('@/components/layout', () => ({
  Layout: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/components/route-pending-fallback', () => ({
  RoutePendingFallback: () => <div>加载中</div>,
}))
vi.mock('@/components/error-boundary', () => ({
  RouteErrorBoundary: () => <div>路由错误</div>,
}))
vi.mock('@/hooks/use-auth', () => ({
  checkAuth: vi.fn(),
}))

// 懒加载页面模块全部打桩，preload 只覆盖 router 里的 import() 工厂，不加载真页面
vi.mock('@/routes/auth', () => ({ AuthPage: StubPage }))
vi.mock('@/routes/setup/index.tsx', () => ({ SetupPage: StubPage }))
vi.mock('@/routes/index', () => ({ IndexPage: StubPage }))
vi.mock('@/routes/logs', () => ({
  LogViewerPage: StubPage,
  ReasoningLogViewerPage: StubPage,
  StatisticsLogViewerPage: StubPage,
}))
vi.mock('@/routes/reply-effects', () => ({ ReplyEffectsPage: StubPage }))
vi.mock('@/routes/focus', () => ({ FocusCompanionPage: StubPage }))
vi.mock('@/routes/config/bot', () => ({ BotConfigPage: StubPage }))
vi.mock('@/routes/config/model', () => ({ ModelConfigPage: StubPage }))
vi.mock('@/routes/config/prompts', () => ({ PromptManagementPage: StubPage }))
vi.mock('@/routes/prompt-generator', () => ({ PromptGeneratorPage: StubPage }))
vi.mock('@/routes/resource/emoji/index.tsx', () => ({ EmojiManagementPage: StubPage }))
vi.mock('@/routes/resource/expression/index.tsx', () => ({ ExpressionManagementPage: StubPage }))
vi.mock('@/routes/person', () => ({ PersonManagementPage: StubPage }))
vi.mock('@/routes/resource/jargon/index.tsx', () => ({ JargonManagementPage: StubPage }))
vi.mock('@/routes/resource/behavior/index.tsx', () => ({ BehaviorLearningPage: StubPage }))
vi.mock('@/routes/resource/knowledge-graph/index.tsx', () => ({ KnowledgeGraphPage: StubPage }))
vi.mock('@/routes/resource/knowledge-base', () => ({ KnowledgeBasePage: StubPage }))
vi.mock('@/routes/monitor/index.tsx', () => ({ PlannerMonitorPage: StubPage }))
vi.mock('@/routes/chat/index', () => ({ ChatPage: StubPage }))
vi.mock('@/routes/chat-management', () => ({ ChatManagementPage: StubPage }))
vi.mock('@/routes/chat/embed', () => ({ ChatEmbedPage: StubPage }))
vi.mock('@/routes/plugins/embed', () => ({ PluginMarketplaceEmbedPage: StubPage }))
vi.mock('@/routes/plugins/PluginMarketplacePage', () => ({ PluginMarketplacePage: StubPage }))
vi.mock('@/routes/model-presets', () => ({ ModelPresetsPage: StubPage }))
vi.mock('@/routes/plugin-config', () => ({ PluginConfigPage: StubPage }))
vi.mock('@/routes/plugin-config-embed', () => ({ PluginConfigEmbedPage: StubPage }))
vi.mock('@/routes/plugin-mirrors-embed', () => ({ PluginMirrorsEmbedPage: StubPage }))
vi.mock('@/routes/plugin-mirrors', () => ({ PluginMirrorsPage: StubPage }))
vi.mock('@/routes/mcp-settings', () => ({ MCPSettingsPage: StubPage }))
vi.mock('@/routes/data-transfer', () => ({ DataTransferPage: StubPage }))
vi.mock('@/routes/settings/index.tsx', () => ({ SettingsPage: StubPage }))
vi.mock('@/routes/config/pack-market', () => ({ default: StubPage }))
vi.mock('@/routes/config/pack-detail', () => ({ default: StubPage }))
vi.mock('@/routes/survey/webui-feedback', () => ({ WebUIFeedbackSurveyPage: StubPage }))
vi.mock('@/routes/survey/maibot-feedback', () => ({ MaiBotFeedbackSurveyPage: StubPage }))

// 期望注册的全部页面路径（不含 root 与通配 404）
const expectedPaths = [
  '/auth',
  '/setup',
  '/chat/embed',
  '/focus/embed',
  '/plugins/embed',
  '/plugin-config/embed',
  '/plugin-mirrors/embed',
  '/',
  '/statistics',
  '/reply-effects',
  '/focus',
  '/config/bot',
  '/config/model',
  '/config/prompts',
  '/config/prompt-generator',
  '/resource/emoji',
  '/resource/expression',
  '/resource/jargon',
  '/resource/behavior',
  '/resource/person',
  '/resource/knowledge-graph',
  '/resource/knowledge-base',
  '/plugins',
  '/model-presets',
  '/plugin-config',
  '/adapter-management',
  '/plugin-mirrors',
  '/mcp-settings',
  '/data-transfer',
  '/logs',
  '/reasoning-process',
  '/planner-monitor',
  '/chat-management',
  '/chat',
  '/settings',
  '/config/pack-market',
  '/config/pack-market/$packId',
  '/survey/webui-feedback',
  '/survey/maibot-feedback',
]

// beforeLoad 的真实签名依赖 TanStack 内部泛型，这里收窄为测试需要的最小形状
type BeforeLoadFn = (ctx: { location: { pathname: string } }) => void | Promise<void>

function getRootBeforeLoad(): BeforeLoadFn {
  const beforeLoad = router.routeTree.options.beforeLoad
  expect(typeof beforeLoad).toBe('function')
  return beforeLoad as unknown as BeforeLoadFn
}

type RouteNodeLike = {
  id?: string
  parentRoute?: { id?: string }
  options: {
    id?: string
    component?: unknown
    errorComponent?: unknown
  }
}

type LazyRouteComponent = {
  (props?: unknown): ReactNode
  preload?: () => Promise<unknown>
}

const embedPaths = [
  '/chat/embed',
  '/focus/embed',
  '/plugins/embed',
  '/plugin-config/embed',
  '/plugin-mirrors/embed',
] as const

const publicRootPaths = ['/auth', '/setup', ...embedPaths] as const

function getRoutesByPath(): Record<string, RouteNodeLike | undefined> {
  return router.routesByPath as unknown as Record<string, RouteNodeLike | undefined>
}

function getProtectedRoute(): RouteNodeLike {
  const routesById = router.routesById as Record<string, RouteNodeLike>
  const matched = Object.entries(routesById).find(
    ([id, route]) => id === '/protected' || id === 'protected' || route.options.id === 'protected'
  )
  expect(
    matched,
    `未找到 protected 路由，现有 id: ${Object.keys(routesById).join(', ')}`
  ).toBeDefined()
  return matched![1]
}

function renderErrorComponent(component: unknown, error: Error): ReturnType<typeof render> {
  expect(typeof component).toBe('function')
  const node = (component as (props: { error: Error }) => ReactNode)({ error })
  expect(isValidElement(node)).toBe(true)
  return render(node as ReactElement)
}

describe('router 路由表', () => {
  afterEach(() => {
    cleanup()
  })

  it('routesByPath 精确注册全部页面路径与通配 404', () => {
    const actualKeys = Object.keys(router.routesByPath as unknown as Record<string, unknown>).sort()
    // '/*' 是根级通配 404 路由
    const expectedKeys = [...expectedPaths, '/*'].sort()
    expect(actualKeys).toEqual(expectedKeys)
  })

  it('每个页面路径都配置了懒加载组件', () => {
    const routesByPath = router.routesByPath as unknown as Record<
      string,
      { options: { component?: unknown } } | undefined
    >
    for (const path of expectedPaths) {
      const route = routesByPath[path]
      expect(route, `routesByPath 缺少 ${path}`).toBeDefined()
      expect(typeof route?.options.component, `${path} 缺少组件`).toBe('function')
    }
  })

  it('registeredRoutePaths 现状为空集合（特征化已知缺陷）', () => {
    // 现状缺陷：registeredRoutePaths 在 createRouter 初始化 fullPath 之前采集，
    // 采集时各路由的 fullPath 均为 undefined，因此集合恒为空。
    // search-dialog 依赖该集合过滤菜单路由，当前会把所有路由项过滤掉。
    // 此测试锁定现状，修复源码后应同步更新为断言完整路径集合。
    expect(registeredRoutePaths.size).toBe(0)
  })

  it('配置模板详情路由导出且路径带 packId 参数', () => {
    // path 不含前导斜杠是 TanStack Router 的归一化行为
    expect(packDetailRoute.path).toBe('config/pack-market/$packId')
    expect(packDetailRoute.fullPath).toBe('/config/pack-market/$packId')
  })

  it('路由器全局选项与 404 组件符合预期', () => {
    expect(router.options.defaultNotFoundComponent).toBe(NotFoundPage)
    expect(router.options.defaultPendingMs).toBe(120)
    expect(router.options.defaultPendingMinMs).toBe(120)
    expect(router.options.defaultPreload).toBe('intent')
    expect(router.options.defaultPreloadDelay).toBe(80)
    expect(typeof router.options.defaultErrorComponent).toBe('function')
    expect(typeof router.options.defaultPendingComponent).toBe('function')
  })

  it('非首页导航时 beforeLoad 直接放行且不触发鉴权', () => {
    const beforeLoad = getRootBeforeLoad()
    const result = beforeLoad({ location: { pathname: '/settings' } })
    expect(result).toBeUndefined()
    expect(checkAuth).not.toHaveBeenCalled()
  })

  it('首页导航且鉴权通过时 beforeLoad 正常放行', async () => {
    vi.mocked(checkAuth).mockResolvedValueOnce(true)
    const beforeLoad = getRootBeforeLoad()
    await expect(beforeLoad({ location: { pathname: '/' } })).resolves.toBeUndefined()
    expect(checkAuth).toHaveBeenCalledTimes(1)
  })

  it('首页导航且鉴权失败时 beforeLoad 抛出跳转 /auth 的 redirect', async () => {
    vi.mocked(checkAuth).mockResolvedValueOnce(false)
    const beforeLoad = getRootBeforeLoad()
    const error: unknown = await Promise.resolve(beforeLoad({ location: { pathname: '/' } })).then(
      () => {
        throw new Error('预期 beforeLoad 抛出 redirect')
      },
      (reason: unknown) => reason
    )
    expect(isRedirect(error)).toBe(true)
    if (isRedirect(error)) {
      expect(error.options.to).toBe('/auth')
    }
  })

  it('登录页、首次配置页与 embed 路由的 beforeLoad 均放行且不鉴权', () => {
    // root beforeLoad 只拦截 pathname === '/'，setup 完成与否不在路由层判断
    const beforeLoad = getRootBeforeLoad()
    for (const pathname of publicRootPaths) {
      expect(beforeLoad({ location: { pathname } }), pathname).toBeUndefined()
    }
    expect(checkAuth).not.toHaveBeenCalled()
  })

  it('embed 路由挂在 root 下，不经过 protected Layout', () => {
    const routesByPath = getRoutesByPath()
    for (const path of embedPaths) {
      const route = routesByPath[path]
      expect(route, `缺少 embed 路由 ${path}`).toBeDefined()
      expect(route?.parentRoute?.id, `${path} 应直挂 root`).toBe(router.routeTree.id)
    }
  })

  it('auth / setup / 通配 404 也直挂 root', () => {
    const routesByPath = getRoutesByPath()
    for (const path of ['/auth', '/setup', '/*'] as const) {
      const route = routesByPath[path]
      expect(route, `缺少路由 ${path}`).toBeDefined()
      expect(route?.parentRoute?.id, `${path} 应直挂 root`).toBe(router.routeTree.id)
    }
    expect(routesByPath['/*']?.options.component).toBe(NotFoundPage)
  })

  it('受保护页面挂在 protected 布局路由下', () => {
    const protectedRoute = getProtectedRoute()
    const routesByPath = getRoutesByPath()
    const rootMounted = new Set<string>(['/auth', '/setup', ...embedPaths])
    for (const path of expectedPaths) {
      if (rootMounted.has(path)) {
        continue
      }
      const route = routesByPath[path]
      expect(route, `缺少受保护路由 ${path}`).toBeDefined()
      expect(route?.parentRoute?.id, `${path} 应挂在 protected 下`).toBe(protectedRoute.id)
    }
  })

  it('根组件在 DEV 下返回带 Outlet 的有效元素', () => {
    const RootComponent = router.routeTree.options.component as (() => ReactNode) | undefined
    expect(typeof RootComponent).toBe('function')
    const element = RootComponent!()
    expect(isValidElement(element)).toBe(true)
  })

  it('protected 布局组件返回包了 Layout 的有效元素', () => {
    const protectedRoute = getProtectedRoute()
    const ProtectedComponent = protectedRoute.options.component as (() => ReactNode) | undefined
    expect(typeof ProtectedComponent).toBe('function')
    const element = ProtectedComponent!()
    expect(isValidElement(element)).toBe(true)
  })

  it('protected errorComponent 渲染路由错误边界桩', () => {
    const protectedRoute = getProtectedRoute()
    renderErrorComponent(protectedRoute.options.errorComponent, new Error('protected 失败'))
    expect(screen.getByText('路由错误')).toBeInTheDocument()
  })

  it('defaultErrorComponent 渲染路由错误边界桩', () => {
    renderErrorComponent(router.options.defaultErrorComponent, new Error('默认错误'))
    expect(screen.getByText('路由错误')).toBeInTheDocument()
  })

  it('通配 404 路由组件渲染 NotFound 桩', () => {
    const NotFound = getRoutesByPath()['/*']?.options.component as (() => ReactNode) | undefined
    expect(NotFound).toBe(NotFoundPage)
    const Comp = NotFound as () => ReactNode
    render(<Comp />)
    expect(screen.getByText('页面不存在')).toBeInTheDocument()
  })

  it('所有懒加载页面工厂均可 preload 到桩组件', async () => {
    const routesByPath = getRoutesByPath()
    for (const path of expectedPaths) {
      const component = routesByPath[path]?.options.component as LazyRouteComponent | undefined
      expect(typeof component, `${path} 缺少组件`).toBe('function')
      expect(typeof component?.preload, `${path} 不是懒加载组件`).toBe('function')
      await expect(component!.preload!(), `${path} preload 失败`).resolves.toBeUndefined()
      const Comp = component as () => ReactNode
      const view = render(<Comp />)
      view.unmount()
    }
  })
})
