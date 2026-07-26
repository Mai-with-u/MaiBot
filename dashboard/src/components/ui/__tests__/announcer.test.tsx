import { act, fireEvent, render, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnnouncerProvider, useAnnounce } from '../announcer'

/** 测试用触发组件：通过按钮调用 announce，模拟业务组件的播报行为 */
function AnnounceTrigger() {
  const announce = useAnnounce()
  return (
    <div>
      <button onClick={() => announce('保存成功')}>polite</button>
      <button onClick={() => announce('操作失败，请重试', 'assertive')}>assertive</button>
      <button onClick={() => announce('第二条消息')}>polite-again</button>
    </div>
  )
}

function renderWithProvider() {
  const utils = render(
    <AnnouncerProvider>
      <AnnounceTrigger />
    </AnnouncerProvider>
  )
  const politeRegion = utils.container.querySelector('[aria-live="polite"]')
  const assertiveRegion = utils.container.querySelector('[aria-live="assertive"]')
  return { ...utils, politeRegion, assertiveRegion }
}

beforeEach(() => {
  // announce 内部有 50ms 的清空-重填延迟，用假定时器精确推进
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('AnnouncerProvider', () => {
  it('挂载两个视觉隐藏的 aria-live 区域', () => {
    const { politeRegion, assertiveRegion } = renderWithProvider()

    expect(politeRegion).not.toBeNull()
    expect(politeRegion).toHaveClass('sr-only')
    expect(politeRegion).toHaveAttribute('aria-atomic', 'true')
    expect(politeRegion).toHaveTextContent('')

    expect(assertiveRegion).not.toBeNull()
    expect(assertiveRegion).toHaveClass('sr-only')
    expect(assertiveRegion).toHaveAttribute('aria-atomic', 'true')
  })

  it('polite 播报延迟 50ms 后写入 polite 区域', () => {
    const { getByText, politeRegion, assertiveRegion } = renderWithProvider()

    fireEvent.click(getByText('polite'))
    // 延迟未到之前区域仍为空（先清空再重填，确保重复消息也会被朗读）
    expect(politeRegion).toHaveTextContent('')

    act(() => {
      vi.advanceTimersByTime(50)
    })
    expect(politeRegion).toHaveTextContent('保存成功')
    // assertive 区域不受影响
    expect(assertiveRegion).toHaveTextContent('')
  })

  it('assertive 播报写入 assertive 区域且不污染 polite 区域', () => {
    const { getByText, politeRegion, assertiveRegion } = renderWithProvider()

    fireEvent.click(getByText('assertive'))
    act(() => {
      vi.advanceTimersByTime(50)
    })

    expect(assertiveRegion).toHaveTextContent('操作失败，请重试')
    expect(politeRegion).toHaveTextContent('')
  })

  it('50ms 内连续播报时只保留最后一条消息', () => {
    const { getByText, politeRegion } = renderWithProvider()

    fireEvent.click(getByText('polite'))
    // 前一个定时器尚未触发时再次播报，应清掉旧定时器
    fireEvent.click(getByText('polite-again'))

    act(() => {
      vi.advanceTimersByTime(50)
    })

    expect(politeRegion).toHaveTextContent('第二条消息')
    expect(politeRegion).not.toHaveTextContent('保存成功')
  })
})

describe('useAnnounce', () => {
  it('在 Provider 外调用时静默降级为 noop，不抛错', () => {
    const { result } = renderHook(() => useAnnounce())

    expect(typeof result.current).toBe('function')
    expect(() => result.current('没有 Provider 也不该崩溃')).not.toThrow()
  })
})
