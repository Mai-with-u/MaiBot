import type { ReactNode } from 'react'
import type { TourContextType } from '../types'

import { cleanup, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LazyTourRenderer } from '../lazy-tour-renderer'
import { TourContext } from '../tour-context'
import { useTour } from '../use-tour'

vi.mock('../tour-renderer', () => ({
  TourRenderer: () => <div>引导渲染器</div>,
}))

function makeContext(isRunning: boolean): TourContextType {
  return {
    state: { activeTourId: isRunning ? 'model' : null, stepIndex: 0, isRunning },
    tours: new Map(),
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

function wrapper(value: TourContextType) {
  return ({ children }: { children: ReactNode }) => (
    <TourContext.Provider value={value}>{children}</TourContext.Provider>
  )
}

afterEach(() => cleanup())

describe('Tour 入口模块', () => {
  it('Provider 外调用 useTour 会立即暴露配置错误', () => {
    expect(() => renderHook(() => useTour())).toThrow('useTour must be used within a TourProvider')
  })

  it('useTour 原样返回 Provider 提供的上下文', () => {
    const context = makeContext(false)
    const { result } = renderHook(() => useTour(), { wrapper: wrapper(context) })
    expect(result.current).toBe(context)
  })

  it('引导未运行时不加载渲染器', () => {
    const { container } = render(<LazyTourRenderer />, {
      wrapper: wrapper(makeContext(false)),
    })
    expect(container).toBeEmptyDOMElement()
  })

  it('引导运行时惰性加载渲染器', async () => {
    render(<LazyTourRenderer />, { wrapper: wrapper(makeContext(true)) })
    expect(await screen.findByText('引导渲染器')).toBeInTheDocument()
  })
})
