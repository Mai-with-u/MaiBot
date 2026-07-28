import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { JargonList } from '../JargonList'

import type { ComponentProps } from 'react'
import type { Jargon } from '@/types/jargon'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** 构造一条完整的黑话数据，覆盖项通过 overrides 指定 */
function makeJargon(id: number, overrides: Partial<Jargon> = {}): Jargon {
  return {
    id,
    content: `黑话${id}`,
    meaning: `含义${id}`,
    session_id: `session-${id}`,
    session_ids: [`session-${id}`],
    chat_name: `聊天${id}`,
    chat_names: [`聊天${id}`],
    is_global: false,
    count: id * 10,
    is_jargon: true,
    is_legacy_empty_meaning: false,
    is_complete: false,
    created_by: 'AI',
    created_timestamp: '2026-01-01T00:00:00Z',
    updated_timestamp: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

type ListProps = ComponentProps<typeof JargonList>

/** 渲染列表并返回全部回调 spy，便于断言调用参数 */
function renderList(overrides: Partial<ListProps> = {}) {
  const props: ListProps = {
    jargons: [makeJargon(1), makeJargon(2)],
    loading: false,
    total: 2,
    page: 1,
    pageSize: 20,
    selectedIds: new Set<number>(),
    onEdit: vi.fn(),
    onDelete: vi.fn(),
    onToggleSelect: vi.fn(),
    onToggleSelectAll: vi.fn(),
    onPageChange: vi.fn(),
    onJumpToPage: vi.fn(),
    ...overrides,
  }
  render(<JargonList {...props} />)
  return props
}

function getTable() {
  return screen.getByRole('table', { name: '黑话列表' })
}

describe('JargonList 渲染', () => {
  it('loading 时桌面与移动端各显示一个加载指示，不显示暂无数据', () => {
    renderList({ jargons: [], loading: true, total: 0 })
    expect(screen.getAllByRole('status', { name: '加载中' })).toHaveLength(2)
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument()
  })

  it('空列表时显示暂无数据，且 total 为 0 时不渲染分页区', () => {
    renderList({ jargons: [], total: 0 })
    expect(screen.getAllByText('暂无数据')).toHaveLength(2)
    expect(screen.queryByText(/共 \d+ 条记录/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '跳转' })).not.toBeInTheDocument()
  })

  it('表格行渲染内容、含义、聊天名与遇见次数', () => {
    renderList()
    const table = getTable()
    expect(within(table).getByText('黑话1')).toBeInTheDocument()
    expect(within(table).getByText('含义1')).toBeInTheDocument()
    expect(within(table).getByText('聊天1')).toBeInTheDocument()
    expect(within(table).getByText('10')).toBeInTheDocument()
    expect(within(table).getByText('黑话2')).toBeInTheDocument()
  })

  it('多个聊天名用顿号连接；无含义显示占位符', () => {
    renderList({
      jargons: [makeJargon(1, { chat_names: ['群甲', '群乙'], meaning: null })],
      total: 1,
    })
    const table = getTable()
    expect(within(table).getByText('群甲、群乙')).toBeInTheDocument()
    expect(within(table).getByText('-')).toBeInTheDocument()
  })

  it('聊天名缺失时回退显示 session_id', () => {
    renderList({
      jargons: [makeJargon(1, { chat_names: [], chat_name: null })],
      total: 1,
    })
    expect(within(getTable()).getByText('session-1')).toBeInTheDocument()
  })

  it('全局黑话显示地球图标，手动创建显示对应标记与徽章', () => {
    renderList({
      jargons: [makeJargon(1, { is_global: true, created_by: 'MANUAL' })],
      total: 1,
    })
    // 桌面端：带 title 的图标容器 + aria-label 的对勾
    expect(screen.getByTitle('全局黑话')).toBeInTheDocument()
    expect(within(getTable()).getByLabelText('手动创建')).toBeInTheDocument()
    // 「手动」同时出现在表头列名与移动端卡片徽章
    expect(screen.getAllByText('手动')).toHaveLength(2)
  })

  it('移动端卡片按 is_jargon 与旧数据标记渲染状态徽章', () => {
    renderList({
      jargons: [
        makeJargon(1, { is_jargon: true, is_legacy_empty_meaning: true }),
        makeJargon(2, { is_jargon: false }),
      ],
    })
    expect(screen.getByText('是黑话')).toBeInTheDocument()
    expect(screen.getByText('无黑话')).toBeInTheDocument()
    expect(screen.getByText('旧数据')).toBeInTheDocument()
  })

  it('hideChatColumn 时不渲染聊天列表头与卡片聊天行', () => {
    renderList({ hideChatColumn: true })
    expect(screen.queryByRole('columnheader', { name: '聊天' })).not.toBeInTheDocument()
    expect(screen.queryByText(/^聊天:/)).not.toBeInTheDocument()
  })
})

describe('JargonList 交互', () => {
  it('勾选行复选框触发 onToggleSelect，表头复选框触发 onToggleSelectAll', async () => {
    const user = userEvent.setup()
    const props = renderList({ selectedIds: new Set<number>([1, 2]) })
    const checkboxes = within(getTable()).getAllByRole('checkbox')
    // 全部选中时表头复选框呈选中态
    expect(checkboxes[0]).toHaveAttribute('aria-checked', 'true')
    await user.click(checkboxes[1])
    expect(props.onToggleSelect).toHaveBeenCalledWith(1)
    await user.click(checkboxes[0])
    expect(props.onToggleSelectAll).toHaveBeenCalledTimes(1)
  })

  it('编辑与删除按钮回调收到对应黑话对象', async () => {
    const user = userEvent.setup()
    const props = renderList()
    const table = getTable()
    await user.click(within(table).getAllByRole('button', { name: '查看或编辑黑话' })[0])
    expect(props.onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }))
    await user.click(within(table).getAllByRole('button', { name: '删除黑话' })[1])
    expect(props.onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }))
  })

  it('单页时显示分页统计且上一页/下一页均禁用', () => {
    renderList()
    expect(screen.getByText('共 2 条记录，第 1 / 1 页')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  })

  it('中间页时上一页/下一页可用并回调相邻页码', async () => {
    const user = userEvent.setup()
    const props = renderList({ total: 60, page: 2 })
    expect(screen.getByText('共 60 条记录，第 2 / 3 页')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '上一页' }))
    expect(props.onPageChange).toHaveBeenCalledWith(1)
    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(props.onPageChange).toHaveBeenCalledWith(3)
  })

  it('跳转输入为空时按钮禁用；输入后点击回调字符串页码并清空输入', async () => {
    const user = userEvent.setup()
    const props = renderList({ total: 60, page: 2 })
    const jumpButton = screen.getByRole('button', { name: '跳转' })
    expect(jumpButton).toBeDisabled()
    const input = screen.getByPlaceholderText('2')
    await user.type(input, '3')
    expect(jumpButton).toBeEnabled()
    await user.click(jumpButton)
    expect(props.onJumpToPage).toHaveBeenCalledWith('3')
    expect(input).toHaveValue(null)
  })

  it('跳转输入框按回车同样触发 onJumpToPage', async () => {
    const user = userEvent.setup()
    const props = renderList({ total: 60, page: 1 })
    await user.type(screen.getByPlaceholderText('1'), '2{Enter}')
    expect(props.onJumpToPage).toHaveBeenCalledWith('2')
  })
})
