import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpressionReviewer } from '../expression-reviewer'
import * as expressionApi from '@/lib/expression-api'
import type { BatchReviewItem, Expression } from '@/types/expression'

// toast 桩：用 hoisted 保证 vi.mock 工厂内能引用同一个实例
const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// 组件只消费这四个 API，全部打桩，避免真实请求
vi.mock('@/lib/expression-api', () => ({
  getReviewStats: vi.fn(),
  getReviewList: vi.fn(),
  batchReviewExpressions: vi.fn(),
  getChatList: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** 构造一条表达方式数据 */
function makeExpr(id: number, overrides: Partial<Expression> = {}): Expression {
  return {
    id,
    situation: `情景${id}`,
    style: `风格${id}`,
    last_active_time: 1_710_000_000,
    chat_id: 'chat-1',
    chat_name: null,
    create_date: 1_710_000_000,
    checked: false,
    modified_by: null,
    ...overrides,
  }
}

/** 构造审核列表响应 */
function makeListResponse(data: Expression[], total = data.length) {
  return { success: true, total, page: 1, page_size: 20, data }
}

/** 等待指定毫秒（用于确认"不应发生"的行为） */
function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms))
}

beforeEach(() => {
  vi.mocked(expressionApi.getReviewStats).mockResolvedValue({
    total: 10,
    unchecked: 6,
    passed: 4,
    ai_checked: 2,
    user_checked: 2,
  })
  vi.mocked(expressionApi.getReviewList).mockResolvedValue(
    makeListResponse([
      makeExpr(1),
      makeExpr(2, { create_date: null }),
      makeExpr(3, { checked: true, modified_by: 'user' }),
    ])
  )
  vi.mocked(expressionApi.getChatList).mockResolvedValue([
    {
      chat_id: 'chat-1',
      chat_name: '测试群聊',
      platform: 'qq',
      is_group: true,
      use_expression: true,
      enable_learning: true,
    },
  ])
  // 默认批量审核全部成功
  vi.mocked(expressionApi.batchReviewExpressions).mockImplementation(
    async (items: BatchReviewItem[]) => ({
      success: true,
      total: items.length,
      succeeded: items.length,
      failed: 0,
      results: items.map((item) => ({ id: item.id, success: true, message: 'ok' })),
    })
  )
})

