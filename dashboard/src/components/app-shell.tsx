import { lazy, Suspense, type ReactNode } from 'react'

import { Layout } from '@/components/layout'
import { RoutePendingFallback } from '@/components/route-pending-fallback'
import { useEffectiveLayoutVersion } from '@/lib/layout-version'
import { useHostRuntime } from '@/host/state'
import { Link, useRouterState } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

const ObservationLayout = lazy(() =>
  import('@/components/observation-layout/ObservationLayout').then((module) => ({
    default: module.ObservationLayout,
  }))
)

/**
 * 受保护页面的外壳入口：按 WebUI 设置在经典布局与麦麦观察布局之间切换。
 * 两套外壳各自独立，页面组件共用。
 */
export function AppShell({ children }: { children: ReactNode }) {
  const version = useEffectiveLayoutVersion()
  const runtime = useHostRuntime()
  // Outlet 渲染的是 matches；导航期间 location 已经是目标地址，旧页面仍可能挂载。
  // 必须依据实际匹配的页面放行，避免返回管理页时短暂启动旧 Bot 页的请求。
  const pathname = useRouterState({ select: (state) => state.matches.at(-1)?.pathname })
  const search = useRouterState({ select: state => state.location.searchStr })
  const transferPage = pathname === '/data-transfer' && Boolean(runtime.selected_bot_id)
  const commonSettings = pathname === '/config/bot' && new URLSearchParams(search).get('mode') === 'common'
  const { t } = useTranslation()
  const botUnavailable =
    runtime.host && !runtime.selected_bot_running && !commonSettings && !transferPage && !['/bots', '/manage', '/settings'].includes(pathname ?? '')
  const content = botUnavailable ? (
    <div className="mx-auto max-w-xl space-y-4 p-8">
      <h1 className="text-xl font-semibold">
        {t(runtime.selected_bot_id ? 'hostManager.selectedStopped' : 'hostManager.noSelection')}
      </h1>
      <p className="text-muted-foreground">{t('hostManager.selectToContinue')}</p>
      <Link to="/config/bot" search={{ mode: 'common' }} className="block underline">公共设置</Link>
      <Link
        className="bg-primary text-primary-foreground inline-flex rounded-md px-4 py-2"
        to="/bots"
      >
        {t('hostManager.title')}
      </Link>
    </div>
  ) : (
    children
  )

  if (version === 'observation') {
    return (
      <Suspense fallback={<RoutePendingFallback />}>
        <ObservationLayout>{content}</ObservationLayout>
      </Suspense>
    )
  }

  return <Layout>{content}</Layout>
}
