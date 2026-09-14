import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CodeEditor } from '../CodeEditor'

/** 同一 thenable：渲染 mock 实现时挂起，让外层 Suspense 停在 fallback */
const implSuspend = vi.hoisted(() => ({
  promise: new Promise<never>(() => {}),
}))

vi.mock('../CodeEditorImpl', () => ({
  default: function CodeEditorImplMock() {
    throw implSuspend.promise
  },
}))

describe('CodeEditor', () => {
  it('lazy 未完成时渲染带 data-dashboard-code-editor 的 fallback', async () => {
    const { container } = render(
      <CodeEditor
        value="print('hi')"
        className="extra-editor"
        height="220px"
        minHeight="80px"
        maxHeight="480px"
      />
    )
    await act(async () => {
      await Promise.resolve()
    })

    const fallback = container.querySelector('[data-dashboard-code-editor="true"]')
    expect(fallback).toBeInTheDocument()
    expect(fallback).toHaveClass('animate-pulse')
    expect(fallback).toHaveClass('extra-editor')
    expect(fallback).toHaveStyle({
      height: '220px',
      minHeight: '80px',
      maxHeight: '480px',
    })
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('未指定高度和 className 时 fallback 使用默认 400px', async () => {
    const { container } = render(<CodeEditor value="" />)
    await act(async () => {
      await Promise.resolve()
    })

    const fallback = container.querySelector('[data-dashboard-code-editor="true"]')
    expect(fallback).toBeInTheDocument()
    expect(fallback).toHaveStyle({ height: '400px' })
    expect(fallback).toHaveClass('bg-muted')
  })
})
