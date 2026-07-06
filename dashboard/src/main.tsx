import { lazy, StrictMode, Suspense, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'

import './index.css'
import './i18n'
import { AnnouncerProvider } from './components/ui/announcer'
import { AssetStoreProvider } from './components/asset-provider'
import { AnimationProvider } from './components/animation-provider'
import { ThemeProvider } from './components/theme-provider'
import { TourProvider } from './components/tour/tour-provider'
import { useTour } from './components/tour/use-tour'
import { ErrorBoundary } from './components/error-boundary'
import { BackendSetupWizard } from './components/electron/BackendSetupWizard'
import { Toaster } from './components/ui/toaster'
import { isElectron } from './lib/runtime'
import { queryClient } from './lib/query'
import { router } from './router'

const TourRenderer = lazy(() =>
  import('./components/tour/tour-renderer').then((module) => ({
    default: module.TourRenderer,
  }))
)

function ElectronShell() {
  const [isFirstLaunch, setIsFirstLaunch] = useState(false)

  useEffect(() => {
    window.electronAPI!.isFirstLaunch().then(setIsFirstLaunch)
  }, [])

  return <BackendSetupWizard open={isFirstLaunch} />
}

function LazyTourRenderer() {
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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
      <AnnouncerProvider>
        <AssetStoreProvider>
          <ThemeProvider defaultTheme="system">
            <AnimationProvider>
              <TourProvider>
                {isElectron() && <ElectronShell />}
                <RouterProvider router={router} />
                <LazyTourRenderer />
                <Toaster />
              </TourProvider>
            </AnimationProvider>
          </ThemeProvider>
        </AssetStoreProvider>
      </AnnouncerProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>
)
