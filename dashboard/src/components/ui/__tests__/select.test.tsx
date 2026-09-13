import type { ComponentProps } from 'react'

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '../select'

/** jsdom 未实现 Pointer Capture，Radix Select 打开下拉时会调用 */
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

/** 补齐 changedTouches，避免 react-remove-scroll 读取空 TouchList */
function dispatchTouchMove(element: Element) {
  const event = new Event('touchmove', { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'changedTouches', {
    value: [{ clientX: 0, clientY: 0 }],
  })
  Object.defineProperty(event, 'touches', {
    value: [{ clientX: 0, clientY: 0 }],
  })
  element.dispatchEvent(event)
}

function renderOpenSelect(
  contentProps: ComponentProps<typeof SelectContent> = {},
  triggerClassName?: string
) {
  return render(
    <Select open value="apple">
      <SelectTrigger className={triggerClassName}>
        <SelectValue placeholder="请选择水果" />
      </SelectTrigger>
      <SelectContent {...contentProps}>
        <SelectGroup>
          <SelectLabel className="extra-label">水果</SelectLabel>
          <SelectItem value="apple">苹果</SelectItem>
          <SelectSeparator className="extra-separator" />
          <SelectItem value="banana" className="extra-item">
            香蕉
          </SelectItem>
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

describe('Select 包装层', () => {
  beforeEach(() => {
    stubPointerCapture()
  })

  it('Trigger 带选择器标记、下拉图标与自定义类名', () => {
    renderOpenSelect({}, 'extra-trigger')

    // 打开态下 combobox 被 aria-hidden，按标记查询
    const trigger = document.querySelector('[data-dashboard-select-trigger="true"]')
    expect(trigger).not.toBeNull()
    expect(trigger).toHaveClass('extra-trigger')
    expect(trigger?.querySelector('.lucide-chevron-down')).not.toBeNull()
  })

  it('默认 popper 定位给内容和视口追加偏移与尺寸类', () => {
    renderOpenSelect({ className: 'extra-content' })

    const content = document.querySelector('[data-dashboard-select-content="true"]')
    expect(content).not.toBeNull()
    expect(content).toHaveClass('extra-content', 'data-[side=bottom]:translate-y-1')
    expect(content).toHaveStyle({
      maxHeight: 'min(var(--radix-select-content-available-height, 20rem), 20rem)',
    })

    const viewport = content?.querySelector('[class*="radix-select-trigger-height"]')
    expect(viewport).not.toBeNull()
    expect(viewport).toHaveClass('h-[var(--radix-select-trigger-height)]', 'w-full')
  })

  it('position=item-aligned 时不追加 popper 偏移与视口高度类', () => {
    renderOpenSelect({ position: 'item-aligned' })

    const content = document.querySelector('[data-dashboard-select-content="true"]')
    expect(content).not.toBeNull()
    expect(content).not.toHaveClass('data-[side=bottom]:translate-y-1')

    const viewport = content?.querySelector('.p-1')
    expect(viewport).not.toBeNull()
    expect(viewport).not.toHaveClass('h-[var(--radix-select-trigger-height)]')
  })

  it('自定义 style 会覆盖默认 maxHeight', () => {
    renderOpenSelect({ style: { maxHeight: '10rem', width: '12rem' } })

    const content = document.querySelector('[data-dashboard-select-content="true"]')
    expect(content).toHaveStyle({ maxHeight: '10rem', width: '12rem' })
  })

  it('Label / Item / Separator 渲染对应角色与自定义类', () => {
    renderOpenSelect()

    expect(screen.getByText('水果')).toHaveClass('extra-label', 'font-semibold')
    const apple = screen.getByRole('option', { name: '苹果' })
    const banana = screen.getByRole('option', { name: '香蕉' })
    expect(banana).toHaveAttribute('data-dashboard-select-item', 'true')
    expect(banana).toHaveClass('extra-item')
    // ItemIndicator 只在选中项里挂载 Check 图标
    expect(apple.querySelector('.lucide-check')).not.toBeNull()
    expect(banana.querySelector('.lucide-check')).toBeNull()

    const separator = document.querySelector('.extra-separator')
    expect(separator).not.toBeNull()
    expect(separator).toHaveClass('bg-muted')
  })

  it('未提供 onWheel / onTouchMove 时仍会 stopPropagation', () => {
    renderOpenSelect()
    const content = document.querySelector('[data-dashboard-select-content="true"]')
    expect(content).not.toBeNull()

    const stop = vi.spyOn(Event.prototype, 'stopPropagation')
    fireEvent.wheel(content as Element)
    expect(stop).toHaveBeenCalled()
    stop.mockClear()

    dispatchTouchMove(content as Element)
    expect(stop).toHaveBeenCalled()
    stop.mockRestore()
  })

  it('提供 onWheel / onTouchMove 时先回调再阻止冒泡', () => {
    const onWheel = vi.fn()
    const onTouchMove = vi.fn()
    renderOpenSelect({ onWheel, onTouchMove })

    const content = document.querySelector('[data-dashboard-select-content="true"]')
    expect(content).not.toBeNull()

    fireEvent.wheel(content as Element)
    expect(onWheel).toHaveBeenCalledTimes(1)

    dispatchTouchMove(content as Element)
    expect(onTouchMove).toHaveBeenCalledTimes(1)
  })
})
