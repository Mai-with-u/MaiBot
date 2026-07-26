/**
 * ExpressionList 行为测试：
 * 覆盖空态 / 行渲染与精选徽标 / 聊天列隐藏 / 勾选与全选回调 /
 * 行操作按钮回调与审核切换 pending 态 / 精选筛选下拉 / 分页交互与页码校验。
 */
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpressionList } from '../ExpressionList'

import type { Expression } from '@/types/expression'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  toastMock.mockClear()
  // Radix DropdownMenu 在 jsdom 下需要 pointer-capture 相关桩
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
})

function makeExpression(overrides: Partial<Expression> = {}): Expression {
  return {
    id: 1,
    situation: '被夸奖时',
    style: '害羞回应',
    last_active_time: 1_710_000_000,
    chat_id: 'chat-1',
    chat_name: null,
    create_date: 1_710_000_000,
    checked: false,
    modified_by: null,
    ...overrides,
  }
}

type ListProps = Parameters<typeof ExpressionList>[0]

function makeProps(overrides: Partial<ListProps> = {}): ListProps {
  return {
    expressions: [makeExpression()],
    loading: false,
    total: 1,
    page: 1,
    pageSize: 20,
    selectedIds: new Set<number>(),
    chatNameMap: new Map([['chat-1', '测试群']]),
    reviewFilter: 'all',
    onEdit: vi.fn(),
    onViewDetail: vi.fn(),
    onDelete: vi.fn(),
    onReviewFilterChange: vi.fn(),
    onToggleReviewStatus: vi.fn(async () => {}),
    onToggleSelect: vi.fn(),
    onToggleSelectAll: vi.fn(),
    onPageChange: vi.fn(),
    onJumpToPage: vi.fn(),
    ...overrides,
  }
}

function getDesktopTable() {
  return screen.getByRole('table', { name: '表达方式列表' })
}

describe('ExpressionList 渲染', () => {
  it('无数据时桌面与移动视图都显示暂无数据', () => {
    render(<ExpressionList {...makeProps({ expressions: [], total: 0 })} />)
    expect(screen.getAllByText('暂无数据')).toHaveLength(2)
    // total 为 0 时不渲染分页
    expect(screen.queryByRole('button', { name: '下一页' })).not.toBeInTheDocument()
  })

  it('渲染情境、风格与 chatNameMap 回退的聊天名称', () => {
    render(<ExpressionList {...makeProps()} />)
    const table = getDesktopTable()
    expect(within(table).getByText('被夸奖时')).toBeInTheDocument()
    expect(within(table).getByText('害羞回应')).toBeInTheDocument()
    expect(within(table).getByText('测试群')).toBeInTheDocument()
  })

  it('checked 且 modified_by=user 时显示精选徽标，其余不显示', () => {
    const props = makeProps({
      expressions: [
        makeExpression({ id: 1, checked: true, modified_by: 'user' }),
        makeExpression({ id: 2, situation: 'AI 审核', checked: true, modified_by: 'ai' }),
      ],
      total: 2,
    })
    render(<ExpressionList {...props} />)
    const table = getDesktopTable()
    // 仅人工精选行有徽标（表头「精选」列名与徽标文本相同，故按行内查找）
    const badges = within(table).getAllByText('精选')
    // 表头 1 个 + 人工精选行 1 个
    expect(badges).toHaveLength(2)
  })

  it('hideChatColumn=true 时不渲染聊天列', () => {
    render(<ExpressionList {...makeProps({ hideChatColumn: true })} />)
    const table = getDesktopTable()
    expect(within(table).queryByText('聊天')).not.toBeInTheDocument()
    expect(within(table).queryByText('测试群')).not.toBeInTheDocument()
  })
})

