import type { ReactNode } from 'react'
import type { CallBackProps, Step } from 'react-joyride'

import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TourProvider } from '../tour-provider'
import { useTour } from '../use-tour'

// 与 tour-provider.tsx 内部使用的 localStorage 键保持一致
const COMPLETED_TOURS_KEY = 'maibot-completed-tours'

const demoSteps: Step[] = [
  { target: 'body', content: '第一步' },
  { target: '#demo-target', content: '第二步' },
]

// 构造 Joyride 回调数据，默认值可按需覆盖
function makeCallback(overrides: Partial<CallBackProps> = {}): CallBackProps {
  return {
    action: 'update',
    controlled: true,
    index: 0,
    lifecycle: 'complete',
    origin: null,
    size: demoSteps.length,
    status: 'running',
    step: demoSteps[0],
    type: 'step:before',
    ...overrides,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  return <TourProvider>{children}</TourProvider>
}

function setup() {
  return renderHook(() => useTour(), { wrapper })
}

// 注册并启动一个演示 Tour 的公共前置
function setupRunning(tourId = 'demo') {
  const view = setup()
  act(() => {
    view.result.current.registerTour(tourId, demoSteps)
  })
  act(() => {
    view.result.current.startTour(tourId)
  })
  return view
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
})

describe('TourProvider 状态机', () => {
  it('初始状态为未激活、第 0 步、未运行，且当前步骤为空', () => {
    const { result } = setup()
    expect(result.current.state).toEqual({ activeTourId: null, stepIndex: 0, isRunning: false })
    expect(result.current.getCurrentSteps()).toEqual([])
  })

  it('注册后启动 Tour 会进入运行态并返回注册的步骤', () => {
    const { result } = setupRunning()
    expect(result.current.state).toEqual({ activeTourId: 'demo', stepIndex: 0, isRunning: true })
    expect(result.current.getCurrentSteps()).toBe(demoSteps)
  })

  it('启动未注册的 Tour 不产生任何状态变化', () => {
    const { result } = setup()
    act(() => {
      result.current.startTour('ghost')
    })
    expect(result.current.state).toEqual({ activeTourId: null, stepIndex: 0, isRunning: false })
  })

  it('startTour 支持指定起始步骤索引', () => {
    const { result } = setup()
    act(() => {
      result.current.registerTour('demo', demoSteps)
    })
    act(() => {
      result.current.startTour('demo', 1)
    })
    expect(result.current.state.stepIndex).toBe(1)
    expect(result.current.state.isRunning).toBe(true)
  })

  it('nextStep 递增不封顶，prevStep 在 0 处夹紧，goToStep 直接跳转', () => {
    const { result } = setupRunning()
    act(() => {
      result.current.nextStep()
    })
    expect(result.current.state.stepIndex).toBe(1)

    // 特征化：nextStep 不校验步骤上限，可以越过最后一步
    act(() => {
      result.current.nextStep()
    })
    expect(result.current.state.stepIndex).toBe(2)

    act(() => {
      result.current.goToStep(0)
    })
    expect(result.current.state.stepIndex).toBe(0)

    act(() => {
      result.current.prevStep()
    })
    expect(result.current.state.stepIndex).toBe(0)
  })

  it('stopTour 仅停止运行，保留 activeTourId 与 stepIndex（特征化现状）', () => {
    const { result } = setupRunning()
    act(() => {
      result.current.goToStep(1)
    })
    act(() => {
      result.current.stopTour()
    })
    expect(result.current.state).toEqual({ activeTourId: 'demo', stepIndex: 1, isRunning: false })
  })

  it('注销正在运行的 Tour 会停止并复位状态', () => {
    const { result } = setupRunning()
    act(() => {
      result.current.unregisterTour('demo')
    })
    expect(result.current.state).toEqual({ activeTourId: null, stepIndex: 0, isRunning: false })
    expect(result.current.getCurrentSteps()).toEqual([])
  })

  it('注销未激活的 Tour 不影响正在运行的 Tour', () => {
    const { result } = setupRunning('demo')
    act(() => {
      result.current.registerTour('other', [{ target: 'body', content: '别的' }])
    })
    act(() => {
      result.current.unregisterTour('other')
    })
    expect(result.current.state).toEqual({ activeTourId: 'demo', stepIndex: 0, isRunning: true })
    expect(result.current.getCurrentSteps()).toBe(demoSteps)
  })
})

