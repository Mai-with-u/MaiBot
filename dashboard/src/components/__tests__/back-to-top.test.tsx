import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { BackToTop } from '../back-to-top'

vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: { select: (state: { location: { pathname: string } }) => string }) =>
    select({ location: { pathname: '/settings' } }),
}))

const setScrollableMetrics = (element: HTMLElement, metrics: { clientHeight: number; scrollHeight: number; scrollTop: number }) => {
  Object.defineProperty(element, 'clientHeight', {
    configurable: true,
    value: metrics.clientHeight,
  })
  Object.defineProperty(element, 'scrollHeight', {
    configurable: true,
    value: metrics.scrollHeight,
  })
  Object.defineProperty(element, 'scrollTop', {
    configurable: true,
    writable: true,
    value: metrics.scrollTop,
  })
}

const triggerScrollableScroll = (element: HTMLElement, scrollTop: number) => {
  Object.defineProperty(element, 'scrollTop', {
    configurable: true,
    writable: true,
    value: scrollTop,
  })
  fireEvent.scroll(element)
}

const mockPointerMode = (pointer: 'coarse' | 'fine') => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: query === '(pointer: coarse)' ? pointer === 'coarse' : false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => {},
    }),
  })
}

describe('BackToTop', () => {
  beforeEach(() => {
    document.body.replaceChildren()
    window.localStorage.clear()
    mockPointerMode('fine')
    if (!Element.prototype.setPointerCapture) {
      Element.prototype.setPointerCapture = vi.fn()
    }
    if (!Element.prototype.releasePointerCapture) {
      Element.prototype.releasePointerCapture = vi.fn()
    }
  })

  it('点击按钮时滚动到顶部', () => {
    const scroller = document.createElement('div')
    setScrollableMetrics(scroller, { clientHeight: 300, scrollHeight: 1200, scrollTop: 500 })
    scroller.scrollTo = vi.fn()
    document.body.appendChild(scroller)

    render(<BackToTop />)
    triggerScrollableScroll(scroller, 500)

    const button = screen.getByRole('button', { name: '回到顶部' })
    fireEvent.click(button)

    expect(scroller.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
  })

  it('垂直拖拽时更新 translateY 且不触发回顶', () => {
    const scroller = document.createElement('div')
    setScrollableMetrics(scroller, { clientHeight: 300, scrollHeight: 1200, scrollTop: 500 })
    scroller.scrollTo = vi.fn()
    document.body.appendChild(scroller)

    render(<BackToTop />)
    triggerScrollableScroll(scroller, 500)

    const button = screen.getByRole('button', { name: '回到顶部' })
    vi.spyOn(button, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 400,
      width: 40,
      height: 40,
      top: 400,
      right: 40,
      bottom: 440,
      left: 0,
      toJSON: () => ({}),
    } as DOMRect)

    fireEvent.pointerDown(button, { buttons: 1, clientX: 20, clientY: 200, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerMove(button, { buttons: 1, clientX: 20, clientY: 260, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerUp(button, { clientX: 20, clientY: 260, pointerId: 1, pointerType: 'mouse' })
    fireEvent.click(button)

    expect(button.parentElement).toHaveStyle({ transform: 'translate3d(0px, 60px, 0px)' })
    expect(scroller.scrollTo).not.toHaveBeenCalled()
  })

  it('粗指针设备上通过触摸拖拽也能更新 translateY', () => {
    mockPointerMode('coarse')

    const scroller = document.createElement('div')
    setScrollableMetrics(scroller, { clientHeight: 300, scrollHeight: 1200, scrollTop: 500 })
    scroller.scrollTo = vi.fn()
    document.body.appendChild(scroller)

    render(<BackToTop />)
    triggerScrollableScroll(scroller, 500)

    const button = screen.getByRole('button', { name: '回到顶部' })
    vi.spyOn(button, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 400,
      width: 40,
      height: 40,
      top: 400,
      right: 40,
      bottom: 440,
      left: 0,
      toJSON: () => ({}),
    } as DOMRect)

    fireEvent.touchStart(button, {
      changedTouches: [{ clientX: 20, clientY: 200, identifier: 7, target: button }],
      targetTouches: [{ clientX: 20, clientY: 200, identifier: 7, target: button }],
      touches: [{ clientX: 20, clientY: 200, identifier: 7, target: button }],
    })
    fireEvent.touchMove(button, {
      changedTouches: [{ clientX: 20, clientY: 250, identifier: 7, target: button }],
      targetTouches: [{ clientX: 20, clientY: 250, identifier: 7, target: button }],
      touches: [{ clientX: 20, clientY: 250, identifier: 7, target: button }],
    })
    fireEvent.touchEnd(button, {
      changedTouches: [{ clientX: 20, clientY: 250, identifier: 7, target: button }],
      targetTouches: [],
      touches: [],
    })

    expect(button.parentElement).toHaveStyle({ transform: 'translate3d(0px, 50px, 0px)' })
  })
})
