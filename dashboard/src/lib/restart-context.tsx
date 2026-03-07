/**
 * 重启管理 Context
 *
 * 提供全局的重启状态管理和触发能力
 * 使用方式：
 *   const { triggerRestart, isRestarting } = useRestart()
 *   triggerRestart() // 触发重启
 */

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from 'react'
import { restartMaiBot } from './system-api'

// ============ 类型定义 ============

export type RestartStatus =
  | 'idle'
  | 'requesting'
  | 'restarting'
  | 'checking'
  | 'success'
  | 'failed'

export interface RestartState {
  status: RestartStatus
  progress: number
  elapsedTime: number
  checkAttempts: number
  maxAttempts: number
  error?: string
}

export interface RestartContextValue {
  /** 当前重启状态 */
  state: RestartState
  /** 是否正在重启中（任何非 idle 状态） */
  isRestarting: boolean
  /** 触发重启 */
  triggerRestart: (options?: TriggerRestartOptions) => Promise<void>
  /** 重置状态（用于失败后重试） */
  resetState: () => void
  /** 手动开始健康检查（用于重试） */
  retryHealthCheck: () => void
}

export interface TriggerRestartOptions {
  /** 重启前延迟（毫秒），用于显示提示 */
  delay?: number
  /** 自定义重启消息 */
  message?: string
  /** 跳过 API 调用（用于后端已触发重启的情况） */
  skipApiCall?: boolean
}

// ============ 配置常量 ============

const CONFIG = {
  /** 初始等待时间（毫秒），给后端重启时间 */
  INITIAL_DELAY: 3000,
  /** 健康检查间隔（毫秒） */
  CHECK_INTERVAL: 2000,
  /** 健康检查超时（毫秒） */
  CHECK_TIMEOUT: 3000,
  /** 最大检查次数 */
  MAX_ATTEMPTS: 60,
  /** 进度条更新间隔（毫秒） */
  PROGRESS_INTERVAL: 200,
  /** 成功后跳转延迟（毫秒） */
  SUCCESS_REDIRECT_DELAY: 1500,
} as const

// ============ Context ============

const RestartContext = createContext<RestartContextValue | null>(null)

// ============ Provider ============

interface RestartProviderProps {
  children: ReactNode
  /** 重启成功后的回调 */
  onRestartComplete?: () => void
  /** 重启失败后的回调 */
  onRestartFailed?: (error: string) => void
  /** 自定义健康检查 URL */
  healthCheckUrl?: string
  /** 自定义最大尝试次数 */
  maxAttempts?: number
}

