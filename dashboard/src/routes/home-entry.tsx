import { lazy, Suspense } from 'react'

import { RoutePendingFallback } from '@/components/route-pending-fallback'

const ObservationHome = lazy(() =>
  import('./observation/ObservationHome').then((module) => ({ default: module.ObservationHome }))
)

/** 首页统一显示麦麦观察面板。 */
export function HomeEntry() {
  return (
    <Suspense fallback={<RoutePendingFallback />}>
      <ObservationHome />
    </Suspense>
  )
}