describe('ExpressionList 勾选与操作', () => {
  it('行复选框触发 onToggleSelect，表头复选框触发 onToggleSelectAll', async () => {
    const user = userEvent.setup()
    const props = makeProps()
    render(<ExpressionList {...props} />)
    const table = getDesktopTable()
    const checkboxes = within(table).getAllByRole('checkbox')
    // 第一个是表头全选，第二个是数据行
    await user.click(checkboxes[1])
    expect(props.onToggleSelect).toHaveBeenCalledWith(1)
    await user.click(checkboxes[0])
    expect(props.onToggleSelectAll).toHaveBeenCalledTimes(1)
  })

  it('编辑 / 查看详情 / 删除按钮回调携带对应表达方式', async () => {
    const user = userEvent.setup()
    const props = makeProps()
    render(<ExpressionList {...props} />)
    const table = getDesktopTable()
    await user.click(within(table).getByRole('button', { name: '编辑' }))
    expect(props.onEdit).toHaveBeenCalledWith(props.expressions[0])
    await user.click(within(table).getByTitle('查看详情'))
    expect(props.onViewDetail).toHaveBeenCalledWith(props.expressions[0])
    await user.click(within(table).getByRole('button', { name: '删除' }))
    expect(props.onDelete).toHaveBeenCalledWith(props.expressions[0])
  })

  it('审核切换：未精选显示通过按钮，pending 期间禁用，完成后恢复', async () => {
    const user = userEvent.setup()
    let resolveToggle: () => void = () => {}
    const onToggleReviewStatus = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveToggle = resolve
        })
    )
    const props = makeProps({ onToggleReviewStatus })
    render(<ExpressionList {...props} />)
    const table = getDesktopTable()

    const approveButton = within(table).getByRole('button', { name: '通过' })
    await user.click(approveButton)
    expect(onToggleReviewStatus).toHaveBeenCalledWith(props.expressions[0])
    // 未完成前按钮禁用
    expect(approveButton).toBeDisabled()

    await act(async () => {
      resolveToggle()
    })
    expect(approveButton).toBeEnabled()
  })

  it('人工精选行显示拒绝按钮', () => {
    render(
      <ExpressionList
        {...makeProps({ expressions: [makeExpression({ checked: true, modified_by: 'user' })] })}
      />
    )
    const table = getDesktopTable()
    expect(within(table).getByRole('button', { name: '拒绝' })).toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: '通过' })).not.toBeInTheDocument()
  })

  it('精选筛选下拉选择已精选时回调 user_checked', async () => {
    const user = userEvent.setup()
    const props = makeProps()
    render(<ExpressionList {...props} />)
    await user.click(screen.getByRole('button', { name: '筛选精选状态' }))
    await user.click(await screen.findByRole('menuitemradio', { name: '已精选' }))
    expect(props.onReviewFilterChange).toHaveBeenCalledWith('user_checked')
  })
})

describe('ExpressionList 分页', () => {
  function paginatedProps(overrides: Partial<ListProps> = {}) {
    return makeProps({ total: 45, page: 1, pageSize: 20, ...overrides })
  }

  it('展示总数与页码信息，首页时上一页/首页禁用', () => {
    render(<ExpressionList {...paginatedProps()} />)
    expect(screen.getByText('45 条 · 1/3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '首页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled()
  })

  it('下一页与末页按钮回调对应页码', async () => {
    const user = userEvent.setup()
    const props = paginatedProps()
    render(<ExpressionList {...props} />)
    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(props.onPageChange).toHaveBeenCalledWith(2)
    await user.click(screen.getByRole('button', { name: '末页' }))
    expect(props.onPageChange).toHaveBeenCalledWith(3)
  })

  it('输入合法页码点击跳转触发 onJumpToPage', async () => {
    const user = userEvent.setup()
    const props = paginatedProps()
    render(<ExpressionList {...props} />)
    await user.type(screen.getByRole('spinbutton'), '2')
    await user.click(screen.getByRole('button', { name: '跳' }))
    expect(props.onJumpToPage).toHaveBeenCalledWith('2')
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('输入超范围页码时弹无效页码提示且不跳转', async () => {
    const user = userEvent.setup()
    const props = paginatedProps()
    render(<ExpressionList {...props} />)
    await user.type(screen.getByRole('spinbutton'), '9')
    await user.click(screen.getByRole('button', { name: '跳' }))
    expect(props.onJumpToPage).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '无效的页码',
        description: '请输入1-3之间的页码',
        variant: 'destructive',
      })
    )
  })
})
