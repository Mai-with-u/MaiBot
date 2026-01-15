import Joyride from 'react-joyride'
import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useTour } from './use-tour'

// Joyride 主题配置
const joyrideStyles = {
  options: {
    zIndex: 10000,
    primaryColor: 'hsl(var(--primary))',
    textColor: 'hsl(var(--foreground))',
    backgroundColor: 'hsl(var(--background))',
    arrowColor: 'hsl(var(--background))',
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
    backgroundColor: 'hsl(var(--primary))',
    color: 'hsl(var(--primary-foreground))',
    borderRadius: 'calc(var(--radius) - 2px)',
    fontSize: '0.875rem',
    padding: '0.5rem 1rem',
  },
  buttonBack: {
    color: 'hsl(var(--muted-foreground))',
    fontSize: '0.875rem',
    marginRight: '0.5rem',
  },
  buttonSkip: {
    color: 'hsl(var(--muted-foreground))',
    fontSize: '0.875rem',
  },
  buttonClose: {
    color: 'hsl(var(--muted-foreground))',
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
  const prevStepIndexRef = useRef(state.stepIndex)
  const cleanupRef = useRef<(() => void) | null>(null)

  // 当步骤变化时，重置 targetReady 以强制重新检测和定位
  useEffect(() => {
    if (prevStepIndexRef.current !== state.stepIndex) {
      setTargetReady(false)
      prevStepIndexRef.current = state.stepIndex
    }
  }, [state.stepIndex])

  // 等待当前步骤的目标元素出现
  useEffect(() => {
    if (!state.isRunning || steps.length === 0) {
      setTargetReady(false)
      return
    }

    const currentStep = steps[state.stepIndex]
    if (!currentStep) {
      setTargetReady(false)
      return
    }

    const target = currentStep.target
    if (target === 'body') {
      setTargetReady(true)
      return
    }

    // 重置状态
    setTargetReady(false)

    // 每次步骤变化时，先等待一段时间让 DOM 更新（弹窗关闭动画等）
    const initialDelay = setTimeout(() => {
      const checkTarget = () => {
        const element = document.querySelector(target as string)
        if (element) {
          // 确保元素可见
          const rect = element.getBoundingClientRect()
          const isVisible = rect.width > 0 && rect.height > 0
          if (isVisible) {
            return true
          }
        }
        return false
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
  
  useEffect(() => {
    // 创建或获取 tour 专用容器
    let container = document.getElementById('tour-portal-container') as HTMLDivElement | null
    if (!container) {
      container = document.createElement('div')
      container.id = 'tour-portal-container'
      container.style.cssText = 'position: fixed; top: 0; left: 0; z-index: 99999; pointer-events: none;'
      document.body.appendChild(container)
    }
    
    setPortalElement(container)
    
    return () => {
      // 组件卸载时不删除容器，因为可能还会再用
    }
  }, [])

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
      floaterProps={{
        styles: {
          floater: {
            zIndex: 99999,
          },
        },
        disableAnimation: true,
      }}
    />
  )

  // 使用 Portal 渲染到高层容器
  if (portalElement) {
    return createPortal(joyrideElement, portalElement)
  }

  return joyrideElement
}
