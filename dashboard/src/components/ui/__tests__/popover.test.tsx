import type { ComponentProps } from 'react'

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '../popover'

/** jsdom 未实现 Pointer Capture，Radix Popover 打开时会调用 */
function stubPointerCapture() {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
}

function renderOpenPopover(contentProps: ComponentProps<typeof PopoverContent> = {}) {
  return render(
    <Popover open>
      <PopoverAnchor>
        <span>锚点</span>
      </PopoverAnchor>
      <PopoverTrigger>打开面板</PopoverTrigger>
      <PopoverContent {...contentProps}>浮层内容</PopoverContent>
    </Popover>
  )
}

describe('Popover 包装层', () => {
  beforeEach(() => {
    stubPointerCapture()
  })

  it('打开后渲染内容，默认 align=center、sideOffset=4，并带浮层标记', () => {
    renderOpenPopover({ className: 'extra-popover' })

    const content = screen.getByText('浮层内容')
    expect(content).toHaveAttribute('data-dashboard-floating-content', 'true')
    expect(content).toHaveAttribute('data-align', 'center')
    expect(content).toHaveClass('extra-popover', 'z-[60]', 'w-72')
  })

  it('可覆盖 align 与 sideOffset', () => {
    renderOpenPopover({ align: 'start', sideOffset: 12 })

    const content = screen.getByText('浮层内容')
    expect(content).toHaveAttribute('data-align', 'start')
  })

  it('未提供 onWheel / onTouchMove 时仍会 stopPropagation', () => {
    renderOpenPopover()
    const content = screen.getByText('浮层内容')

    const stop = vi.spyOn(Event.prototype, 'stopPropagation')
    fireEvent.wheel(content)
    expect(stop).toHaveBeenCalled()
    stop.mockClear()

    fireEvent.touchMove(content, {
      touches: [{ clientX: 0, clientY: 0 }],
      changedTouches: [{ clientX: 0, clientY: 0 }],
    })
    expect(stop).toHaveBeenCalled()
    stop.mockRestore()
  })

  it('提供 onWheel / onTouchMove 时先回调再阻止冒泡', () => {
    const onWheel = vi.fn()
    const onTouchMove = vi.fn()
    renderOpenPopover({ onWheel, onTouchMove })

    const content = screen.getByText('浮层内容')
    fireEvent.wheel(content)
    fireEvent.touchMove(content, {
      touches: [{ clientX: 0, clientY: 0 }],
      changedTouches: [{ clientX: 0, clientY: 0 }],
    })

    expect(onWheel).toHaveBeenCalledTimes(1)
    expect(onTouchMove).toHaveBeenCalledTimes(1)
  })

  it('关闭态不渲染浮层内容', () => {
    render(
      <Popover>
        <PopoverTrigger>打开面板</PopoverTrigger>
        <PopoverContent>隐藏内容</PopoverContent>
      </Popover>
    )

    expect(screen.queryByText('隐藏内容')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开面板' })).toBeInTheDocument()
  })
})
