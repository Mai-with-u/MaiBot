import { createRootRoute, createRoute, createRouter, Outlet, redirect } from '@tanstack/react-router'
import { TanStackRouterDevtools } from '@tanstack/router-devtools'
import { IndexPage } from './routes/index'
import { SettingsPage } from './routes/settings'
import { AuthPage } from './routes/auth'
import { SetupPage } from './routes/setup'
import { NotFoundPage } from './routes/404'
import { BotConfigPage } from './routes/config/bot'
import { ModelProviderConfigPage } from './routes/config/modelProvider'
import { ModelConfigPage } from './routes/config/model'
import { AdapterConfigPage } from './routes/config/adapter'
import { EmojiManagementPage } from './routes/resource/emoji'
import { ExpressionManagementPage } from './routes/resource/expression'
import { JargonManagementPage } from './routes/resource/jargon'
import { PersonManagementPage } from './routes/person'
import { KnowledgeGraphPage } from './routes/resource/knowledge-graph'
import { KnowledgeBasePage } from './routes/resource/knowledge-base'
import { LogViewerPage } from './routes/logs'
import { PlannerMonitorPage } from './routes/monitor'
import { PluginsPage } from './routes/plugins'
import { ModelPresetsPage } from './routes/model-presets'
import { PluginConfigPage } from './routes/plugin-config'
import { PluginMirrorsPage } from './routes/plugin-mirrors'
import { PluginDetailPage } from './routes/plugin-detail'
import { ChatPage } from './routes/chat'
import { WebUIFeedbackSurveyPage, MaiBotFeedbackSurveyPage } from './routes/survey'
import { AnnualReportPage } from './routes/annual-report'
import PackMarketPage from './routes/config/pack-market'
import PackDetailPage from './routes/config/pack-detail'
import { Layout } from './components/layout'
import { checkAuth } from './hooks/use-auth'
import { RouteErrorBoundary } from './components/error-boundary'

// Root 路由
const rootRoute = createRootRoute({
  component: () => (
    <>
      <Outlet />
      {import.meta.env.DEV && <TanStackRouterDevtools />}
    </>
  ),
  beforeLoad: () => {
    // 如果访问根路径且未认证，重定向到认证页面
    if (window.location.pathname === '/' && !checkAuth()) {
      throw redirect({ to: '/auth' })
    }
  },
})

// 认证路由（无 Layout）
const authRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/auth',
  component: AuthPage,
})

// 首次配置路由（无 Layout）
const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/setup',
  component: SetupPage,
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

// 首页路由
const indexRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/',
  component: IndexPage,
})

// 配置路由 - 麦麦主程序配置
const botConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/bot',
  component: BotConfigPage,
})

// 配置路由 - 麦麦模型提供商配置
const modelProviderConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/modelProvider',
  component: ModelProviderConfigPage,
})

// 配置路由 - 麦麦模型配置
const modelConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/model',
  component: ModelConfigPage,
})

// 配置路由 - 麦麦适配器配置
const adapterConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/adapter',
  component: AdapterConfigPage,
})

// 资源管理路由 - 表情包管理
const emojiManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/emoji',
  component: EmojiManagementPage,
})

// 资源管理路由 - 表达方式管理
const expressionManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/expression',
  component: ExpressionManagementPage,
})

// 资源管理路由 - 人物信息管理
const personManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/person',
  component: PersonManagementPage,
})

// 资源管理路由 - 黑话管理
const jargonManagementRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/jargon',
  component: JargonManagementPage,
})

// 资源管理路由 - 知识库图谱可视化
const knowledgeGraphRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/knowledge-graph',
  component: KnowledgeGraphPage,
})

// 资源管理路由 - 麦麦知识库管理
const knowledgeBaseRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/resource/knowledge-base',
  component: KnowledgeBasePage,
})

// 日志查看器路由
const logsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/logs',
  component: LogViewerPage,
})

// 计划器&恢复器监控路由
const plannerMonitorRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/planner-monitor',
  component: PlannerMonitorPage,
})

// 本地聊天室路由
const chatRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/chat',
  component: ChatPage,
})

// 插件市场路由
const pluginsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugins',
  component: PluginsPage,
})

// 插件详情路由
const pluginDetailRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugin-detail',
  component: PluginDetailPage,
})

// 模型分配预设市场路由
const modelPresetsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/model-presets',
  component: ModelPresetsPage,
})

// 插件配置路由
const pluginConfigRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugin-config',
  component: PluginConfigPage,
})

// 插件镜像源配置路由
const pluginMirrorsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/plugin-mirrors',
  component: PluginMirrorsPage,
})

// 设置页路由
const settingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/settings',
  component: SettingsPage,
})

// 配置模板市场路由
const packMarketRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/pack-market',
  component: PackMarketPage,
})

// 配置模板详情路由
export const packDetailRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/config/pack-market/$packId',
  component: PackDetailPage,
})

// 问卷调查路由 - WebUI 反馈
const webuiFeedbackSurveyRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/survey/webui-feedback',
  component: WebUIFeedbackSurveyPage,
})

// 问卷调查路由 - 麦麦体验反馈
const maibotFeedbackSurveyRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/survey/maibot-feedback',
  component: MaiBotFeedbackSurveyPage,
})

// 年度报告路由
const annualReportRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/annual-report',
  component: AnnualReportPage,
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
  protectedRoute.addChildren([
    indexRoute,
    botConfigRoute,
    modelProviderConfigRoute,
    modelConfigRoute,
    adapterConfigRoute,
    emojiManagementRoute,
    expressionManagementRoute,
    jargonManagementRoute,
    personManagementRoute,
    knowledgeGraphRoute,
    knowledgeBaseRoute,
    pluginsRoute,
    pluginDetailRoute,
    modelPresetsRoute,
    pluginConfigRoute,
    pluginMirrorsRoute,
    logsRoute,
    plannerMonitorRoute,
    chatRoute,
    settingsRoute,
    packMarketRoute,
    packDetailRoute,
    webuiFeedbackSurveyRoute,
    maibotFeedbackSurveyRoute,
    annualReportRoute,
  ]),
  notFoundRoute,
])

// 创建路由器
export const router = createRouter({ 
  routeTree,
  defaultNotFoundComponent: NotFoundPage,
  defaultErrorComponent: ({ error }) => <RouteErrorBoundary error={error} />,
})

// 类型声明
declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
