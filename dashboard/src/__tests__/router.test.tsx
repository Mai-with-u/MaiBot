import { isRedirect } from '@tanstack/react-router'
import { describe, expect, it, vi } from 'vitest'

import { checkAuth } from '@/hooks/use-auth'
import { NotFoundPage } from '@/routes/404'
import { packDetailRoute, registeredRoutePaths, router } from '@/router'

// 路由表本体只做路径映射测试，不真实渲染各个页面，重组件全部替换为轻量桩
vi.mock('@tanstack/router-devtools', () => ({
  TanStackRouterDevtools: () => null,
}))
vi.mock('@/routes/404', () => ({
  NotFoundPage: () => <div>页面不存在</div>,
}))
vi.mock('@/components/layout', () => ({
  Layout: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
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

describe('router 路由表', () => {
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
})
