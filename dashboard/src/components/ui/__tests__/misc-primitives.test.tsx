import type { ReactNode } from 'react'

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CardWithBackground } from '../card-with-background'
import { DialogContentWithBackground } from '../dialog-with-background'
import { Markdown } from '../markdown'
import { SkipNav } from '../skip-nav'
import { Toaster } from '../toaster'

const { toastState, mobileState } = vi.hoisted(() => ({
  toastState: { toasts: [] as Array<Record<string, unknown>> },
  mobileState: { value: false },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/hooks/use-background', () => ({
  useBackground: (layer: string) => ({ config: { layer } }),
}))

vi.mock('@/components/background-layer', () => ({
  BackgroundLayer: ({ layerId }: { layerId: string }) => (
    <div data-testid={`background-${layerId}`} />
  ),
}))

vi.mock('@/components/ui/dialog', () => ({
  DialogContent: ({
    children,
    className,
    ...props
  }: {
    children: ReactNode
    className?: string
    [key: string]: unknown
  }) => (
    <section data-testid="dialog-content" className={className} {...props}>
      {children}
    </section>
  ),
}))

vi.mock('@/components/markdown-renderer', () => ({
  MarkdownRenderer: ({ content, className }: { content: string; className?: string }) => (
    <article data-testid="markdown-renderer" className={className}>
      {content}
    </article>
  ),
}))

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => toastState,
}))

vi.mock('@/hooks/use-media-query', () => ({
  useIsMobile: () => mobileState.value,
}))

vi.mock('@/components/ui/toast', () => ({
  ToastProvider: ({
    children,
    swipeDirection,
  }: {
    children: ReactNode
    swipeDirection: string
  }) => (
    <div data-testid="toast-provider" data-swipe={swipeDirection}>
      {children}
    </div>
  ),
  Toast: ({
    children,
    duration,
    variant,
  }: {
    children: ReactNode
    duration: number
    variant?: string
  }) => (
    <div data-testid="toast" data-duration={String(duration)} data-variant={variant}>
      {children}
    </div>
  ),
  ToastTitle: ({ children }: { children: ReactNode }) => <strong>{children}</strong>,
  ToastDescription: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  ToastClose: () => <button type="button">关闭</button>,
  ToastViewport: () => <div data-testid="toast-viewport" />,
}))

afterEach(() => cleanup())

beforeEach(() => {
  toastState.toasts = []
  mobileState.value = false
})

describe('小型 UI 模块', () => {
  it('SkipNav 指向主内容并使用无障碍翻译键', () => {
    render(<SkipNav />)
    expect(screen.getByRole('link', { name: 'a11y.skipToContent' })).toHaveAttribute(
      'href',
      '#main-content'
    )
  })

  it('CardWithBackground 合并样式、透传属性并渲染卡片背景', () => {
    render(
      <CardWithBackground className="custom-card" data-testid="card">
        卡片内容
      </CardWithBackground>
    )

    expect(screen.getByTestId('card')).toHaveClass('relative', 'isolate', 'custom-card')
    expect(screen.getByTestId('background-card')).toBeInTheDocument()
    expect(screen.getByText('卡片内容')).toBeInTheDocument()
  })

  it('DialogContentWithBackground 透传内容和属性', () => {
    render(
      <DialogContentWithBackground className="custom-dialog" aria-label="测试对话框">
        对话框内容
      </DialogContentWithBackground>
    )

    expect(screen.getByTestId('dialog-content')).toHaveClass('relative', 'isolate', 'custom-dialog')
    expect(screen.getByTestId('background-dialog')).toBeInTheDocument()
    expect(screen.getByLabelText('测试对话框')).toHaveTextContent('对话框内容')
  })

  it('Markdown 把文本和样式完整交给 MarkdownRenderer', () => {
    render(<Markdown className="prose-test"># 标题</Markdown>)
    expect(screen.getByTestId('markdown-renderer')).toHaveClass('prose-test')
    expect(screen.getByTestId('markdown-renderer')).toHaveTextContent('# 标题')
  })
})

describe('Toaster', () => {
  it('桌面端使用右滑，并给普通、危险和自定义提示设置对应时长', () => {
    toastState.toasts = [
      { id: 'default', title: '普通提示', description: '五秒', variant: 'default' },
      { id: 'danger', title: '危险提示', variant: 'destructive' },
      { id: 'custom', title: '自定义提示', duration: 1234 },
    ]
    render(<Toaster />)

    expect(screen.getByTestId('toast-provider')).toHaveAttribute('data-swipe', 'right')
    expect(screen.getAllByTestId('toast').map((toast) => toast.dataset.duration)).toEqual([
      '5000',
      '10000',
      '1234',
    ])
    expect(screen.getByText('五秒')).toBeInTheDocument()
  })

  it('移动端使用上滑方向且始终渲染视口', () => {
    mobileState.value = true
    render(<Toaster />)
    expect(screen.getByTestId('toast-provider')).toHaveAttribute('data-swipe', 'up')
    expect(screen.getByTestId('toast-viewport')).toBeInTheDocument()
  })
})