describe('ExpressionReviewer 列表模式', () => {
  it('初始加载拉取列表/统计/聊天名称并渲染行内容', async () => {
    render(<ExpressionReviewer embedded mode="list" />)

    expect(await screen.findByText('情景1')).toBeInTheDocument()
    expect(expressionApi.getReviewList).toHaveBeenCalledWith({
      page: 1,
      page_size: 20,
      filter_type: 'unchecked',
      search: undefined,
    })
    expect(expressionApi.getReviewStats).toHaveBeenCalled()
    expect(expressionApi.getChatList).toHaveBeenCalled()

    // 统计数字渲染到三个筛选 tab 上
    expect(screen.getByText('(6)')).toBeInTheDocument()
    expect(screen.getByText('(4)')).toBeInTheDocument()
    expect(screen.getByText('(10)')).toBeInTheDocument()

    // chat_name 为空时回退到 getChatList 建立的映射
    expect((await screen.findAllByText('测试群聊')).length).toBeGreaterThan(0)
    // checked + modified_by=user 显示"人工通过"徽章
    expect(screen.getByText('人工通过')).toBeInTheDocument()
    // create_date 为空显示占位符
    expect(screen.getAllByText('-').length).toBeGreaterThan(0)
    // 总条数
    expect(screen.getByText('共 3 条')).toBeInTheDocument()
  })

  it('列表为空时显示空状态提示', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(makeListResponse([]))
    render(<ExpressionReviewer embedded mode="list" />)

    expect(await screen.findByText('没有找到表达方式')).toBeInTheDocument()
  })

  it('列表加载失败时弹出错误 toast', async () => {
    vi.mocked(expressionApi.getReviewList).mockRejectedValue(new Error('后端炸了'))
    render(<ExpressionReviewer embedded mode="list" />)

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载失败',
          description: '后端炸了',
          variant: 'destructive',
        })
      )
    )
  })

  it('单条通过：调用批量接口并刷新列表、触发 onReviewed', async () => {
    const onReviewed = vi.fn()
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" onReviewed={onReviewed} />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByTitle('通过')[0])

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: true, require_unchecked: true },
      ])
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '已通过', description: expect.stringContaining('#1') })
      )
    )
    // 成功后刷新列表（初始一次 + 刷新一次）
    await waitFor(() => expect(expressionApi.getReviewList).toHaveBeenCalledTimes(2))
    expect(onReviewed).toHaveBeenCalled()
  })

  it('单条拒绝返回失败结果时展示操作失败 toast', async () => {
    vi.mocked(expressionApi.batchReviewExpressions).mockResolvedValue({
      success: true,
      total: 1,
      succeeded: 0,
      failed: 1,
      results: [{ id: 1, success: false, message: '条目已被处理' }],
    })
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByTitle('拒绝')[0])

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: false, require_unchecked: true },
      ])
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '操作失败',
          description: '条目已被处理',
          variant: 'destructive',
        })
      )
    )
  })

  it('全选后批量通过：提交所有选中项并展示结果 toast', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    // 第一个 checkbox 是"全选当前页"
    await user.click(screen.getAllByRole('checkbox')[0])
    expect(screen.getByText('已全选当前页 (3 条)')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /批量通过/ }))

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: true, require_unchecked: true },
        { id: 2, approved: true, require_unchecked: true },
        { id: 3, approved: true, require_unchecked: true },
      ])
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量审核完成',
          description: '成功 3 条，失败 0 条',
          variant: 'default',
        })
      )
    )
  })

  it('取消选择后批量操作按钮消失', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    // 选中第一行（索引 0 是全选框，1 起是行选择框）
    await user.click(screen.getAllByRole('checkbox')[1])
    expect(screen.getByRole('button', { name: /批量通过/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '取消选择' }))
    expect(screen.queryByRole('button', { name: /批量通过/ })).not.toBeInTheDocument()
  })

  it('切换到已通过筛选：重新请求并显示改为拒绝按钮', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getByRole('tab', { name: /已通过/ }))

    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        filter_type: 'passed',
        search: undefined,
      })
    )
    expect((await screen.findAllByTitle('改为拒绝')).length).toBe(3)
  })

  it('搜索：输入关键字回车后带 search 参数重新请求', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.type(screen.getByPlaceholderText('搜索情景或风格...'), '测试{Enter}')

    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        filter_type: 'unchecked',
        search: '测试',
      })
    )
  })

  it('分页：点击页码与跳转输入均能翻页，非法页码不触发请求', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(
      makeListResponse([makeExpr(1), makeExpr(2), makeExpr(3)], 50)
    )
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    // total=50 / pageSize=20 => 3 页，点击第 2 页
    await user.click(screen.getByRole('link', { name: '2' }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2 })
      )
    )

    // 跳转输入框跳到第 3 页
    const jumpInput = screen.getByRole('spinbutton')
    await user.type(jumpInput, '3')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 3 })
      )
    )

    // 非法页码（超出总页数）不触发请求
    await user.type(jumpInput, '99')
    await user.click(screen.getByRole('button', { name: '跳转' }))
    expect(expressionApi.getReviewList).not.toHaveBeenCalledWith(
      expect.objectContaining({ page: 99 })
    )
  })

  it('非 embedded 时渲染弹窗标题，按 Escape 触发 onOpenChange(false)', async () => {
    const onOpenChange = vi.fn()
    const user = userEvent.setup()
    render(<ExpressionReviewer open onOpenChange={onOpenChange} />)

    expect(await screen.findByText('表达方式审核')).toBeInTheDocument()
    expect(screen.getByText(/审核麦麦学习到的表达方式/)).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('ExpressionReviewer 模式切换', () => {
  it('未指定 mode 时显示切换器，点击精选进入快速审核模式', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded />)
    await screen.findByText('情景1')

    expect(screen.getByRole('button', { name: /列表模式/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /精选/ }))

    // 快速模式以随机顺序加载待浏览数据
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ filter_type: 'unchecked', order: 'random' })
      )
    )
    expect(await screen.findByRole('tab', { name: /待浏览/ })).toBeInTheDocument()
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('指定 mode 时不渲染模式切换器', async () => {
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    expect(screen.queryByRole('button', { name: /列表模式/ })).not.toBeInTheDocument()
  })
})

