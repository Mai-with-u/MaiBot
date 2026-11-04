import { lazy, Suspense } from 'react'

import { RoutePendingFallback } from '@/components/route-pending-fallback'
import { useEffectiveLayoutVersion } from '@/lib/layout-version'

const ClassicHome = lazy(() => import('./index').then((module) => ({ default: module.IndexPage })))
const ObservationHome = lazy(() =>
  import('./observation/ObservationHome').then((module) => ({ default: module.ObservationHome }))
)

/** 首页按布局版本选择：经典布局用卡片首页，新布局用麦麦观察面板。 */
export function HomeEntry() {
  const version = useEffectiveLayoutVersion()
  return (
    <Suspense fallback={<RoutePendingFallback />}>
      {version === 'observation' ? <ObservationHome /> : <ClassicHome />}
    </Suspense>
  )
}
