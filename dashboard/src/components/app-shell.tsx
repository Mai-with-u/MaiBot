import { lazy, Suspense, type ReactNode } from 'react'

import { Layout } from '@/components/layout'
import { BotUnavailable } from '@/components/bot-unavailable'
import { RoutePendingFallback } from '@/components/route-pending-fallback'
import { useEffectiveLayoutVersion } from '@/lib/layout-version'
import { isBotIndependentPath, useHostRuntime } from '@/host/state'
import { useRouterState } from '@tanstack/react-router'

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
  const botUnavailable =
    runtime.host && !runtime.selected_bot_running && !commonSettings && !transferPage && !isBotIndependentPath(pathname)
  const content = botUnavailable ? <BotUnavailable /> : children

  if (version === 'observation') {
    return (
      <Suspense fallback={<RoutePendingFallback />}>
        <ObservationLayout>{content}</ObservationLayout>
      </Suspense>
    )
  }

  return <Layout>{content}</Layout>
}
