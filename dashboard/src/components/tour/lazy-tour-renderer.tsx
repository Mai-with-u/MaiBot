import { lazy, Suspense } from 'react'

import { useTour } from './use-tour'

const TourRenderer = lazy(() =>
  import('./tour-renderer').then((module) => ({
    default: module.TourRenderer,
  }))
)

export function LazyTourRenderer() {
  const { state } = useTour()

  if (!state.isRunning) {
    return null
  }

  return (
    <Suspense fallback={null}>
      <TourRenderer />
    </Suspense>
  )
}
