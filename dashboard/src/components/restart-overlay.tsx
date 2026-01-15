/**
 * 重启遮罩层组件
 *
 * 用于显示重启进度和状态，阻止用户操作
 *
 * 使用方式 1: 配合 RestartProvider（推荐）
 *   <RestartProvider>
 *     <App />
 *     <RestartOverlay />
 *   </RestartProvider>
 *
 * 使用方式 2: 独立使用
 *   <RestartOverlay
 *     visible={true}
 *     onComplete={() => navigate('/auth')}
 *   />
 */

import { useEffect, useState, useCallback } from 'react'
import {
  Loader2,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  RotateCcw,
} from 'lucide-react'
import { Progress } from '@/components/ui/progress'
import { Button } from '@/components/ui/button'
import { useRestart, type RestartStatus, type RestartContextValue } from '@/lib/restart-context'
import { cn } from '@/lib/utils'

// Hook 用于安全获取 restart context
function useSafeRestart(): RestartContextValue | null {
  try {
    return useRestart()
  } catch {
    return null
  }
}

// ============ 类型定义 ============

interface RestartOverlayProps {
  /** 是否可见（仅独立模式使用） */
  visible?: boolean
  /** 重启完成回调 */
  onComplete?: () => void
  /** 重启失败回调 */
  onFailed?: () => void
  /** 自定义标题 */
  title?: string
  /** 自定义描述 */
  description?: string
  /** 是否显示背景动画 */
  showAnimation?: boolean
  /** 自定义类名 */
  className?: string
}

// ============ 状态配置 ============

interface StatusConfig {
  icon: React.ReactNode
  title: string
  description: string
  tip: string
}

const getStatusConfig = (
  status: RestartStatus,
  checkAttempts: number,
  maxAttempts: number,
  customTitle?: string,
  customDescription?: string
): StatusConfig => {
  const configs: Record<RestartStatus, StatusConfig> = {
    idle: {
      icon: null,
      title: '',
      description: '',
      tip: '',
    },
    requesting: {
      icon: <Loader2 className="h-16 w-16 text-primary animate-spin" />,
      title: customTitle ?? '准备重启',
      description: customDescription ?? '正在发送重启请求...',
      tip: '🔄 正在准备重启麦麦...',
    },
    restarting: {
      icon: <Loader2 className="h-16 w-16 text-primary animate-spin" />,
      title: customTitle ?? '正在重启麦麦',
      description: customDescription ?? '请稍候，麦麦正在重启中...',
      tip: '🔄 配置已保存，正在重启主程序...',
    },
    checking: {
      icon: <Loader2 className="h-16 w-16 text-primary animate-spin" />,
      title: '检查服务状态',
      description: `等待服务恢复... (${checkAttempts}/${maxAttempts})`,
      tip: '⏳ 正在等待服务恢复，请勿关闭页面...',
    },
    success: {
      icon: <CheckCircle2 className="h-16 w-16 text-green-500" />,
      title: '重启成功',
      description: '正在跳转到登录页面...',
      tip: '✅ 配置已生效，服务运行正常',
    },
    failed: {
      icon: <AlertCircle className="h-16 w-16 text-destructive" />,
      title: '重启超时',
      description: '服务未能在预期时间内恢复',
      tip: '⚠️ 如果长时间无响应，请尝试手动重启',
    },
  }
  return configs[status]
}

// ============ 主组件（配合 Provider） ============

export function RestartOverlay({
  visible,
  onComplete,
  onFailed,
  title,
  description,
  showAnimation = true,
  className,
}: RestartOverlayProps) {
  // 尝试使用 context（可能不存在）
  const contextValue = useSafeRestart()

  // 如果有 context，使用 context 状态；否则使用 props
  const isVisible = contextValue ? contextValue.isRestarting : visible

  if (!isVisible) return null

  if (contextValue) {
    return (
      <RestartOverlayContent
        state={contextValue.state}
        onRetry={contextValue.retryHealthCheck}
        onComplete={onComplete}
        onFailed={onFailed}
        title={title}
        description={description}
        showAnimation={showAnimation}
        className={className}
      />
    )
  }

  // 独立模式
  return (
    <StandaloneRestartOverlay
      onComplete={onComplete}
      onFailed={onFailed}
      title={title}
      description={description}
      showAnimation={showAnimation}
      className={className}
    />
  )
}

// ============ 内容组件 ============

interface RestartOverlayContentProps {
  state: {
    status: RestartStatus
    progress: number
    elapsedTime: number
    checkAttempts: number
    maxAttempts: number
    error?: string
  }
  onRetry: () => void
  onComplete?: () => void
  onFailed?: () => void
  title?: string
  description?: string
  showAnimation?: boolean
  className?: string
}

