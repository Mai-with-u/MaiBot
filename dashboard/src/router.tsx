import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
} from '@tanstack/react-router'
import { NotFoundPage } from './routes/404'
import { Layout } from './components/layout'
import { RoutePendingFallback } from './components/route-pending-fallback'
import { checkAuth } from './hooks/use-auth'
import { RouteErrorBoundary } from './components/error-boundary'
import { rememberMarketInstall } from './lib/plugin-market-navigation'

// Root 路由
const rootRoute = createRootRoute({
  component: Outlet,
  beforeLoad: ({ location }) => {
    // 只在目标路由确实是首页时异步鉴权。使用 window.location 会读到导航前的旧路径，
    // 并让离开首页的工作区切换无故进入 pending 状态。
    if (location.pathname !== '/') {
      return
    }

    return checkAuth().then((authenticated) => {
      if (!authenticated) {
        throw redirect({ to: '/auth' })
      }
    })
  },
})

// 认证路由（无 Layout）
const authRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/auth',
  component: lazyRouteComponent(() => import('./routes/auth'), 'AuthPage'),
})

// 首次配置路由（无 Layout）
const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/setup',
  component: lazyRouteComponent(() => import('./routes/setup/index.tsx'), 'SetupPage'),
})

// 受保护的路由 Root（带 Layout）
const protectedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'protected',
  component: () => (
    <Layout>
      <Outlet />
    </Layout>
  ),
  errorComponent: ({ error }) => <RouteErrorBoundary error={error} />,
})

const pluginWebUIRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/extensions/$pluginId/$pageId',
  component: lazyRouteComponent(() => import('./routes/plugin-webui'), 'PluginWebUIPage'),
})

const pluginWebUIManagerRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/extensions',
  beforeLoad: () => {
    throw redirect({ to: '/plugin-config', hash: 'webui-extensions', replace: true })
  },
})

// 首页路由
const indexRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/',
  component: lazyRouteComponent(() => import('./routes/index'), 'IndexPage'),
})

// 详细统计路由
const statisticsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/statistics',
  component: lazyRouteComponent(() => import('./routes/logs'), 'StatisticsLogViewerPage'),
})


// 配置路由 - 麦麦主程序配置
const botConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/bot',
  beforeLoad: ({ location }) => {
    const params = new URLSearchParams(location.searchStr)
    if (params.get('mode') !== 'webui') return
    params.delete('mode')
    const search = params.size ? `?${params.toString()}` : ''
    // 兼容此前内嵌 WebUI 设置的链接，保留页签参数和 hash。
    const hash = location.hash ? `#${location.hash.replace(/^#/, '')}` : ''
    throw redirect({ href: `/settings${search}${hash}`, replace: true })
  },
  component: lazyRouteComponent(() => import('./routes/config/bot'), 'BotConfigPage'),
})

// 配置路由 - 麦麦模型配置
const modelConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/model',
  component: lazyRouteComponent(() => import('./routes/config/model'), 'ModelConfigPage'),
})

// 配置路由 - Prompt 管理
const promptManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/prompts',
  component: lazyRouteComponent(() => import('./routes/config/prompts'), 'PromptManagementPage'),
})

// 旧表情包地址进入学习页的表情包页签。
const emojiManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/emoji',
  beforeLoad: () => {
    throw redirect({ to: '/resource/learning', search: { tab: 'emoji' }, replace: true })
  },
})

// 资源管理路由 - 学习（表达方式、黑话与表情包）
const learningRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/learning',
  validateSearch: (search: Record<string, unknown>): { tab: 'expression' | 'jargon' | 'emoji' } => ({
    tab: search.tab === 'jargon' || search.tab === 'emoji' ? search.tab : 'expression',
  }),
  component: lazyRouteComponent(() => import('./routes/resource/learning'), 'LearningPage'),
})

// 旧表达方式地址进入学习页的表达页签。
const expressionManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/expression',
  beforeLoad: () => {
    throw redirect({ to: '/resource/learning', search: { tab: 'expression' }, replace: true })
  },
})

// 资源管理路由 - 人物信息管理
const personManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/person',
  component: lazyRouteComponent(() => import('./routes/person'), 'PersonManagementPage'),
})

// 旧黑话地址进入学习页的黑话页签。
const jargonManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/jargon',
  beforeLoad: () => {
    throw redirect({ to: '/resource/learning', search: { tab: 'jargon' }, replace: true })
  },
})

// 资源管理路由 - 知识库图谱可视化

const knowledgeGraphRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/knowledge-graph',
  component: lazyRouteComponent(
    () => import('./routes/resource/knowledge-graph/index.tsx'),
    'KnowledgeGraphPage'
  ),
})

// 资源管理路由 - 麦麦知识库管理
const knowledgeBaseRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/knowledge-base',
  component: lazyRouteComponent(
    () => import('./routes/resource/knowledge-base'),
    'KnowledgeBasePage'
  ),
})

// 日志查看器路由
const logsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/logs',
  component: lazyRouteComponent(() => import('./routes/logs'), 'LogViewerPage'),
})

const reasoningProcessRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/reasoning-process',
  component: lazyRouteComponent(() => import('./routes/logs'), 'ReasoningLogViewerPage'),
})

// MaiSaka 聊天流监控路由
const plannerMonitorRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/planner-monitor',
  component: lazyRouteComponent(() => import('./routes/monitor/index.tsx'), 'PlannerMonitorPage'),
})

const computerRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/computer',
  component: lazyRouteComponent(() => import('./routes/computer'), 'ComputerPage'),
})

const filesRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/files',
  beforeLoad: () => {
    throw redirect({ to: '/data-transfer', search: { tab: 'files' }, replace: true })
  },
})

const agentRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/agent',
  component: lazyRouteComponent(() => import('./routes/agent'), 'AgentPage'),
})

// 移动端管理页路由：底部标签栏「管理」，汇总侧栏的全部入口
const manageRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/manage',
  component: lazyRouteComponent(() => import('./routes/manage'), 'ManagePage'),
})

// 本地聊天室路由
const chatRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/chat',
  validateSearch: (search: Record<string, unknown>): { setupTrial?: boolean; observe?: string } => ({
    setupTrial: search.setupTrial === true || search.setupTrial === 'true' ? true : undefined,
    observe: typeof search.observe === 'string' && search.observe ? search.observe : undefined,
  }),
  component: lazyRouteComponent(() => import('./routes/chat/index'), 'ChatPage'),
})

// 聊天管理路由
const chatManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/chat-management',
  component: lazyRouteComponent(() => import('./routes/chat-management'), 'ChatManagementPage'),
})

// 外部程序嵌入用聊天室路由，不挂载 dashboard 顶栏
const chatEmbedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/chat/embed',
  component: lazyRouteComponent(() => import('./routes/chat/embed'), 'ChatEmbedPage'),
})

// 外部程序嵌入用插件市场路由，不挂载 dashboard 顶栏和侧边栏
const pluginsEmbedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/plugins/embed',
  validateSearch: (search: Record<string, unknown>): { pluginId?: string } => ({
    pluginId: typeof search.pluginId === 'string' ? search.pluginId.trim() || undefined : undefined,
  }),
  component: lazyRouteComponent(
    () => import('./routes/plugins/embed'),
    'PluginMarketplaceEmbedPage'
  ),
})

// 插件市场路由
const pluginsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugins',
  beforeLoad: ({ search }) => { rememberMarketInstall(search) },
  validateSearch: (search: Record<string, unknown>): { pluginId?: string; installPluginId?: string; installVersion?: string } => ({
    pluginId: typeof search.pluginId === 'string' ? search.pluginId.trim() || undefined : undefined,
    installPluginId: typeof search.installPluginId === 'string' && /^[\p{L}\p{N}_.-]{1,256}$/u.test(search.installPluginId) ? search.installPluginId : undefined,
    installVersion: typeof search.installVersion === 'string' && /^[\w.+-]{1,80}$/.test(search.installVersion) ? search.installVersion : undefined,
  }),
  component: lazyRouteComponent(
    () => import('./routes/plugins/PluginMarketplacePage'),
    'PluginMarketplacePage'
  ),
})

// 模型分配预设市场路由
const modelPresetsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/model-presets',
  component: lazyRouteComponent(() => import('./routes/model-presets'), 'ModelPresetsPage'),
})

// 插件配置路由
const pluginConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugin-config',
  component: lazyRouteComponent(() => import('./routes/plugin-config'), 'PluginConfigPage'),
})

// 适配器管理路由：复用插件配置页，仅展示 adapter 类型插件
const adapterManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/adapter-management',
  component: lazyRouteComponent(() => import('./routes/plugin-config'), 'PluginConfigPage'),
})

// 外部程序嵌入用插件配置路由，不挂载 dashboard 顶栏和侧边栏
const pluginConfigEmbedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/plugin-config/embed',
  component: lazyRouteComponent(
    () => import('./routes/plugin-config-embed'),
    'PluginConfigEmbedPage'
  ),
})

