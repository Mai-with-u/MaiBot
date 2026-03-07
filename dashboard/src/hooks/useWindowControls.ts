import { useCallback, useEffect, useState } from 'react'

import { isElectron } from '@/lib/runtime'

export function useWindowControls() {
  const [isMaximized, setIsMaximized] = useState(false)

  useEffect(() => {
    if (!isElectron()) return
    
    const api = window.electronAPI
    if (!api) return

    api.isMaximized().then(setIsMaximized)
    
    const unsubMax = api.onWindowMaximized(() => setIsMaximized(true))
    const unsubUnmax = api.onWindowUnmaximized(() => setIsMaximized(false))
    
    return () => {
      unsubMax?.()
      unsubUnmax?.()
    }
  }, [])

  const minimize = useCallback(() => window.electronAPI?.minimizeWindow(), [])
  const toggleMaximize = useCallback(() => window.electronAPI?.maximizeWindow(), [])
  const close = useCallback(() => window.electronAPI?.closeWindow(), [])

  return { close, isMaximized, minimize, toggleMaximize }
}