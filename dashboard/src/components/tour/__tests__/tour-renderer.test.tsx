import type { Step } from 'react-joyride'
import type { TourContextType, TourState } from '../types'

import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TourContext } from '../tour-context'
import { TourRenderer } from '../tour-renderer'

// 捕获传给 Joyride 的 props，供断言透传是否正确
const captured = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }))

vi.mock('react-joyride', () => ({
  default: (props: Record<string, unknown>) => {
    captured.props = props
    return <div data-testid="joyride-mock" />
  },
}))

// 构造一份可控的 Tour 上下文
function makeContext(state: TourState, steps: Step[]): TourContextType {
  return {
    state,
    tours: new Map(),
    registerTour: vi.fn(),
    unregisterTour: vi.fn(),
    startTour: vi.fn(),
    stopTour: vi.fn(),
    goToStep: vi.fn(),
    nextStep: vi.fn(),
    prevStep: vi.fn(),
    getCurrentSteps: () => steps,
    handleJoyrideCallback: vi.fn(),
    isTourCompleted: vi.fn(() => false),
    markTourCompleted: vi.fn(),
    resetTourCompleted: vi.fn(),
  }
}

function renderWithContext(context: TourContextType) {
  return render(
    <TourContext.Provider value={context}>
      <TourRenderer />
    </TourContext.Provider>
  )
}

// 插入一个 getBoundingClientRect 有实际尺寸的可见元素（jsdom 默认全为 0）
function addVisibleElement(id: string): HTMLElement {
  const element = document.createElement('div')
  element.id = id
  element.getBoundingClientRect = () =>
    ({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 120,
      bottom: 40,
      width: 120,
      height: 40,
      toJSON: () => ({}),
    }) as DOMRect
  document.body.appendChild(element)
  return element
}

const insertedIds: string[] = []

function addTrackedVisibleElement(id: string): HTMLElement {
  insertedIds.push(id)
  return addVisibleElement(id)
}

beforeEach(() => {
  captured.props = null
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  for (const id of insertedIds.splice(0)) {
    document.getElementById(id)?.remove()
  }
})

describe('TourRenderer 渲染联动', () => {
  it('未运行时不渲染 Joyride', () => {
    const context = makeContext({ activeTourId: null, stepIndex: 0, isRunning: false }, [
      { target: 'body', content: '第一步' },
    ])
    renderWithContext(context)
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()
    expect(captured.props).toBeNull()
  })

  it('运行中但步骤为空时不渲染 Joyride', () => {
    const context = makeContext({ activeTourId: 'demo', stepIndex: 0, isRunning: true }, [])
    renderWithContext(context)
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()
  })

  it('body 目标立即就绪，渲染进高层级 portal 容器并透传关键 props', () => {
    const steps: Step[] = [{ target: 'body', content: '第一步' }]
    const context = makeContext({ activeTourId: 'demo', stepIndex: 0, isRunning: true }, steps)
    const { container } = renderWithContext(context)

    const joyride = screen.getByTestId('joyride-mock')
    // Joyride 通过 portal 渲染到 #tour-portal-container，而不是组件本地容器
    expect(joyride.closest('#tour-portal-container')).not.toBeNull()
    expect(container.querySelector('[data-testid="joyride-mock"]')).toBeNull()

    // 关键 props 透传：运行态、步骤、回调、中文本地化
    expect(captured.props).not.toBeNull()
    expect(captured.props?.run).toBe(true)
    expect(captured.props?.stepIndex).toBe(0)
    expect(captured.props?.steps).toBe(steps)
    expect(captured.props?.callback).toBe(context.handleJoyrideCallback)
    expect(captured.props?.locale).toMatchObject({
      back: '上一步',
      last: '完成',
      next: '下一步',
      skip: '跳过',
    })
  })

  it('选择器目标已存在且可见时，等待 DOM 稳定延迟后渲染', () => {
    vi.useFakeTimers()
    addTrackedVisibleElement('ready-target')
    const context = makeContext({ activeTourId: 'demo', stepIndex: 0, isRunning: true }, [
      { target: '#ready-target', content: '第一步' },
    ])
    renderWithContext(context)

    // 初始 150ms 延迟内不渲染
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(150)
    })
    // 找到元素后还有 100ms 的动画等待
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(screen.getByTestId('joyride-mock')).toBeInTheDocument()
  })

  it('目标延迟出现时通过轮询捕获后再渲染', () => {
    vi.useFakeTimers()
    const context = makeContext({ activeTourId: 'demo', stepIndex: 0, isRunning: true }, [
      { target: '#late-target', content: '第一步' },
    ])
    renderWithContext(context)

    // 初始检查失败，进入轮询
    act(() => {
      vi.advanceTimersByTime(150)
    })
    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()

    // 元素出现后，下一次轮询 + 100ms 动画等待后渲染
    addTrackedVisibleElement('late-target')
    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(screen.getByTestId('joyride-mock')).toBeInTheDocument()
  })

  it('目标始终缺失时 5 秒超时后仍渲染，让 Joyride 展示错误提示', () => {
    vi.useFakeTimers()
    const context = makeContext({ activeTourId: 'demo', stepIndex: 0, isRunning: true }, [
      { target: '#never-exists', content: '第一步' },
    ])
    renderWithContext(context)

    act(() => {
      vi.advanceTimersByTime(150)
    })
    act(() => {
      vi.advanceTimersByTime(4999)
    })
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(screen.getByTestId('joyride-mock')).toBeInTheDocument()
  })

  it('步骤切换后先隐藏，等新目标就绪再以新索引渲染', () => {
    vi.useFakeTimers()
    const steps: Step[] = [
      { target: 'body', content: '第一步' },
      { target: '#step2-target', content: '第二步' },
    ]
    const running = makeContext({ activeTourId: 'demo', stepIndex: 0, isRunning: true }, steps)
    const { rerender } = renderWithContext(running)
    expect(screen.getByTestId('joyride-mock')).toBeInTheDocument()
    expect(captured.props?.stepIndex).toBe(0)

    // 切到第二步：目标还不存在，Joyride 应先隐藏
    const nextContext: TourContextType = {
      ...running,
      state: { activeTourId: 'demo', stepIndex: 1, isRunning: true },
      getCurrentSteps: () => steps,
    }
    rerender(
      <TourContext.Provider value={nextContext}>
        <TourRenderer />
      </TourContext.Provider>
    )
    expect(screen.queryByTestId('joyride-mock')).not.toBeInTheDocument()

    // 目标出现并等待检测完成后，携带新的 stepIndex 重新渲染
    addTrackedVisibleElement('step2-target')
    act(() => {
      vi.advanceTimersByTime(150)
    })
    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(screen.getByTestId('joyride-mock')).toBeInTheDocument()
    expect(captured.props?.stepIndex).toBe(1)
  })
})