function RestartOverlayContent({
  state,
  onRetry,
  onComplete,
  onFailed,
  title,
  description,
  showAnimation,
  className,
}: RestartOverlayContentProps) {
  const { status, progress, elapsedTime, checkAttempts, maxAttempts } = state

  // 回调处理
  useEffect(() => {
    if (status === 'success' && onComplete) {
      onComplete()
    } else if (status === 'failed' && onFailed) {
      onFailed()
    }
  }, [status, onComplete, onFailed])

  const config = getStatusConfig(
    status,
    checkAttempts,
    maxAttempts,
    title,
    description
  )

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  return (
    <div
      className={cn(
        'fixed inset-0 bg-background/95 backdrop-blur-sm z-50 flex items-center justify-center',
        className
      )}
    >
      {/* 背景动画 */}
      {showAnimation && <BackgroundAnimation />}

      <div className="max-w-md w-full mx-4 space-y-8 relative z-10">
        {/* 图标和状态 */}
        <div className="flex flex-col items-center space-y-4">
          <div className="relative">
            {config.icon}
            {/* 脉冲动画 */}
            {(status === 'restarting' || status === 'checking') && (
              <div className="absolute inset-0 rounded-full bg-primary/20 animate-ping" />
            )}
          </div>
          <h2 className="text-2xl font-bold">{config.title}</h2>
          <p className="text-muted-foreground text-center">{config.description}</p>
        </div>

        {/* 进度条 */}
        {status !== 'failed' && status !== 'idle' && (
          <div className="space-y-2">
            <Progress value={progress} className="h-2" />
            <div className="flex justify-between text-sm text-muted-foreground">
              <span>{progress}%</span>
              <span>已用时: {formatTime(elapsedTime)}</span>
            </div>
          </div>
        )}

        {/* 提示信息 */}
        <div className="bg-muted/50 rounded-lg p-4">
          <p className="text-sm text-muted-foreground">{config.tip}</p>
        </div>

        {/* 失败时的操作按钮 */}
        {status === 'failed' && (
          <div className="flex gap-2">
            <Button
              onClick={() => window.location.reload()}
              variant="default"
              className="flex-1"
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              刷新页面
            </Button>
            <Button onClick={onRetry} variant="secondary" className="flex-1">
              <RotateCcw className="mr-2 h-4 w-4" />
              重试检测
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

// ============ 独立模式组件 ============

interface StandaloneRestartOverlayProps {
  onComplete?: () => void
  onFailed?: () => void
  title?: string
  description?: string
  showAnimation?: boolean
  className?: string
}

function StandaloneRestartOverlay({
  onComplete,
  onFailed,
  title,
  description,
  showAnimation,
  className,
}: StandaloneRestartOverlayProps) {
  const [state, setState] = useState({
    status: 'restarting' as RestartStatus,
    progress: 0,
    elapsedTime: 0,
    checkAttempts: 0,
    maxAttempts: 60,
  })

  const startHealthCheck = useCallback(() => {
    let attempts = 0
    const maxAttempts = 60

    const check = async () => {
      attempts++
      setState((prev) => ({
        ...prev,
        status: 'checking',
        checkAttempts: attempts,
      }))

      try {
        const response = await fetch('/api/webui/system/status', {
          method: 'GET',
          signal: AbortSignal.timeout(3000),
        })

        if (response.ok) {
          setState((prev) => ({ ...prev, status: 'success', progress: 100 }))
          setTimeout(() => {
            onComplete?.()
            window.location.href = '/auth'
          }, 1500)
          return
        }
      } catch {
        // 继续重试
      }

      if (attempts >= maxAttempts) {
        setState((prev) => ({ ...prev, status: 'failed' }))
        onFailed?.()
      } else {
        setTimeout(check, 2000)
      }
    }

    check()
  }, [onComplete, onFailed])

  useEffect(() => {
    // 进度条动画
    const progressInterval = setInterval(() => {
      setState((prev) => ({
        ...prev,
        progress: prev.progress >= 90 ? prev.progress : prev.progress + 1,
      }))
    }, 200)

    // 计时器
    const timerInterval = setInterval(() => {
      setState((prev) => ({ ...prev, elapsedTime: prev.elapsedTime + 1 }))
    }, 1000)

    // 3秒后开始健康检查
    const initialDelay = setTimeout(() => {
      startHealthCheck()
    }, 3000)

    return () => {
      clearInterval(progressInterval)
      clearInterval(timerInterval)
      clearTimeout(initialDelay)
    }
  }, [startHealthCheck])

  return (
    <RestartOverlayContent
      state={state}
      onRetry={startHealthCheck}
      onComplete={onComplete}
      onFailed={onFailed}
      title={title}
      description={description}
      showAnimation={showAnimation}
      className={className}
    />
  )
}

// ============ 背景动画 ============

function BackgroundAnimation() {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {/* 渐变圆环 */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px]">
        <div className="absolute inset-0 rounded-full border border-primary/10 animate-[ping_3s_ease-in-out_infinite]" />
        <div className="absolute inset-8 rounded-full border border-primary/10 animate-[ping_3s_ease-in-out_infinite_0.5s]" />
        <div className="absolute inset-16 rounded-full border border-primary/10 animate-[ping_3s_ease-in-out_infinite_1s]" />
      </div>

      {/* 浮动粒子 */}
      <div className="absolute top-1/4 left-1/4 w-2 h-2 bg-primary/20 rounded-full animate-bounce" />
      <div className="absolute top-3/4 right-1/4 w-3 h-3 bg-primary/15 rounded-full animate-bounce delay-150" />
      <div className="absolute top-1/2 right-1/3 w-2 h-2 bg-primary/20 rounded-full animate-bounce delay-300" />
    </div>
  )
}

// ============ 导出旧组件（兼容性） ============

// 如需使用旧版组件，请直接导入:
// import { RestartingOverlay } from '@/components/RestartingOverlay.legacy'
