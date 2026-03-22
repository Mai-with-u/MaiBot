/**
 * 重启上下文
 */

import { createContext, useContext, useState, useCallback, ReactNode } from 'react'

export interface RestartStatus {
  isRestarting: boolean
  progress: number
  message: string
}

export interface RestartContextValue {
  status: RestartStatus
  startRestart: () => void
  endRestart: () => void
  updateProgress: (progress: number, message?: string) => void
}

const RestartContext = createContext<RestartContextValue | undefined>(undefined)

interface RestartProviderProps {
  children: ReactNode
}

export function RestartProvider({ children }: RestartProviderProps) {
  const [status, setStatus] = useState<RestartStatus>({
    isRestarting: false,
    progress: 0,
    message: '',
  })

  const startRestart = useCallback(() => {
    setStatus({
      isRestarting: true,
      progress: 0,
      message: '正在重启...',
    })
  }, [])

  const endRestart = useCallback(() => {
    setStatus({
      isRestarting: false,
      progress: 100,
      message: '重启完成',
    })
  }, [])

  const updateProgress = useCallback((progress: number, message?: string) => {
    setStatus((prev) => ({
      ...prev,
      progress,
      message: message || prev.message,
    }))
  }, [])

  return (
    <RestartContext.Provider
      value={{
        status,
        startRestart,
        endRestart,
        updateProgress,
      }}
    >
      {children}
    </RestartContext.Provider>
  )
}

export function useRestart(): RestartContextValue {
  const context = useContext(RestartContext)
  if (!context) {
    throw new Error('useRestart must be used within a RestartProvider')
  }
  return context
}
