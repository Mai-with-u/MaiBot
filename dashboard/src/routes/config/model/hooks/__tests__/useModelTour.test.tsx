import type { ReactNode } from 'react'

import { cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { TourContextType, TourState } from '@/components/tour/types'

import { TourContext } from '@/components/tour/tour-context'
import {
  MODEL_ASSIGNMENT_TOUR_ID,
  modelAssignmentTourSteps,
} from '@/components/tour/tours/model-assignment-tour'
import { useModelTour } from '../useModelTour'

// 路由导航替换为可断言的桩函数
const navigateMock = vi.hoisted(() => vi.fn(() => Promise.resolve()))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

/** 构造一份可控的 Tour 上下文桩 */
function makeContextFns() {
  return {
    registerTour: vi.fn(),
    unregisterTour: vi.fn(),
    startTour: vi.fn(),
    stopTour: vi.fn(),
    goToStep: vi.fn(),
    nextStep: vi.fn(),
    prevStep: vi.fn(),
    getCurrentSteps: vi.fn(() => []),
    handleJoyrideCallback: vi.fn(),
    isTourCompleted: vi.fn(() => false),
    markTourCompleted: vi.fn(),
    resetTourCompleted: vi.fn(),
  }
}

/** 构造模型引导对话框/标签页回调集合 */
function makeCallbacks() {
  return {
    onOpenEditDialog: vi.fn(),
    onCloseEditDialog: vi.fn(),
    onOpenProviderDialog: vi.fn(),
    onCloseProviderDialog: vi.fn(),
    onOpenProvidersTab: vi.fn(),
    onOpenModelsTab: vi.fn(),
    onOpenTasksTab: vi.fn(),
  }
}

type Callbacks = ReturnType<typeof makeCallbacks>

/** 渲染 Hook 并返回可动态更新 Tour 状态的控制器 */
function setup(initialState: TourState, callbacks: Callbacks = makeCallbacks()) {
  const fns = makeContextFns()
  let state = initialState

  const wrapper = ({ children }: { children: ReactNode }) => {
    const value: TourContextType = {
      ...fns,
      state,
      tours: new Map(),
    }
    return <TourContext.Provider value={value}>{children}</TourContext.Provider>
  }

  const view = renderHook(() => useModelTour(callbacks), { wrapper })

  return {
    ...view,
    fns,
    callbacks,
    /** 更新 Tour 状态并重渲染 */
    setState(next: Partial<TourState>) {
      state = { ...state, ...next }
      view.rerender()
    },
  }
}

/** 运行中的模型引导状态 */
function runningState(stepIndex: number): TourState {
  return { activeTourId: MODEL_ASSIGNMENT_TOUR_ID, stepIndex, isRunning: true }
}

/** 未运行的空闲状态 */
const idleState: TourState = { activeTourId: null, stepIndex: 0, isRunning: false }

/** 在 body 中放置带 data-tour 标记的元素 */
function addTourElement(tour: string): HTMLElement {
  const element = document.createElement('button')
  element.setAttribute('data-tour', tour)
  document.body.appendChild(element)
  return element
}

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
  navigateMock.mockClear()
  vi.useRealTimers()
})