// 外部程序嵌入用插件镜像源配置路由，不挂载 dashboard 顶栏和侧边栏
const pluginMirrorsEmbedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/plugin-mirrors/embed',
  component: lazyRouteComponent(
    () => import('./routes/plugin-mirrors-embed'),
    'PluginMirrorsEmbedPage'
  ),
})

// 插件镜像源配置路由
const pluginMirrorsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugin-mirrors',
  component: lazyRouteComponent(() => import('./routes/plugin-mirrors'), 'PluginMirrorsPage'),
})

// 旧 MCP 地址统一进入插件扩展。
const mcpSettingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/mcp-settings',
  beforeLoad: () => {
    throw redirect({ to: '/plugin-config' })
  },
})

// 数据管理：迁移备份与文件管理共用入口。
const dataTransferRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/data-transfer',
  validateSearch: (search: Record<string, unknown>): { tab?: 'transfer' | 'files' } => ({
    tab: search.tab === 'files' ? 'files' : undefined,
  }),
  component: lazyRouteComponent(() => import('./routes/data-transfer'), 'DataTransferPage'),
})

const settingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/settings',
  component: lazyRouteComponent(() => import('./routes/settings/index.tsx'), 'SettingsPage'),
})

// 配置模板市场路由
const packMarketRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/pack-market',
  component: lazyRouteComponent(() => import('./routes/config/pack-market')),
})

// 配置模板详情路由
export const packDetailRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/pack-market/$packId',
  component: lazyRouteComponent(() => import('./routes/config/pack-detail')),
})

// 问卷调查路由 - WebUI 反馈
const webuiFeedbackSurveyRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/survey/webui-feedback',
  component: lazyRouteComponent(
    () => import('./routes/survey/webui-feedback'),
    'WebUIFeedbackSurveyPage'
  ),
})

// 问卷调查路由 - 麦麦体验反馈
const maibotFeedbackSurveyRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/survey/maibot-feedback',
  component: lazyRouteComponent(
    () => import('./routes/survey/maibot-feedback'),
    'MaiBotFeedbackSurveyPage'
  ),
})

// 404 路由
const notFoundRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '*',
  component: NotFoundPage,
})

// 路由树
const routeTree = rootRoute.addChildren([
  authRoute,
  setupRoute,
  chatEmbedRoute,
  pluginsEmbedRoute,
  pluginConfigEmbedRoute,
  pluginMirrorsEmbedRoute,
  protectedRoute.addChildren([
    pluginWebUIRoute,
    pluginWebUIManagerRoute,
    indexRoute,
    statisticsRoute,
    botConfigRoute,
    modelConfigRoute,
    promptManagementRoute,
    emojiManagementRoute,
    learningRoute,
    expressionManagementRoute,
    jargonManagementRoute,
    personManagementRoute,
    knowledgeGraphRoute,
    knowledgeBaseRoute,
    pluginsRoute,
    modelPresetsRoute,
    pluginConfigRoute,
    adapterManagementRoute,
    pluginMirrorsRoute,
    mcpSettingsRoute,
    dataTransferRoute,
    logsRoute,
    reasoningProcessRoute,
    plannerMonitorRoute,
    agentRoute,
    computerRoute,
    filesRoute,
    chatManagementRoute,
    chatRoute,
    manageRoute,
    settingsRoute,
    packMarketRoute,
    packDetailRoute,
    webuiFeedbackSurveyRoute,
    maibotFeedbackSurveyRoute,
  ]),
  notFoundRoute,
])

type RouteNode = {
  fullPath?: string
  children?: RouteNode[]
}

function collectRoutePaths(node: RouteNode): string[] {
  const currentPath = node.fullPath ? [node.fullPath] : []
  const childPaths = node.children?.flatMap(collectRoutePaths) ?? []
  return [...currentPath, ...childPaths]
}

// 创建路由器
export const router = createRouter({
  routeTree,
  defaultNotFoundComponent: NotFoundPage,
  defaultErrorComponent: ({ error }) => <RouteErrorBoundary error={error} />,
  defaultPendingComponent: RoutePendingFallback,
  defaultPendingMs: 120,
  defaultPendingMinMs: 120,
  defaultPreload: 'intent',
  defaultPreloadDelay: 80,
})

// createRouter 初始化后才有 fullPath，搜索索引必须在此时采集已注册路径。
export const registeredRoutePaths = new Set(collectRoutePaths(routeTree as RouteNode))

// 类型声明
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
