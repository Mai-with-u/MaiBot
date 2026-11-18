import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ComponentGalleryCanvas } from '../component-gallery-canvas'

afterEach(cleanup)

function setupCanvas() {
  const { container } = render(
    <ComponentGalleryCanvas>
      <section data-gallery-item>
        <button>示例操作</button>
      </section>
    </ComponentGalleryCanvas>
  )
  const area = screen.getByRole('region', { name: '公共组件画布' })
  const board = container.querySelector<HTMLElement>('[data-gallery-content]')!
  Object.defineProperties(area, {
    clientWidth: { value: 800 },
    clientHeight: { value: 600 },
    offsetWidth: { value: 800 },
    offsetHeight: { value: 600 },
  })
  vi.spyOn(area, 'getBoundingClientRect').mockReturnValue(new DOMRect(100, 60, 1000, 750))
  Object.defineProperties(board, { offsetWidth: { value: 2000 }, offsetHeight: { value: 3000 } })
  return { area, board }
}

function readCamera(board: HTMLElement) {
  const values = board.style.transform.match(
    /translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/
  )!
  return { x: Number(values[1]), y: Number(values[2]), scale: Number(values[3]) }
}

describe('组件画布缩放', () => {
  it('容器有偏移且祖先缩放为 125% 时仍保持鼠标下的画布坐标不变', () => {
    const { area, board } = setupCanvas()
    const event = new WheelEvent('wheel', {
      bubbles: true,
      cancelable: true,
      deltaY: -100,
      clientX: 350,
      clientY: 247.5,
    })
    fireEvent(area, event)
    const camera = readCamera(board)
    expect(event.defaultPrevented).toBe(true)
    expect(camera.scale).toBeGreaterThan(1)
    expect((200 - camera.x) / camera.scale).toBeCloseTo(200)
    expect((150 - camera.y) / camera.scale).toBeCloseTo(150)
  })

  it('按内容的宽高完整居中适配，并能恢复初始视图', () => {
    const { board } = setupCanvas()
    fireEvent.click(screen.getByRole('button', { name: '显示全部' }))
    const camera = readCamera(board)
    expect(camera.x).toBeGreaterThanOrEqual(16)
    expect(camera.y).toBeGreaterThanOrEqual(16)
    expect(camera.x + 2000 * camera.scale).toBeLessThanOrEqual(784)
    expect(camera.y + 3000 * camera.scale).toBeLessThanOrEqual(584)
    expect(camera.x * 2 + 2000 * camera.scale).toBeCloseTo(800)
    expect(camera.y * 2 + 3000 * camera.scale).toBeCloseTo(600)
    fireEvent.click(screen.getByRole('button', { name: '重置视图' }))
    expect(readCamera(board)).toEqual({ x: 0, y: 0, scale: 1 })
  })
})
