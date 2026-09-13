import type { ComponentProps, ReactNode } from 'react'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Pagination } from '../Pagination'

vi.mock('@/components/ui/select', () => ({
  Select: ({
    children,
    value,
    onValueChange,
  }: {
    children: ReactNode
    value: string
    onValueChange: (value: string) => void
  }) => (
    <div data-testid="select-root" data-value={value}>
      {children}
      <button type="button" onClick={() => onValueChange('50')}>
        切换每页数量
      </button>
    </div>
  ),
  SelectTrigger: ({ children, ...props }: { children: ReactNode }) => <div {...props}>{children}</div>,
  SelectValue: () => <span>当前选项</span>,
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

function renderPagination(overrides: Partial<ComponentProps<typeof Pagination>> = {}) {
  const onPageChange = vi.fn()
  const onPageSizeChange = vi.fn()
  const onJumpToPageChange = vi.fn()
  const onJumpToPage = vi.fn()
  const view = render(
    <Pagination
      page={1}
      pageSize={10}
      totalItems={25}
      jumpToPage=""
      onPageChange={onPageChange}
      onPageSizeChange={onPageSizeChange}
      onJumpToPageChange={onJumpToPageChange}
      onJumpToPage={onJumpToPage}
      {...overrides}
    />
  )
  return { ...view, onPageChange, onPageSizeChange, onJumpToPageChange, onJumpToPage }
}

afterEach(() => cleanup())

describe('Pagination 缺口', () => {
  it('首页与末页禁用对应翻页按钮，末页范围截断到总数', () => {
    const { rerender, onPageChange } = renderPagination({ page: 1, jumpToPage: '1' })

    expect(screen.getByRole('button', { name: '第一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '最后一页' })).toBeEnabled()
    expect(screen.getByText('1 到 10 条，共 25 条')).toBeInTheDocument()

    rerender(
      <Pagination
        page={3}
        pageSize={10}
        totalItems={25}
        jumpToPage="3"
        onPageChange={onPageChange}
        onPageSizeChange={vi.fn()}
        onJumpToPageChange={vi.fn()}
        onJumpToPage={vi.fn()}
      />
    )
    expect(screen.getByText('21 到 25 条，共 25 条')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '最后一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '第一页' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '上一页' })).toBeEnabled()
  })

  it('空跳转禁用按钮，点击跳转与非回车键分别处理', async () => {
    const user = userEvent.setup()
    const { rerender, onJumpToPage, onJumpToPageChange } = renderPagination({ jumpToPage: '' })

    expect(screen.getByRole('button', { name: '跳转' })).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('spinbutton'), { key: 'Escape' })
    expect(onJumpToPage).not.toHaveBeenCalled()

    rerender(
      <Pagination
        page={1}
        pageSize={10}
        totalItems={25}
        jumpToPage="2"
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
        onJumpToPageChange={onJumpToPageChange}
        onJumpToPage={onJumpToPage}
      />
    )
    await user.click(screen.getByRole('button', { name: '跳转' }))
    expect(onJumpToPage).toHaveBeenCalledOnce()
  })

  it('修改每页数量在未提供清空选择回调时仍重置页码', async () => {
    const user = userEvent.setup()
    const { onPageChange, onPageSizeChange } = renderPagination({ page: 2 })

    await user.click(screen.getByRole('button', { name: '切换每页数量' }))
    expect(onPageSizeChange).toHaveBeenCalledWith(50)
    expect(onPageChange).toHaveBeenCalledWith(1)
  })
})
