import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'

import './index.css'
import './i18n'
import { ElectronShell } from './components/electron/electron-shell'
import { AnnouncerProvider } from './components/ui/announcer'
import { AssetStoreProvider } from './components/asset-provider'
import { AnimationProvider } from './components/animation-provider'
import { ThemeProvider } from './components/theme-provider'
import { TourProvider } from './components/tour/tour-provider'
import { LazyTourRenderer } from './components/tour/lazy-tour-renderer'
import { ErrorBoundary } from './components/error-boundary'
import { Toaster } from './components/ui/toaster'
import { isElectron } from './lib/runtime'
import { queryClient } from './lib/query'
import { router } from './router'

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