describe('TourProvider 完成状态持久化', () => {
  it('markTourCompleted 写入 localStorage，resetTourCompleted 再移除', () => {
    const { result } = setup()
    expect(result.current.isTourCompleted('demo')).toBe(false)

    act(() => {
      result.current.markTourCompleted('demo')
    })
    expect(result.current.isTourCompleted('demo')).toBe(true)
    expect(JSON.parse(localStorage.getItem(COMPLETED_TOURS_KEY) ?? '[]')).toEqual(['demo'])

    act(() => {
      result.current.resetTourCompleted('demo')
    })
    expect(result.current.isTourCompleted('demo')).toBe(false)
    expect(JSON.parse(localStorage.getItem(COMPLETED_TOURS_KEY) ?? '[]')).toEqual([])
  })

  it('挂载时读取 localStorage 中已完成的 Tour 列表', () => {
    localStorage.setItem(COMPLETED_TOURS_KEY, JSON.stringify(['model', 'chat']))
    const { result } = setup()
    expect(result.current.isTourCompleted('model')).toBe(true)
    expect(result.current.isTourCompleted('chat')).toBe(true)
    expect(result.current.isTourCompleted('other')).toBe(false)
  })

  it('localStorage 内容损坏时回退为空集合而不抛错', () => {
    localStorage.setItem(COMPLETED_TOURS_KEY, '{损坏的 JSON')
    const { result } = setup()
    expect(result.current.isTourCompleted('model')).toBe(false)
  })
})

describe('TourProvider Joyride 回调处理', () => {
  it('close 动作停止运行并复位步骤，但保留 activeTourId（特征化现状）', () => {
    const { result } = setupRunning()
    act(() => {
      result.current.goToStep(1)
    })
    act(() => {
      result.current.handleJoyrideCallback(makeCallback({ action: 'close', type: 'step:after', index: 1 }))
    })
    expect(result.current.state).toEqual({ activeTourId: 'demo', stepIndex: 0, isRunning: false })
  })

  it('finished 状态停止 Tour 并异步标记完成', async () => {
    const { result } = setupRunning()
    act(() => {
      result.current.handleJoyrideCallback(
        makeCallback({ action: 'next', status: 'finished', type: 'tour:end', index: 1 })
      )
    })
    expect(result.current.state.isRunning).toBe(false)
    expect(result.current.state.stepIndex).toBe(0)

    // markTourCompleted 通过 setTimeout(0) 延迟执行
    await waitFor(() => {
      expect(result.current.isTourCompleted('demo')).toBe(true)
    })
    expect(JSON.parse(localStorage.getItem(COMPLETED_TOURS_KEY) ?? '[]')).toEqual(['demo'])
  })

  it('skipped 状态停止 Tour 但不标记完成', async () => {
    const { result } = setupRunning()
    act(() => {
      result.current.handleJoyrideCallback(
        makeCallback({ action: 'skip', status: 'skipped', type: 'tour:end' })
      )
    })
    expect(result.current.state.isRunning).toBe(false)

    // 等待一个宏任务周期，确认没有异步补写完成状态
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
    })
    expect(result.current.isTourCompleted('demo')).toBe(false)
    expect(localStorage.getItem(COMPLETED_TOURS_KEY)).toBeNull()
  })

  it('step:after 且 action=next 时步骤索引前进到 index + 1', () => {
    const { result } = setupRunning()
    act(() => {
      result.current.handleJoyrideCallback(makeCallback({ action: 'next', type: 'step:after', index: 0 }))
    })
    expect(result.current.state.stepIndex).toBe(1)
    expect(result.current.state.isRunning).toBe(true)
  })

  it('step:after 且 action=prev 时步骤索引回退到 index - 1', () => {
    const { result } = setupRunning()
    act(() => {
      result.current.goToStep(1)
    })
    act(() => {
      result.current.handleJoyrideCallback(makeCallback({ action: 'prev', type: 'step:after', index: 1 }))
    })
    expect(result.current.state.stepIndex).toBe(0)
  })

  it('普通事件（如 step:before）不改变任何状态', () => {
    const { result } = setupRunning()
    act(() => {
      result.current.handleJoyrideCallback(makeCallback({ action: 'update', type: 'step:before', index: 0 }))
    })
    expect(result.current.state).toEqual({ activeTourId: 'demo', stepIndex: 0, isRunning: true })
  })
})
