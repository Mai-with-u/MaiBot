import Joyride from 'react-joyride'
import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useTour } from './use-tour'

// Joyride 主题配置
const joyrideStyles = {
  options: {
    // 提到 portal 容器（99999）之上，确保 overlay/spotlight/tooltip 都在最上层；
    // overlay 的 z-index 由 react-joyride 内部基于 options.zIndex 推算，必须大于 floater 才能让 tooltip 按钮可点击。
    zIndex: 100000,
    primaryColor: 'hsl(var(--color-primary))',
    textColor: 'hsl(var(--color-foreground))',
    backgroundColor: 'hsl(var(--color-background))',
    arrowColor: 'hsl(var(--color-background))',
    overlayColor: 'rgba(0, 0, 0, 0.5)',
  },
  tooltip: {
    borderRadius: 'var(--radius)',
    padding: '1rem',
  },
  tooltipContainer: {
    textAlign: 'left' as const,
  },
  tooltipTitle: {
    fontSize: '1rem',
    fontWeight: 600,
    marginBottom: '0.5rem',
  },
  tooltipContent: {
    fontSize: '0.875rem',
    padding: '0.5rem 0',
  },
  buttonNext: {
    backgroundColor: 'hsl(var(--color-primary))',
    color: 'hsl(var(--color-primary-foreground))',
    borderRadius: 'calc(var(--radius) - 2px)',
    fontSize: '0.875rem',
    padding: '0.5rem 1rem',
  },
  buttonBack: {
    color: 'hsl(var(--color-muted-foreground))',
    fontSize: '0.875rem',
    marginRight: '0.5rem',
  },
  buttonSkip: {
    color: 'hsl(var(--color-muted-foreground))',
    fontSize: '0.875rem',
  },
  buttonClose: {
    color: 'hsl(var(--color-muted-foreground))',
  },
  spotlight: {
    borderRadius: 'var(--radius)',
  },
}

// 中文本地化
const locale = {
  back: '上一步',
  close: '关闭',
  last: '完成',
  next: '下一步',
  nextLabelWithProgress: '下一步 ({step}/{steps})',
  open: '打开对话框',
  skip: '跳过',
}

export function TourRenderer() {
  const { state, getCurrentSteps, handleJoyrideCallback } = useTour()
  const steps = getCurrentSteps()
  const [targetReady, setTargetReady] = useState(false)
  const [seenStepIndex, setSeenStepIndex] = useState(state.stepIndex)
  const cleanupRef = useRef<(() => void) | null>(null)

  if (seenStepIndex !== state.stepIndex) {
    setSeenStepIndex(state.stepIndex)
    if (targetReady) {
      setTargetReady(false)
    }
  }

  const currentStep = state.isRunning && steps.length > 0 ? steps[state.stepIndex] : undefined
  if (!state.isRunning || steps.length === 0 || !currentStep) {
    if (targetReady) {
      setTargetReady(false)
    }
  } else if (currentStep.target === 'body' && !targetReady) {
    setTargetReady(true)
  }

  // 等待当前步骤的目标元素出现
  useEffect(() => {
    if (!state.isRunning || steps.length === 0) {
      return
    }

    const step = steps[state.stepIndex]
    if (!step) {
      return
    }

    const target = step.target
    if (target === 'body') {
      return
    }

    queueMicrotask(() => {
      setTargetReady(false)
    })

    // 每次步骤变化时，先等待一段时间让 DOM 更新（弹窗关闭动画等）
    const initialDelay = setTimeout(() => {
      const checkTarget = () => {
        const elements = document.querySelectorAll(target as string)

        return Array.from(elements).some((element) => {
          const rect = element.getBoundingClientRect()
          return rect.width > 0 && rect.height > 0
        })
      }

      if (checkTarget()) {
        // 找到元素后再等一小段时间，确保动画完成
        setTimeout(() => setTargetReady(true), 100)
        return
      }

      // 使用轮询检测元素
      const intervalId = setInterval(() => {
        if (checkTarget()) {
          clearInterval(intervalId)
          // 找到元素后再等一小段时间
          setTimeout(() => setTargetReady(true), 100)
        }
      }, 100)

      const timeout = setTimeout(() => {
        clearInterval(intervalId)
        // 超时后设置 targetReady 为 true，让 Joyride 显示错误提示
        setTargetReady(true)
      }, 5000)

      // 保存清理函数
      const cleanup = () => {
        clearInterval(intervalId)
        clearTimeout(timeout)
      }
      
      // 将清理函数保存到 ref 中以便外部清理
      cleanupRef.current = cleanup
    }, 150) // 等待 150ms 让 DOM 更新和动画完成

    return () => {
      clearTimeout(initialDelay)
      if (cleanupRef.current) {
        cleanupRef.current()
        cleanupRef.current = null
      }
    }
  }, [state.isRunning, state.stepIndex, steps])

  // 创建一个高层级的容器用于渲染 Joyride
  const [portalElement, setPortalElement] = useState<HTMLElement | null>(null)
  if (typeof document !== 'undefined' && portalElement === null) {
    let container = document.getElementById('tour-portal-container') as HTMLDivElement | null
    if (!container) {
      container = document.createElement('div')
      container.id = 'tour-portal-container'
      container.style.cssText = 'position: fixed; top: 0; left: 0; z-index: 99999; pointer-events: none;'
      document.body.appendChild(container)
    }
    setPortalElement(container)
  }

  if (!state.isRunning || steps.length === 0 || !targetReady) {
    return null
  }

  const joyrideElement = (
    <Joyride
      key={`tour-step-${state.stepIndex}`}
      steps={steps}
      stepIndex={state.stepIndex}
      run={state.isRunning}
      continuous
      showSkipButton
      showProgress
      disableOverlayClose
      disableScrolling={false}
      disableScrollParentFix={false}
      callback={handleJoyrideCallback}
      styles={joyrideStyles}
      locale={locale}
      scrollOffset={80}
      scrollToFirstStep
    />
  )

  // 使用 Portal 渲染到高层容器
  if (portalElement) {
    return createPortal(joyrideElement, portalElement)
  }

  return joyrideElement
}
