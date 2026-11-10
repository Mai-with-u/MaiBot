import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'

import './index.css'
import './styles/controls.css'
import './styles/millennium.css'
import './styles/modern.css'
import './styles/mobile.css'
import './styles/surfaces.css'
import './styles/data-browser.css'
import './styles/workspace.css'
import './styles/management.css'
import './styles/conversation.css'
import './styles/desktop-embedded.css'
import './i18n'
import { ElectronShell } from './components/electron/electron-shell'
import { AnnouncerProvider } from './components/ui/announcer'
import { AssetStoreProvider } from './components/asset-provider'
import { AnimationProvider } from './components/animation-provider'
import { ThemeProvider } from './components/theme-provider'
import { TourProvider } from './components/tour/tour-provider'
import { LazyTourRenderer } from './components/tour/lazy-tour-renderer'
import { BootErrorScreen } from './components/boot-error-screen'
import { ErrorBoundary } from './components/error-boundary'
import { installMillenniumIconStyle } from './components/ui/millennium-icon-set'
import { Toaster } from './components/ui/toaster'
import { applyDesktopEmbedding } from './lib/desktop-host'
import { isElectron } from './lib/runtime'
import { queryClient } from './lib/query'
import { router } from './router'
import { bootstrapHostRuntime } from './host/api'
import { HostRuntimeObserver, HostVersionCompatibility } from './host/HostRuntimeObserver'

installMillenniumIconStyle()
applyDesktopEmbedding()

const root = createRoot(document.getElementById('root')!)
void bootstrapHostRuntime()
  .then(() =>
    root.render(
      <StrictMode>
        <ErrorBoundary>
          <QueryClientProvider client={queryClient}>
            <AnnouncerProvider>
              <AssetStoreProvider>
                <ThemeProvider defaultTheme="system">
                  <HostRuntimeObserver />
                  <HostVersionCompatibility />
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
  )
  .catch((error: unknown) => {
    root.render(
      <BootErrorScreen message={error instanceof Error ? error.message : String(error)} />
    )
  })
