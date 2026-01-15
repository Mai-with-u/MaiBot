import { useState, useCallback, type ReactNode } from 'react'
import type { Step, CallBackProps, Status } from 'react-joyride'
import { TourContext } from './tour-context'
import type { TourId, TourState } from './types'

const COMPLETED_TOURS_KEY = 'maibot-completed-tours'

// 从 localStorage 读取已完成的 Tours
function getCompletedTours(): Set<TourId> {
  try {
    const stored = localStorage.getItem(COMPLETED_TOURS_KEY)
    return stored ? new Set(JSON.parse(stored)) : new Set()
  } catch {
    return new Set()
  }
}

// 保存已完成的 Tours 到 localStorage
function saveCompletedTours(tours: Set<TourId>) {
  localStorage.setItem(COMPLETED_TOURS_KEY, JSON.stringify([...tours]))
}

export function TourProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<TourState>({
    activeTourId: null,
    stepIndex: 0,
    isRunning: false,
  })
  
  // 使用 useState 存储 tours（Map 对象是可变的，可以直接修改）
  const [tours] = useState<Map<TourId, Step[]>>(() => new Map())
  const [completedTours, setCompletedTours] = useState<Set<TourId>>(getCompletedTours)
  // 用于强制重新渲染的计数器
  const [, forceUpdate] = useState(0)

  const registerTour = useCallback((tourId: TourId, steps: Step[]) => {
    tours.set(tourId, steps)
    // 强制更新以确保 context 消费者能获取到最新数据
    forceUpdate(n => n + 1)
  }, [tours])

  const unregisterTour = useCallback((tourId: TourId) => {
    tours.delete(tourId)
    // 如果正在运行的 Tour 被注销，停止它
    setState(prev => {
      if (prev.activeTourId === tourId) {
        return { ...prev, activeTourId: null, isRunning: false, stepIndex: 0 }
      }
      return prev
    })
  }, [tours])

  const startTour = useCallback((tourId: TourId, startIndex = 0) => {
    if (tours.has(tourId)) {
      setState({
        activeTourId: tourId,
        stepIndex: startIndex,
        isRunning: true,
      })
    }
  }, [tours])

  const stopTour = useCallback(() => {
    setState(prev => ({
      ...prev,
      isRunning: false,
    }))
  }, [])

  const goToStep = useCallback((index: number) => {
    setState(prev => ({
      ...prev,
      stepIndex: index,
    }))
  }, [])

  const nextStep = useCallback(() => {
    setState(prev => ({
      ...prev,
      stepIndex: prev.stepIndex + 1,
    }))
  }, [])

  const prevStep = useCallback(() => {
    setState(prev => ({
      ...prev,
      stepIndex: Math.max(0, prev.stepIndex - 1),
    }))
  }, [])

  const getCurrentSteps = useCallback((): Step[] => {
    if (!state.activeTourId) return []
    return tours.get(state.activeTourId) || []
  }, [state.activeTourId, tours])

  const markTourCompleted = useCallback((tourId: TourId) => {
    setCompletedTours(prev => {
      const next = new Set(prev)
      next.add(tourId)
      saveCompletedTours(next)
      return next
    })
  }, [])

  const handleJoyrideCallback = useCallback((data: CallBackProps) => {
    const { action, index, status, type } = data
    const finishedStatuses: Status[] = ['finished', 'skipped']

    // 处理关闭按钮点击
    if (action === 'close') {
      setState(prev => ({
        ...prev,
        isRunning: false,
        stepIndex: 0,
      }))
      return
    }

    if (finishedStatuses.includes(status)) {
      // Tour 完成或跳过
      setState(prev => {
        if (status === 'finished' && prev.activeTourId) {
          // 使用 setTimeout 避免在 setState 中调用另一个 setState
          setTimeout(() => markTourCompleted(prev.activeTourId!), 0)
        }
        return {
          ...prev,
          isRunning: false,
          stepIndex: 0,
        }
      })
    } else if (type === 'step:after') {
      // 步骤切换后更新索引
      if (action === 'next') {
        setState(prev => ({ ...prev, stepIndex: index + 1 }))
      } else if (action === 'prev') {
        setState(prev => ({ ...prev, stepIndex: index - 1 }))
      }
    }
  }, [markTourCompleted])

  const isTourCompleted = useCallback((tourId: TourId): boolean => {
    return completedTours.has(tourId)
  }, [completedTours])

  const resetTourCompleted = useCallback((tourId: TourId) => {
    setCompletedTours(prev => {
      const next = new Set(prev)
      next.delete(tourId)
      saveCompletedTours(next)
      return next
    })
  }, [])

  return (
    <TourContext.Provider
      value={{
        state,
        tours,
        registerTour,
        unregisterTour,
        startTour,
        stopTour,
        goToStep,
        nextStep,
        prevStep,
        getCurrentSteps,
        handleJoyrideCallback,
        isTourCompleted,
        markTourCompleted,
        resetTourCompleted,
      }}
    >
      {children}
    </TourContext.Provider>
  )
}