describe('ExpressionReviewer 快速审核模式', () => {
  it('初始加载渲染卡片堆叠、风格徽章与快捷键提示', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(
      makeListResponse([makeExpr(1, { style: '幽默，可爱' }), makeExpr(2), makeExpr(3)])
    )
    render(<ExpressionReviewer embedded mode="quick" />)

    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    expect(expressionApi.getReviewList).toHaveBeenCalledWith({
      page: 1,
      page_size: 20,
      filter_type: 'unchecked',
      order: 'random',
      exclude_ids: undefined,
    })

    // 风格按中英文逗号拆分为多个徽章
    expect(screen.getByText('幽默')).toBeInTheDocument()
    expect(screen.getByText('可爱')).toBeInTheDocument()
    // 快捷键提示
    expect(screen.getByText('拖拽卡片滑动审核')).toBeInTheDocument()
    expect(screen.getByText('上一条')).toBeInTheDocument()
    expect(screen.getByText('下一条')).toBeInTheDocument()
  })

  it('按右方向键通过当前卡片并从堆叠中移除', async () => {
    const onReviewed = vi.fn()
    render(<ExpressionReviewer embedded mode="quick" onReviewed={onReviewed} />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    fireEvent.keyDown(window, { key: 'ArrowRight' })

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: true, require_unchecked: true },
      ])
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '已通过' }))
    )
    // 300ms 动画后当前卡片被移除，焦点移到下一条
    await waitFor(() => expect(screen.queryByText('情景1')).not.toBeInTheDocument())
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-2')
    expect(onReviewed).toHaveBeenCalled()
  })

  it('按左方向键拒绝当前卡片', async () => {
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    fireEvent.keyDown(window, { key: 'ArrowLeft' })

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: false, require_unchecked: true },
      ])
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '已删除' }))
    )
  })

  it('上下方向键在卡片间导航', async () => {
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    fireEvent.keyDown(window, { key: 'ArrowDown' })
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-2')
    )

    fireEvent.keyDown(window, { key: 'ArrowUp' })
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
    )

    // 审核接口不应被触发
    expect(expressionApi.batchReviewExpressions).not.toHaveBeenCalled()
  })

  it('审核返回冲突时展示冲突提示并延迟刷新数据', async () => {
    vi.mocked(expressionApi.batchReviewExpressions).mockResolvedValue({
      success: true,
      total: 1,
      succeeded: 0,
      failed: 1,
      results: [{ id: 1, success: false, message: '已被后台处理' }],
    })
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))
    const initialCalls = vi.mocked(expressionApi.getReviewList).mock.calls.length

    fireEvent.keyDown(window, { key: 'ArrowRight' })

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '数据冲突', variant: 'destructive' })
      )
    )
    // 冲突遮罩文案
    expect(await screen.findByText('数据已更新')).toBeInTheDocument()

    // 1.5 秒后重新拉取当前页
    await waitFor(
      () =>
        expect(vi.mocked(expressionApi.getReviewList).mock.calls.length).toBeGreaterThan(
          initialCalls
        ),
      { timeout: 3000 }
    )
  })

  it('已通过筛选下右滑（通过）被禁止', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="quick" />)
    await screen.findByRole('listbox')

    await user.click(screen.getByRole('tab', { name: /已通过/ }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ filter_type: 'passed', order: 'latest' })
      )
    )
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
    )

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await sleep(80)
    expect(expressionApi.batchReviewExpressions).not.toHaveBeenCalled()
  })

  it('已通过筛选下左滑改为拒绝且不要求未审核状态', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="quick" />)
    await screen.findByRole('listbox')

    await user.click(screen.getByRole('tab', { name: /已通过/ }))
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
    )

    fireEvent.keyDown(window, { key: 'ArrowLeft' })

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: false, require_unchecked: false },
      ])
    )
  })

  it('没有数据时显示全部审核完成', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(makeListResponse([]))
    render(<ExpressionReviewer embedded mode="quick" />)

    expect(await screen.findByText('全部审核完成！')).toBeInTheDocument()
    expect(screen.getByText('当前筛选条件下没有待处理的项目')).toBeInTheDocument()
  })

  it('点击刷新按钮重新加载数据和统计', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="quick" />)
    await screen.findByRole('listbox')
    const listCalls = vi.mocked(expressionApi.getReviewList).mock.calls.length
    const statsCalls = vi.mocked(expressionApi.getReviewStats).mock.calls.length

    await user.click(screen.getByRole('button', { name: /刷新/ }))

    await waitFor(() =>
      expect(vi.mocked(expressionApi.getReviewList).mock.calls.length).toBeGreaterThan(listCalls)
    )
    expect(vi.mocked(expressionApi.getReviewStats).mock.calls.length).toBeGreaterThan(statsCalls)
  })

  it('拖拽未超过阈值时回弹且不触发审核', async () => {
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    // 当前卡片是 aria-selected=true 的 option
    const card = screen.getByRole('option', { selected: true })
    fireEvent.mouseDown(card, { clientX: 100, clientY: 100 })
    fireEvent.mouseMove(card, { clientX: 130, clientY: 100 })
    fireEvent.mouseUp(card)

    await sleep(80)
    expect(expressionApi.batchReviewExpressions).not.toHaveBeenCalled()
    // 卡片仍在原位
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
  })
})
