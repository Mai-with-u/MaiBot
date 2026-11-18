import { render, screen } from '@testing-library/react'
import { useCallback, useState } from 'react'
import { describe, expect, it } from 'vitest'

import { ScrollArea } from './scroll-area'

function MeasuredScrollArea({ label }: { label: string }) {
  const [measurements, setMeasurements] = useState(0)
  const measure = useCallback((node: HTMLDivElement | null) => {
    if (node) setMeasurements((count) => count + 1)
  }, [])

  return (
    <ScrollArea ref={measure} data-testid="measured-scroll-area" data-measurements={measurements}>
      {label}
    </ScrollArea>
  )
}

describe('ScrollArea ref lifecycle', () => {
  it('keeps the ref attached when measuring the element updates parent state', () => {
    // 测量回调更新父状态是合法用法；普通渲染不应重新绑定未变化的 DOM 引用。
    // 旧版 Radix 会在这里反复解绑/绑定，最终触发 React #185。
    const { rerender } = render(<MeasuredScrollArea label="Initial messages" />)

    expect(screen.getByTestId('measured-scroll-area')).toHaveAttribute('data-measurements', '1')

    rerender(<MeasuredScrollArea label="Updated messages" />)

    expect(screen.getByText('Updated messages')).toBeInTheDocument()
    expect(screen.getByTestId('measured-scroll-area')).toHaveAttribute('data-measurements', '1')
  })
})
