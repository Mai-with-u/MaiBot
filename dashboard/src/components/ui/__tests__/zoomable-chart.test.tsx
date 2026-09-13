import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ZoomableChart } from '../zoomable-chart'

const pinchState = vi.hoisted(() => ({
  handler: undefined as
    | ((state: { offset: [number]; first: boolean; last: boolean }) => void)
    | undefined,
  options: undefined as Record<string, unknown> | undefined,
}))

vi.mock('@use-gesture/react', () => ({
  usePinch: (
    handler: (state: { offset: [number]; first: boolean; last: boolean }) => void,
    options: Record<string, unknown>
  ) => {
    pinchState.handler = handler
    pinchState.options = options
  },
}))

function pinch(scale: number, first = false, last = false) {
  act(() => {
    pinchState.handler?.({ offset: [scale], first, last })
  })
}

describe('ZoomableChart', () => {
  beforeEach(() => {
    pinchState.handler = undefined
    pinchState.options = undefined
  })

  afterEach(() => {
    cleanup()
  })

  it('渲染图表容器、无障碍标签、默认缩放边界与子节点', () => {
    render(
      <ZoomableChart aria-label="每小时请求量趋势" className="extra-chart">
        <span>折线</span>
      </ZoomableChart>
    )

    const root = screen.getByRole('img', { name: '每小时请求量趋势' })
    expect(root).toHaveClass('overflow-hidden', 'touch-none', 'select-none', 'extra-chart')
    expect(root).toHaveStyle({ touchAction: 'none' })
    expect(screen.getByText('折线')).toBeInTheDocument()

    expect(pinchState.options).toMatchObject({
      scaleBounds: { min: 0.5 * 0.85, max: 4 * 1.15 },
      rubberband: true,
      preventDefault: true,
      eventOptions: { passive: false },
    })
    expect(pinchState.options?.target).toBeDefined()
    expect(typeof pinchState.handler).toBe('function')
  })

  it('自定义 minScale / maxScale 会写入手势边界', () => {
    render(
      <ZoomableChart aria-label="自定义范围" minScale={1} maxScale={2}>
        图
      </ZoomableChart>
    )

    expect(pinchState.options).toMatchObject({
      scaleBounds: { min: 0.85, max: 2.3 },
    })
  })

  it('缩放到范围内只启动一次弹簧，first 时 immediate', () => {
    render(<ZoomableChart aria-label="范围内缩放">图</ZoomableChart>)

    pinch(1, true, false)
    pinch(2, false, true)
    expect(screen.getByRole('img', { name: '范围内缩放' })).toBeInTheDocument()
  })

  it('低于 minScale 走下限 rubberband，松手后弹回 minScale', () => {
    render(<ZoomableChart aria-label="下限弹性">图</ZoomableChart>)

    // scale=0.4 → clamped=0.425 < 0.5，松手后再夹到 0.5
    pinch(0.4, false, false)
    pinch(0.4, false, true)
    expect(screen.getByRole('img', { name: '下限弹性' })).toBeInTheDocument()
  })

  it('高于 maxScale 走上限 rubberband，松手后弹回 maxScale', () => {
    render(<ZoomableChart aria-label="上限弹性">图</ZoomableChart>)

    // scale=5 → clamped=4.6 > 4，松手后再夹到 4
    pinch(5, false, false)
    pinch(5, false, true)
    expect(screen.getByRole('img', { name: '上限弹性' })).toBeInTheDocument()
  })

  it('自定义边界下低于 min、高于 max 以及范围内松手都可执行', () => {
    render(
      <ZoomableChart aria-label="自定义弹性" minScale={1} maxScale={2}>
        图
      </ZoomableChart>
    )

    pinch(0.5, true, false)
    pinch(0.5, false, true)
    pinch(3, false, false)
    pinch(3, false, true)
    pinch(1.5, false, true)
    expect(screen.getByRole('img', { name: '自定义弹性' })).toBeInTheDocument()
  })
})
