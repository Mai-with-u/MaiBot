import Joyride from 'react-joyride'
import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
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
    maxWidth: 'calc(100vw - 2rem)',
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

export function TourRenderer() {
  const { t } = useTranslation()
  const { state, getCurrentSteps, handleJoyrideCallback } = useTour()
  const locale = {
    back: t('tour.controls.back'),
    close: t('tour.controls.close'),
    last: t('tour.controls.last'),
    next: t('tour.controls.next'),
    // {step}/{steps} 是 Joyride 自己的单花括号占位符，i18next 不会替换。
    nextLabelWithProgress: t('tour.controls.nextWithProgress'),
    open: t('tour.controls.open'),
    skip: t('tour.controls.skip'),
  }
  const steps = getCurrentSteps()
  const [targetReady, setTargetReady] = useState(false)
  const [seenStepIndex, setSeenStepIndex] = useState(state.stepIndex)
  const cleanupRef = useRef<(() => void) | null>(null)
  const [dialogSpotlight, setDialogSpotlight] = useState<{
    top: number
    left: number
    width: number
    height: number
    position: 'fixed'
  } | null>(null)

  if (seenStepIndex !== state.stepIndex) {
    setSeenStepIndex(state.stepIndex)
    if (targetReady) {
      setTargetReady(false)
    }
  }

  const currentStep = state.isRunning && steps.length > 0 ? steps[state.stepIndex] : undefined
  const currentTarget = typeof currentStep?.target === 'string'
    ? document.querySelector<HTMLElement>(currentStep.target)
    : currentStep?.target
  const isDialogTarget = !!currentTarget?.closest('[data-dashboard-dialog-content]')
  if (!state.isRunning || steps.length === 0 || !currentStep) {
    if (targetReady) {
      setTargetReady(false)
    }
  } else if (currentStep.target === 'body' && !targetReady) {
    setTargetReady(true)
  }

  // Joyride 对固定弹窗内的滚动目标使用 offsetTop，会丢失弹窗在视口中的偏移。
  // 用实际视口坐标覆盖高亮，并在滚动、缩放和字段尺寸变化后同步更新。
  useLayoutEffect(() => {
    setDialogSpotlight(null)
    if (!targetReady || !currentStep) return
    const target = typeof currentStep.target === 'string'
      ? document.querySelector<HTMLElement>(currentStep.target)
      : currentStep.target
    if (!target?.closest('[data-dashboard-dialog-content]')) return

    const padding = currentStep.spotlightPadding ?? 10
    const updateSpotlight = () => {
      const rect = target.getBoundingClientRect()
      setDialogSpotlight({
        top: rect.top - padding,
        left: rect.left - padding,
        width: rect.width + padding * 2,
        height: rect.height + padding * 2,
        position: 'fixed',
      })
    }
    updateSpotlight()
    const observer = new ResizeObserver(updateSpotlight)
    observer.observe(target)
    window.addEventListener('resize', updateSpotlight)
    document.addEventListener('scroll', updateSpotlight, true)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateSpotlight)
      document.removeEventListener('scroll', updateSpotlight, true)
    }
  }, [currentStep, targetReady])

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
          if (rect.width <= 0 || rect.height <= 0) return false
          // 先滚动弹窗内的字段，再让 Joyride 测量，避免它使用错误的滚动偏移。
          if (element.closest('[data-dashboard-dialog-content]')) {
            element.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' })
          }
          return true
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
      disableScrolling={isDialogTarget}
      disableScrollParentFix
      callback={handleJoyrideCallback}
      styles={{
        ...joyrideStyles,
        spotlight: { ...joyrideStyles.spotlight, ...dialogSpotlight },
      }}
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