export function RestartProvider({
  children,
  onRestartComplete,
  onRestartFailed,
  healthCheckUrl = '/api/webui/system/status',
  maxAttempts = CONFIG.MAX_ATTEMPTS,
}: RestartProviderProps) {
  const [state, setState] = useState<RestartState>({
    status: 'idle',
    progress: 0,
    elapsedTime: 0,
    checkAttempts: 0,
    maxAttempts,
  })

  // 使用 useRef 存储定时器引用，避免闭包陷阱
  const timersRef = useRef<{
    progress?: ReturnType<typeof setInterval>
    elapsed?: ReturnType<typeof setInterval>
    check?: ReturnType<typeof setTimeout>
  }>({})

  // 清理所有定时器
  const clearAllTimers = useCallback(() => {
    const timers = timersRef.current
    if (timers.progress) {
      clearInterval(timers.progress)
      timers.progress = undefined
    }
    if (timers.elapsed) {
      clearInterval(timers.elapsed)
      timers.elapsed = undefined
    }
    if (timers.check) {
      clearTimeout(timers.check)
      timers.check = undefined
    }
  }, [])

  // 重置状态
  const resetState = useCallback(() => {
    clearAllTimers()
    setState({
      status: 'idle',
      progress: 0,
      elapsedTime: 0,
      checkAttempts: 0,
      maxAttempts,
    })
  }, [clearAllTimers, maxAttempts])

  // 健康检查
  const checkHealth = useCallback(
    async (): Promise<boolean> => {
      try {
        const controller = new AbortController()
        const timeoutId = setTimeout(
          () => controller.abort(),
          CONFIG.CHECK_TIMEOUT
        )

        const response = await fetch(healthCheckUrl, {
          method: 'GET',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          signal: controller.signal,
        })

        clearTimeout(timeoutId)
        return response.ok
      } catch {
        // 网络错误、超时等都视为服务不可用，这是正常的
        return false
      }
    },
    [healthCheckUrl]
  )

  // 开始健康检查循环
  const startHealthCheck = useCallback(() => {
    let currentAttempt = 0

    const doCheck = async () => {
      currentAttempt++
      setState((prev) => ({
        ...prev,
        status: 'checking',
        checkAttempts: currentAttempt,
      }))

      const isHealthy = await checkHealth()

      if (isHealthy) {
        // 成功
        clearAllTimers()
        setState((prev) => ({
          ...prev,
          status: 'success',
          progress: 100,
        }))

        // 延迟后跳转
        setTimeout(() => {
          onRestartComplete?.()
          // 默认跳转到 auth 页面
          window.location.href = '/auth'
        }, CONFIG.SUCCESS_REDIRECT_DELAY)
      } else if (currentAttempt >= maxAttempts) {
        // 失败
        clearAllTimers()
        const error = `健康检查超时 (${currentAttempt}/${maxAttempts})`
        setState((prev) => ({
          ...prev,
          status: 'failed',
          error,
        }))
        onRestartFailed?.(error)
      } else {
        // 继续检查
        const checkTimer = setTimeout(doCheck, CONFIG.CHECK_INTERVAL)
        timersRef.current.check = checkTimer
      }
    }

    doCheck()
  }, [checkHealth, clearAllTimers, maxAttempts, onRestartComplete, onRestartFailed])

  // 重试健康检查
  const retryHealthCheck = useCallback(() => {
    setState((prev) => ({
      ...prev,
      status: 'checking',
      checkAttempts: 0,
      error: undefined,
    }))
    startHealthCheck()
  }, [startHealthCheck])

  // 触发重启
  const triggerRestart = useCallback(
    async (options?: TriggerRestartOptions) => {
      const { delay = 0, skipApiCall = false } = options ?? {}

      // 已经在重启中，忽略
      if (state.status !== 'idle' && state.status !== 'failed') {
        return
      }

      // 重置状态
      clearAllTimers()
      setState({
        status: 'requesting',
        progress: 0,
        elapsedTime: 0,
        checkAttempts: 0,
        maxAttempts,
      })

      // 可选延迟
      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay))
      }

      // 调用重启 API
      if (!skipApiCall) {
        try {
          setState((prev) => ({ ...prev, status: 'restarting' }))
          // 重启 API 可能不返回响应（服务立即关闭）
          await Promise.race([
            restartMaiBot(),
            // 5秒超时，超时也视为成功（服务已关闭）
            new Promise((resolve) => setTimeout(resolve, 5000)),
          ])
        } catch {
          // API 调用失败也是正常的（服务已关闭）
          // 继续进行健康检查
        }
      } else {
        setState((prev) => ({ ...prev, status: 'restarting' }))
      }

      // 启动进度条动画
      const progressTimer = setInterval(() => {
        setState((prev) => ({
          ...prev,
          progress: prev.progress >= 90 ? prev.progress : prev.progress + 1,
        }))
      }, CONFIG.PROGRESS_INTERVAL)

      // 启动计时器
      const elapsedTimer = setInterval(() => {
        setState((prev) => ({
          ...prev,
          elapsedTime: prev.elapsedTime + 1,
        }))
      }, 1000)

      timersRef.current.progress = progressTimer
      timersRef.current.elapsed = elapsedTimer

      // 延迟后开始健康检查
      setTimeout(() => {
        startHealthCheck()
      }, CONFIG.INITIAL_DELAY)
    },
    [state.status, clearAllTimers, maxAttempts, startHealthCheck]
  )

  const contextValue: RestartContextValue = {
    state,
    isRestarting: state.status !== 'idle',
    triggerRestart,
    resetState,
    retryHealthCheck,
  }

  return (
    <RestartContext value={contextValue}>
      {children}
    </RestartContext>
  )
}

// ============ Hook ============

export function useRestart(): RestartContextValue {
  const context = useContext(RestartContext)
  if (!context) {
    throw new Error('useRestart must be used within a RestartProvider')
  }
  return context
}

// ============ 便捷 Hook（无需 Provider） ============

/**
 * 独立的重启 Hook，不依赖 Provider
 * 适用于只需要触发重启，不需要全局状态的场景
 */
export function useRestartAction() {
  const [isRestarting, setIsRestarting] = useState(false)

  const triggerRestart = useCallback(async () => {
    if (isRestarting) return

    setIsRestarting(true)
    try {
      await restartMaiBot()
    } catch {
      // 忽略错误，服务可能已关闭
    }
  }, [isRestarting])

  return { isRestarting, triggerRestart }
}
