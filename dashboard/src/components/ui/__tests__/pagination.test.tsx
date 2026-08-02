import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from '../pagination'

describe('Pagination 容器', () => {
  it('渲染为 navigation 地标并带 aria-label', () => {
    render(<Pagination data-testid="pagination" />)

    const nav = screen.getByRole('navigation')
    expect(nav).toHaveAttribute('aria-label', 'pagination')
    expect(nav).toHaveClass('mx-auto', 'flex', 'w-full', 'justify-center')
  })

  it('合并自定义 className', () => {
    render(<Pagination className="mt-4" />)

    expect(screen.getByRole('navigation')).toHaveClass('mt-4', 'flex')
  })
})

describe('PaginationContent / PaginationItem', () => {
  it('渲染 ul > li 列表结构', () => {
    render(
      <PaginationContent data-testid="content">
        <PaginationItem data-testid="item">第 1 页</PaginationItem>
      </PaginationContent>
    )

    const content = screen.getByTestId('content')
    expect(content.tagName).toBe('UL')
    expect(content).toHaveClass('flex', 'flex-row', 'items-center', 'gap-1')

    const item = screen.getByTestId('item')
    expect(item.tagName).toBe('LI')
    expect(item).toHaveTextContent('第 1 页')
  })
})

describe('PaginationLink', () => {
  it('激活状态标记 aria-current=page 并使用 outline 变体', () => {
    render(
      <PaginationLink isActive href="#page-2">
        2
      </PaginationLink>
    )

    const link = screen.getByRole('link', { name: '2' })
    expect(link).toHaveAttribute('aria-current', 'page')
    // outline 变体带边框，区分于 ghost 变体
    expect(link).toHaveClass('border', 'border-input')
    // 默认 icon 尺寸
    expect(link).toHaveClass('h-9', 'w-9')
  })

  it('非激活状态不带 aria-current 且使用 ghost 变体', () => {
    render(<PaginationLink href="#page-3">3</PaginationLink>)

    const link = screen.getByRole('link', { name: '3' })
    expect(link).not.toHaveAttribute('aria-current')
    expect(link).not.toHaveClass('border-input')
  })

  it('支持覆盖 size 变体', () => {
    render(
      <PaginationLink href="#page-4" size="default">
        4
      </PaginationLink>
    )

    const link = screen.getByRole('link', { name: '4' })
    expect(link).toHaveClass('px-4')
    expect(link).not.toHaveClass('w-9')
  })
})

describe('PaginationPrevious / PaginationNext', () => {
  it('上一页按钮渲染中文文案与无障碍标签', () => {
    // aria-label 会覆盖可见文本成为可访问名称，因此按 aria-label 查询
    render(<PaginationPrevious href="#prev" />)

    const link = screen.getByRole('link', { name: 'Go to previous page' })
    expect(link).toHaveAttribute('aria-label', 'Go to previous page')
    expect(link).toHaveAttribute('href', '#prev')
    expect(link).toHaveTextContent('上一页')
  })

  it('下一页按钮渲染中文文案与无障碍标签', () => {
    render(<PaginationNext href="#next" />)

    const link = screen.getByRole('link', { name: 'Go to next page' })
    expect(link).toHaveAttribute('aria-label', 'Go to next page')
    expect(link).toHaveAttribute('href', '#next')
    expect(link).toHaveTextContent('下一页')
  })
})

describe('PaginationEllipsis', () => {
  it('对屏幕阅读器隐藏并保留 sr-only 说明文本', () => {
    render(<PaginationEllipsis data-testid="ellipsis" />)

    const ellipsis = screen.getByTestId('ellipsis')
    expect(ellipsis).toHaveAttribute('aria-hidden')
    expect(ellipsis).toHaveTextContent('More pages')
    expect(ellipsis.querySelector('.sr-only')).not.toBeNull()
  })
})
