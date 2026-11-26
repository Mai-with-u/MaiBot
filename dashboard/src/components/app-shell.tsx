import { lazy, Suspense, type ReactNode } from 'react'
import { useRouterState } from '@tanstack/react-router'

import { BotUnavailable } from '@/components/bot-unavailable'
import { RoutePendingFallback } from '@/components/route-pending-fallback'
import { isBotIndependentPath, useHostRuntime } from '@/host/state'

const ObservationLayout = lazy(() =>
  import('@/components/observation-layout/ObservationLayout').then((module) => ({
    default: module.ObservationLayout,
  }))
)

/**
 * 受保护页面的统一外壳入口，检查当前 Bot 是否可访问目标页面。
 */
export function AppShell({ children }: { children: ReactNode }) {
  const runtime = useHostRuntime()
  // Outlet 渲染的是 matches；导航期间 location 已经是目标地址，旧页面仍可能挂载。
  // 必须依据实际匹配的页面放行，避免返回管理页时短暂启动旧 Bot 页的请求。
  const pathname = useRouterState({ select: (state) => state.matches.at(-1)?.pathname })
  const search = useRouterState({ select: state => state.location.searchStr })
  const commonSettings = pathname === '/config/bot' && new URLSearchParams(search).get('mode') === 'common'
  const botUnavailable =
    runtime.host && !runtime.selected_bot_running && !commonSettings && !isBotIndependentPath(pathname)
  const content = botUnavailable ? <BotUnavailable /> : children

  return (
    <Suspense fallback={<RoutePendingFallback />}>
      <ObservationLayout>{content}</ObservationLayout>
    </Suspense>
  )
}
