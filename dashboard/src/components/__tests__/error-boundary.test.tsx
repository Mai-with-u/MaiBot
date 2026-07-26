import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ErrorBoundary, RouteErrorBoundary } from '../error-boundary'

// t 在 vi.mock 工厂内建一次，保持稳定引用，避免依赖 [t] 的 hook 失稳
vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return {
    useTranslation: () => ({ t, i18n: { language: 'zh-CN' } }),
  }
})

// 构造带可控堆栈的错误，保证堆栈解析断言稳定
function makeError(message: string, stack?: string): Error {
  const error = new Error(message)
  error.name = 'RangeError'
  error.stack = stack
  return error
}

// 三行堆栈：具名函数帧、匿名帧、无法解析的帧
const SAMPLE_STACK = [
  'RangeError: 渲染爆炸',
  '    at renderCard (src/components/card.tsx:12:34)',
  '    at src/pages/home.tsx:5:6',
  '    at some-unparsable-line',
].join('\n')

// 抛错子组件，用于触发错误边界
function ThrowingChild({ error }: { error: Error }): null {
  throw error
}

beforeEach(() => {
  // 静音 React 与 componentDidCatch 的错误日志，避免测试输出被刷屏
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

describe('ErrorBoundary', () => {
  it('子组件正常时直接渲染子内容', () => {
    render(
      <ErrorBoundary>
        <div>正常内容</div>
      </ErrorBoundary>,
    )

    expect(screen.getByText('正常内容')).toBeInTheDocument()
    expect(screen.queryByText('errorBoundary.title')).not.toBeInTheDocument()
  })

  it('子组件抛错时渲染默认回退 UI 并展示错误名称与消息', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild error={makeError('渲染爆炸', SAMPLE_STACK)} />
      </ErrorBoundary>,
    )

    expect(screen.getByText('errorBoundary.title')).toBeInTheDocument()
    expect(screen.getByText('errorBoundary.description')).toBeInTheDocument()
    expect(screen.getByText('errorBoundary.footer')).toBeInTheDocument()
    // 错误名称与消息分属不同文本节点，分别断言
    expect(screen.getByText('RangeError:')).toBeInTheDocument()
    expect(screen.getByText('渲染爆炸')).toBeInTheDocument()
    // 操作按钮
    expect(screen.getByRole('button', { name: 'errorBoundary.refreshPage' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'errorBoundary.goHome' })).toBeInTheDocument()
  })

  it('堆栈被解析为结构化帧并默认展开', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild error={makeError('渲染爆炸', SAMPLE_STACK)} />
      </ErrorBoundary>,
    )

    // 三行 at 开头的堆栈全部计入帧数
    expect(screen.getByRole('button', { name: 'Stack Trace (3 frames)' })).toBeInTheDocument()
    // 具名函数帧：函数名与文件位置
    expect(screen.getByText('renderCard')).toBeInTheDocument()
    expect(screen.getByText('src/components/card.tsx')).toBeInTheDocument()
    expect(screen.getByText(':12:34')).toBeInTheDocument()
    // 无函数名的帧回退为 <anonymous>
    expect(screen.getByText('<anonymous>')).toBeInTheDocument()
    // 无法解析的帧回退为 <unknown>
    expect(screen.getByText('<unknown>')).toBeInTheDocument()
  })

  it('点击 Stack Trace 触发器可折叠堆栈内容', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild error={makeError('渲染爆炸', SAMPLE_STACK)} />
      </ErrorBoundary>,
    )

    expect(screen.getByText('renderCard')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stack Trace (3 frames)' }))
    expect(screen.queryByText('renderCard')).not.toBeInTheDocument()
  })

  it('错误没有堆栈时不渲染 Stack Trace 区域', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild error={makeError('无堆栈错误', undefined)} />
      </ErrorBoundary>,
    )

    expect(screen.getByText('errorBoundary.title')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Stack Trace/ })).not.toBeInTheDocument()
  })

  it('组件堆栈默认折叠，点击后展开显示抛错组件名', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild error={makeError('渲染爆炸', SAMPLE_STACK)} />
      </ErrorBoundary>,
    )

    // 匹配组件堆栈的 pre 元素内容（error.stack 中不含 ThrowingChild，避免误匹配）
    const componentStackMatcher = (_: string, element: Element | null) =>
      element?.tagName === 'PRE' && (element.textContent ?? '').includes('ThrowingChild')

    expect(screen.queryByText(componentStackMatcher)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Component Stack' }))
    expect(screen.getByText(componentStackMatcher)).toBeInTheDocument()
  })

  it('点击复制按钮把错误信息写入剪贴板并切换为已复制文案', async () => {
    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)

    render(
      <ErrorBoundary>
        <ThrowingChild error={makeError('渲染爆炸', SAMPLE_STACK)} />
      </ErrorBoundary>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'errorBoundary.copyError' }))

    await waitFor(() => {
      expect(screen.getByText('errorBoundary.copiedToClipboard')).toBeInTheDocument()
    })
    expect(writeTextSpy).toHaveBeenCalledTimes(1)
    const copiedText = writeTextSpy.mock.calls[0][0]
    expect(copiedText).toContain('Error: RangeError')
    expect(copiedText).toContain('Message: 渲染爆炸')
    expect(copiedText).toContain('renderCard')
  })

  it('提供自定义 fallback 时优先渲染 fallback', () => {
    render(
      <ErrorBoundary fallback={<div>自定义回退</div>}>
        <ThrowingChild error={makeError('渲染爆炸', SAMPLE_STACK)} />
      </ErrorBoundary>,
    )

    expect(screen.getByText('自定义回退')).toBeInTheDocument()
    expect(screen.queryByText('errorBoundary.title')).not.toBeInTheDocument()
  })
})

describe('RouteErrorBoundary', () => {
  it('直接渲染回退 UI，且没有组件堆栈区域', () => {
    render(<RouteErrorBoundary error={makeError('路由加载失败', SAMPLE_STACK)} />)

    expect(screen.getByText('errorBoundary.title')).toBeInTheDocument()
    expect(screen.getByText('路由加载失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stack Trace (3 frames)' })).toBeInTheDocument()
    // errorInfo 为 null，不应出现组件堆栈折叠区
    expect(screen.queryByRole('button', { name: 'Component Stack' })).not.toBeInTheDocument()
  })
})