describe('useModelTour', () => {
  it('挂载时注册模型分配引导步骤', () => {
    const { fns } = setup(idleState)
    expect(fns.registerTour).toHaveBeenCalledWith(
      MODEL_ASSIGNMENT_TOUR_ID,
      modelAssignmentTourSteps
    )
  })

  it('startTour 先切到厂商标签页再启动引导', () => {
    const { result, fns, callbacks } = setup(idleState)
    result.current.startTour()
    expect(callbacks.onOpenProvidersTab).toHaveBeenCalledTimes(1)
    expect(fns.startTour).toHaveBeenCalledWith(MODEL_ASSIGNMENT_TOUR_ID)
  })

  it('isRunning 仅在当前引导为模型分配引导时为 true', () => {
    const running = setup(runningState(5))
    expect(running.result.current.isRunning).toBe(true)
    expect(running.result.current.stepIndex).toBe(5)

    const other = setup({ activeTourId: 'other-tour', stepIndex: 2, isRunning: true })
    expect(other.result.current.isRunning).toBe(false)
  })

  it('引导运行且不在模型配置页时触发路由跳转', () => {
    // jsdom 默认 pathname 为 '/'，与步骤目标路由 /config/model 不同
    setup(runningState(0))
    expect(navigateMock).toHaveBeenCalledWith({ to: '/config/model' })
  })

  it('引导未运行时不触发路由跳转', () => {
    setup(idleState)
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('步骤推进时按区间联动对话框与标签页', () => {
    const view = setup(runningState(0))
    const cb = view.callbacks

    // 初始步骤 0：进入厂商标签页
    expect(cb.onOpenProvidersTab).toHaveBeenCalledTimes(1)

    // 2 -> 3：打开提供商对话框
    view.setState({ stepIndex: 3 })
    expect(cb.onOpenProviderDialog).toHaveBeenCalledTimes(1)

    // 3 -> 10：关闭提供商对话框并切到模型标签页
    view.setState({ stepIndex: 10 })
    expect(cb.onCloseProviderDialog).toHaveBeenCalledTimes(1)
    expect(cb.onOpenModelsTab).toHaveBeenCalledTimes(1)

    // 10 -> 12：打开模型编辑对话框
    view.setState({ stepIndex: 12 })
    expect(cb.onOpenEditDialog).toHaveBeenCalledTimes(1)

    // 12 -> 19：切到模型分配标签页
    view.setState({ stepIndex: 19 })
    expect(cb.onOpenTasksTab).toHaveBeenCalledTimes(1)
    // 编辑对话框不应被关闭（19 不小于 12）
    expect(cb.onCloseEditDialog).not.toHaveBeenCalled()
  })

  it('步骤回退到编辑对话框区间之前时关闭编辑对话框', () => {
    const view = setup(runningState(12))
    const cb = view.callbacks

    // 12 -> 2：关闭编辑对话框并回到厂商标签页
    view.setState({ stepIndex: 2 })
    expect(cb.onCloseEditDialog).toHaveBeenCalledTimes(1)
    expect(cb.onOpenProvidersTab).toHaveBeenCalled()
  })

  it('步骤 1 点击厂商标签触发器后延迟跳到步骤 2', () => {
    vi.useFakeTimers()
    const view = setup(runningState(1))
    const trigger = addTourElement('providers-tab-trigger')
    view.callbacks.onOpenProvidersTab.mockClear()

    trigger.click()
    expect(view.callbacks.onOpenProvidersTab).toHaveBeenCalledTimes(1)
    expect(view.fns.goToStep).not.toHaveBeenCalled()

    vi.advanceTimersByTime(300)
    expect(view.fns.goToStep).toHaveBeenCalledWith(2)
  })

  it('步骤 2 点击命中目标元素包围盒时也推进步骤（特征化坐标兜底）', () => {
    // jsdom 中元素包围盒全为 0，点击坐标 (0,0) 会被判定为命中目标
    vi.useFakeTimers()
    const view = setup(runningState(2))
    addTourElement('add-provider-button')
    const outside = document.createElement('div')
    document.body.appendChild(outside)

    outside.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 0, clientY: 0 }))
    expect(view.callbacks.onOpenProviderDialog).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(300)
    expect(view.fns.goToStep).toHaveBeenCalledWith(3)
  })

  it('步骤 17 点击模型取消按钮后关闭编辑对话框并跳到步骤 18', () => {
    vi.useFakeTimers()
    const view = setup(runningState(17))
    const cancel = addTourElement('model-cancel-button')

    cancel.click()
    expect(view.callbacks.onCloseEditDialog).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(300)
    expect(view.fns.goToStep).toHaveBeenCalledWith(18)
  })

  it('引导未运行时点击目标元素不做任何联动', () => {
    const view = setup(idleState)
    const trigger = addTourElement('providers-tab-trigger')
    view.callbacks.onOpenProvidersTab.mockClear()

    trigger.click()
    expect(view.callbacks.onOpenProvidersTab).not.toHaveBeenCalled()
    expect(view.fns.goToStep).not.toHaveBeenCalled()
  })
})
