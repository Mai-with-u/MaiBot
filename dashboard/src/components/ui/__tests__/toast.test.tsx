import type { ReactNode } from 'react'

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  Toast,
  ToastAction,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
} from '../toast'

const { mobileState } = vi.hoisted(() => ({
  mobileState: { value: false },
}))

vi.mock('@/hooks/use-media-query', () => ({
  useIsMobile: () => mobileState.value,
}))

/** Toast 必须挂在 Provider + Viewport 下，Radix 才会把内容传送进视口 */
function renderToast(toast: ReactNode, viewportClassName?: string) {
  return render(
    <ToastProvider>
      {toast}
      <ToastViewport className={viewportClassName} />
    </ToastProvider>
  )
}

function queryToast(suffix = '') {
  return document.querySelector(`[data-dashboard-toast${suffix}="true"]`)
}

beforeEach(() => {
  mobileState.value = false
})

describe('ToastViewport', () => {
  it('桌面端靠右堆叠，并带视口标记与自定义 class', () => {
    render(
      <ToastProvider>
        <ToastViewport className="extra-viewport" />
      </ToastProvider>
    )

    const viewport = screen.getByRole('list')
    expect(viewport).toHaveAttribute('data-dashboard-toast-viewport', 'true')
    expect(viewport).toHaveClass('top-0', 'right-0', 'sm:max-w-[420px]', 'extra-viewport')
    expect(viewport).not.toHaveClass('items-center')
  })

  it('移动端顶部居中排列', () => {
    mobileState.value = true
    render(
      <ToastProvider>
        <ToastViewport />
      </ToastProvider>
    )

    const viewport = screen.getByRole('list')
    expect(viewport).toHaveClass('top-0', 'left-0', 'right-0', 'items-center')
    expect(viewport.className).not.toContain('sm:max-w-[420px]')
  })
})

describe('Toast', () => {
  it('桌面端使用 default 变体与从右侧滑入的位置类', () => {
    renderToast(
      <Toast open className="extra-toast">
        <ToastTitle>普通标题</ToastTitle>
      </Toast>
    )

    const toast = queryToast()
    expect(toast).not.toBeNull()
    expect(toast).toHaveClass(
      'bg-primary/5',
      'data-[state=open]:animate-slide-in-from-right',
      'extra-toast'
    )
    expect(toast).not.toHaveClass('data-[state=open]:animate-slide-in-from-top')
  })

  it('移动端 destructive 变体改用从顶部滑入', () => {
    mobileState.value = true
    renderToast(
      <Toast open variant="destructive">
        <ToastTitle>危险</ToastTitle>
      </Toast>
    )

    const toast = queryToast()
    expect(toast).toHaveClass(
      'destructive',
      'border-destructive',
      'data-[state=open]:animate-slide-in-from-top'
    )
    expect(toast).not.toHaveClass('data-[state=open]:animate-slide-in-from-right')
  })

  it('仅在有限且大于 0 的 duration 下渲染进度条', () => {
    const { rerender } = renderToast(
      <Toast open duration={2400}>
        <ToastTitle>有进度</ToastTitle>
      </Toast>
    )

    const progress = queryToast('-progress')
    expect(progress).not.toBeNull()
    expect(progress).toHaveStyle({
      animationDuration: '2400ms',
      animationPlayState: 'running',
    })

    rerender(
      <ToastProvider>
        <Toast open duration={0}>
          <ToastTitle>零时长</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    expect(queryToast('-progress')).toBeNull()

    rerender(
      <ToastProvider>
        <Toast open duration={Number.POSITIVE_INFINITY}>
          <ToastTitle>无限</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    expect(queryToast('-progress')).toBeNull()

    rerender(
      <ToastProvider>
        <Toast open duration={Number.NaN}>
          <ToastTitle>非数字</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    expect(queryToast('-progress')).toBeNull()

    rerender(
      <ToastProvider>
        <Toast open>
          <ToastTitle>未传 duration</ToastTitle>
        </Toast>
        <ToastViewport />
      </ToastProvider>
    )
    expect(queryToast('-progress')).toBeNull()
  })

  it('视口暂停 / 恢复时同步进度动画，并转发 onPause / onResume', () => {
    const onPause = vi.fn()
    const onResume = vi.fn()

    renderToast(
      <Toast open duration={8000} onPause={onPause} onResume={onResume}>
        <ToastTitle>可暂停</ToastTitle>
      </Toast>
    )

    const region = screen.getByRole('region', { name: /Notifications/i })
    expect(queryToast('-progress')).toHaveStyle({ animationPlayState: 'running' })

    fireEvent.pointerMove(region)
    expect(onPause).toHaveBeenCalledTimes(1)
    expect(queryToast('-progress')).toHaveStyle({ animationPlayState: 'paused' })

    fireEvent.pointerLeave(region)
    expect(onResume).toHaveBeenCalledTimes(1)
    expect(queryToast('-progress')).toHaveStyle({ animationPlayState: 'running' })
  })

  it('未传入 onPause / onResume 时仍能切换进度条播放状态', () => {
    renderToast(
      <Toast open duration={8000}>
        <ToastTitle>无回调</ToastTitle>
      </Toast>
    )

    const region = screen.getByRole('region', { name: /Notifications/i })
    fireEvent.pointerMove(region)
    expect(queryToast('-progress')).toHaveStyle({ animationPlayState: 'paused' })

    fireEvent.pointerLeave(region)
    expect(queryToast('-progress')).toHaveStyle({ animationPlayState: 'running' })
  })
})

describe('Toast 子组件', () => {
  it('Title / Description / Action / Close 带标记、文案与无障碍属性', () => {
    renderToast(
      <Toast open>
        <ToastTitle className="extra-title">标题文本</ToastTitle>
        <ToastDescription className="extra-desc">描述文本</ToastDescription>
        <ToastAction altText="撤销刚才的操作" className="extra-action">
          撤销
        </ToastAction>
        <ToastClose className="extra-close" />
      </Toast>
    )

    const title = queryToast('-title')
    const description = queryToast('-description')
    const action = queryToast('-action')
    const close = queryToast('-close')

    expect(title).toHaveTextContent('标题文本')
    expect(title).toHaveClass('font-semibold', 'extra-title')
    expect(description).toHaveTextContent('描述文本')
    expect(description).toHaveClass('select-text', 'extra-desc')
    expect(action).toHaveTextContent('撤销')
    expect(action).toHaveClass('extra-action')
    expect(close).toHaveAttribute('aria-label', '关闭提示')
    expect(close).toHaveAttribute('toast-close', '')
    expect(close).toHaveClass('extra-close')
  })
})
