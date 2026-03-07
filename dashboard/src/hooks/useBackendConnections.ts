import { useCallback, useEffect, useState } from 'react'

import { isElectron } from '@/lib/runtime'
import type { BackendConnection } from '@/types/electron'

export function useBackendConnections() {
  const [backends, setBackends] = useState<BackendConnection[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!isElectron()) return
    const [list, active] = await Promise.all([
      window.electronAPI!.getBackends(),
      window.electronAPI!.getActiveBackend(),
    ])
    setBackends(list)
    setActiveId(active?.id ?? null)
    setLoading(false)
  }, [])

  useEffect(() => { 
    refresh() 
  }, [refresh])

  const addBackend = useCallback(async (conn: Omit<BackendConnection, 'id'>) => {
    if (!isElectron()) return
    await window.electronAPI!.addBackend(conn)
    await refresh()
  }, [refresh])

  const updateBackend = useCallback(async (id: string, patch: Partial<BackendConnection>) => {
    if (!isElectron()) return
    await window.electronAPI!.updateBackend(id, patch)
    await refresh()
  }, [refresh])

  const removeBackend = useCallback(async (id: string) => {
    if (!isElectron()) return
    await window.electronAPI!.removeBackend(id)
    await refresh()
  }, [refresh])

  const switchBackend = useCallback(async (id: string) => {
    if (!isElectron()) return
    await window.electronAPI!.setActiveBackend(id)
    setActiveId(id)
    // 重新加载页面以使用新后端
    window.location.reload()
  }, [])

  return { 
    backends, 
    activeId, 
    loading, 
    addBackend, 
    updateBackend, 
    removeBackend, 
    switchBackend, 
    refresh 
  }
}
