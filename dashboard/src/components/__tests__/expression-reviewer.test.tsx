import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpressionReviewer } from '../expression-reviewer'
import * as expressionApi from '@/lib/expression-api'
import type { BatchReviewItem, Expression } from '@/types/expression'

// toast 桩：用 hoisted 保证 vi.mock 工厂内能引用同一个实例
const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// jsdom 下 react-spring 的 x.get() 不会立刻跟上 immediate start，
// 拖拽结束判定依赖 get()，这里同步记录最近一次 x，不改变动画样式对象。
vi.mock('@react-spring/web', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@react-spring/web')>()
  return {
    ...actual,
    useSpring: ((init: unknown) => {
      const [spring, api] = actual.useSpring(init as never)
      let latestX = 0
      const originalGet = spring.x.get.bind(spring.x)
      spring.x.get = () => {
        const animatedX = Number(originalGet()) || 0
        return Math.abs(latestX) > Math.abs(animatedX) ? latestX : animatedX
      }
      const trackX = (fn: (cfg: { x?: number }) => unknown) => (cfg: { x?: number }) => {
        if (cfg && typeof cfg.x === 'number') {
          latestX = cfg.x
        }
        return fn(cfg)
      }
      return [
        spring,
        {
          ...api,
          start: trackX(api.start.bind(api)),
          set: trackX(api.set.bind(api)),
        },
      ]
    }) as typeof actual.useSpring,
  }
})

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

/** 分页导航里的上一页/下一页（无无障碍名称，仅图标） */
function getPaginationNavButtons() {
  const nav = screen.getByRole('navigation', { name: 'pagination' })
  const buttons = within(nav).getAllByRole('button')
  return { prev: buttons[0], next: buttons[buttons.length - 1] }
}

/** 列表工具栏里的搜索/刷新图标按钮 */
function getListToolbarIconButtons() {
  const input = screen.getByPlaceholderText('搜索情景或风格...')
  const group = input.parentElement?.nextElementSibling
  if (!group) {
    throw new Error('未找到搜索工具栏')
  }
  const buttons = within(group as HTMLElement).getAllByRole('button')
  return { search: buttons[0], refresh: buttons[1] }
}

/** 快速审核移动端圆形通过/拒绝按钮 */
function getQuickMobileButtons() {
  const bar = document.querySelector('.z-50.flex.shrink-0.items-center.gap-8')
  if (!bar) {
    throw new Error('未找到移动端审核按钮栏')
  }
  const buttons = within(bar as HTMLElement).getAllByRole('button')
  return { reject: buttons[0], approve: buttons[1] }
}

beforeEach(() => {
  // Radix Select 在 jsdom 下需要 pointer-capture 桩
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }

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

  it('全选后再点一次取消全选，批量按钮随之消失', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    const selectAll = screen.getAllByRole('checkbox')[0]
    await user.click(selectAll)
    expect(screen.getByText('已全选当前页 (3 条)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /批量通过/ })).toBeInTheDocument()

    await user.click(selectAll)
    expect(screen.getByText('全选当前页 (3 条)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /批量通过/ })).not.toBeInTheDocument()
  })

  it('全选后批量拒绝：提交 approved=false 并刷新列表', async () => {
    const onReviewed = vi.fn()
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" onReviewed={onReviewed} />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByRole('checkbox')[0])
    await user.click(screen.getByRole('button', { name: /批量拒绝/ }))

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: false, require_unchecked: true },
        { id: 2, approved: false, require_unchecked: true },
        { id: 3, approved: false, require_unchecked: true },
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
    expect(onReviewed).toHaveBeenCalled()
  })

  it('批量审核部分失败时 toast 为 destructive', async () => {
    vi.mocked(expressionApi.batchReviewExpressions).mockResolvedValue({
      success: true,
      total: 3,
      succeeded: 2,
      failed: 1,
      results: [
        { id: 1, success: true, message: 'ok' },
        { id: 2, success: true, message: 'ok' },
        { id: 3, success: false, message: '冲突' },
      ],
    })
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByRole('checkbox')[0])
    await user.click(screen.getByRole('button', { name: /批量通过/ }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量审核完成',
          description: '成功 2 条，失败 1 条',
          variant: 'destructive',
        })
      )
    )
  })

  it('批量审核接口抛错时提示批量审核失败', async () => {
    vi.mocked(expressionApi.batchReviewExpressions).mockRejectedValue(new Error('批量超时'))
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByRole('checkbox')[0])
    await user.click(screen.getByRole('button', { name: /批量通过/ }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量审核失败',
          description: '批量超时',
          variant: 'destructive',
        })
      )
    )
  })

  it('批量审核抛非 Error 时使用未知错误文案', async () => {
    vi.mocked(expressionApi.batchReviewExpressions).mockRejectedValue('oops')
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByRole('checkbox')[0])
    await user.click(screen.getByRole('button', { name: /批量拒绝/ }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量审核失败',
          description: '未知错误',
          variant: 'destructive',
        })
      )
    )
  })

  it('单条审核接口抛错时提示操作失败', async () => {
    vi.mocked(expressionApi.batchReviewExpressions).mockRejectedValue(new Error('单条失败'))
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByTitle('通过')[0])

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '操作失败',
          description: '单条失败',
          variant: 'destructive',
        })
      )
    )
  })

  it('单条审核抛非 Error 或空 results 时回退到未知错误', async () => {
    vi.mocked(expressionApi.batchReviewExpressions)
      .mockRejectedValueOnce('raw-fail')
      .mockResolvedValueOnce({
        success: true,
        total: 1,
        succeeded: 0,
        failed: 1,
        results: [],
      })
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getAllByTitle('通过')[0])
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '操作失败',
          description: '未知错误',
          variant: 'destructive',
        })
      )
    )

    await user.click(screen.getAllByTitle('拒绝')[0])
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '操作失败',
          description: '未知错误',
          variant: 'destructive',
        })
      )
    )
  })

  it('切换到全部筛选：显示两种批量按钮且不要求未审核', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getByRole('tab', { name: /全部/ }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        filter_type: 'all',
        search: undefined,
      })
    )
    await screen.findByText('情景1')

    // 已通过条目只显示「改为拒绝」，未审核显示通过+拒绝
    expect(screen.getAllByTitle('通过')).toHaveLength(2)
    expect(screen.getAllByTitle('拒绝')).toHaveLength(2)
    expect(screen.getAllByTitle('改为拒绝')).toHaveLength(1)

    await user.click(screen.getAllByRole('checkbox')[0])
    expect(screen.getByRole('button', { name: /批量通过/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /批量拒绝/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /批量通过/ }))
    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: true, require_unchecked: false },
        { id: 2, approved: true, require_unchecked: false },
        { id: 3, approved: true, require_unchecked: false },
      ])
    )
  })

  it('已通过筛选下批量改为拒绝且 require_unchecked 为 false', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getByRole('tab', { name: /已通过/ }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ filter_type: 'passed' })
      )
    )
    await screen.findByText('情景1')

    await user.click(screen.getAllByRole('checkbox')[0])
    expect(screen.getByRole('button', { name: /批量改为拒绝/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /批量通过/ })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /批量改为拒绝/ }))
    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: false, require_unchecked: false },
        { id: 2, approved: false, require_unchecked: false },
        { id: 3, approved: false, require_unchecked: false },
      ])
    )
  })

  it('全部筛选下对已通过条目点改为拒绝', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getByRole('tab', { name: /全部/ }))
    await screen.findByTitle('改为拒绝')

    await user.click(screen.getByTitle('改为拒绝'))
    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 3, approved: false, require_unchecked: false },
      ])
    )
  })

  it('总页数超过 7 时显示省略号，跳转中间页后两侧都有省略', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(
      makeListResponse([makeExpr(1), makeExpr(2)], 200)
    )
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    // 10 页：首页渲染 1、2、…、10
    expect(screen.getByRole('link', { name: '1' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '2' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '10' })).toBeInTheDocument()
    expect(screen.getByText('More pages')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '5' })).not.toBeInTheDocument()

    const jumpInput = screen.getByRole('spinbutton')
    await user.type(jumpInput, '5{Enter}')
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 5 })
      )
    )
    expect(screen.getByRole('link', { name: '4' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '5' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '6' })).toBeInTheDocument()
    expect(screen.getAllByText('More pages').length).toBeGreaterThan(1)
  })

  it('上一页/下一页按钮翻页，首页时上一页禁用', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(
      makeListResponse([makeExpr(1)], 50)
    )
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    const { prev, next } = getPaginationNavButtons()
    expect(prev).toBeDisabled()
    expect(next).toBeEnabled()

    await user.click(next)
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2 })
      )
    )

    const afterNext = getPaginationNavButtons()
    expect(afterNext.prev).toBeEnabled()
    await user.click(afterNext.prev)
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1 })
      )
    )
  })

  it('修改每页条数会重置到第一页并按新 page_size 请求', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(
      makeListResponse([makeExpr(1)], 50)
    )
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getByRole('link', { name: '2' }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, page_size: 20 })
      )
    )

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '50' }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith({
        page: 1,
        page_size: 50,
        filter_type: 'unchecked',
        search: undefined,
      })
    )
  })

  it('点击搜索按钮带关键字请求，刷新按钮重新拉取列表和统计', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')
    const statsCalls = vi.mocked(expressionApi.getReviewStats).mock.calls.length

    await user.type(screen.getByPlaceholderText('搜索情景或风格...'), '可爱')
    const { search, refresh } = getListToolbarIconButtons()
    await user.click(search)

    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        filter_type: 'unchecked',
        search: '可爱',
      })
    )

    await user.click(refresh)
    await waitFor(() =>
      expect(vi.mocked(expressionApi.getReviewStats).mock.calls.length).toBeGreaterThan(statsCalls)
    )
  })

  it('从第 2 页切换筛选会重置回第 1 页', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(
      makeListResponse([makeExpr(1)], 50)
    )
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="list" />)
    await screen.findByText('情景1')

    await user.click(screen.getByRole('link', { name: '2' }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, filter_type: 'unchecked' })
      )
    )

    await user.click(screen.getByRole('tab', { name: /已通过/ }))
    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith({
        page: 1,
        page_size: 20,
        filter_type: 'passed',
        search: undefined,
      })
    )
  })

  it('列表加载失败且非 Error 时使用兜底文案', async () => {
    vi.mocked(expressionApi.getReviewList).mockRejectedValue('boom')
    render(<ExpressionReviewer embedded mode="list" />)

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载失败',
          description: '无法加载列表',
          variant: 'destructive',
        })
      )
    )
    expect(await screen.findByText('没有找到表达方式')).toBeInTheDocument()
  })

  it('统计与聊天名称加载失败只打日志，列表仍正常渲染', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(expressionApi.getReviewStats).mockRejectedValue(new Error('统计挂了'))
    vi.mocked(expressionApi.getChatList).mockRejectedValue(new Error('聊天挂了'))
    render(<ExpressionReviewer embedded mode="list" />)

    expect(await screen.findByText('情景1')).toBeInTheDocument()
    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith('加载统计失败:', expect.any(Error))
      expect(errorSpy).toHaveBeenCalledWith('加载聚天名称失败:', expect.any(Error))
    })
    errorSpy.mockRestore()
  })

  it('优先使用条目自带 chat_name，否则回退 chat_id', async () => {
    vi.mocked(expressionApi.getReviewList).mockResolvedValue(
      makeListResponse([
        makeExpr(1, { chat_name: '自带群名' }),
        makeExpr(2, { chat_id: 'unknown-chat', chat_name: null }),
      ])
    )
    render(<ExpressionReviewer embedded mode="list" />)

    expect(await screen.findByText('自带群名')).toBeInTheDocument()
    expect(screen.getByText('unknown-chat')).toBeInTheDocument()
  })

  it('列表首屏加载中显示转圈，embedded 且关闭时不请求', async () => {
    vi.mocked(expressionApi.getReviewList).mockReturnValue(new Promise(() => {}))
    const { unmount } = render(<ExpressionReviewer embedded mode="list" />)

    await waitFor(() => expect(document.querySelector('.animate-spin')).not.toBeNull())
    unmount()

    vi.mocked(expressionApi.getReviewList).mockClear()
    render(<ExpressionReviewer embedded open={false} mode="list" />)
    await sleep(40)
    expect(expressionApi.getReviewList).not.toHaveBeenCalled()
    expect(screen.getByText('没有找到表达方式')).toBeInTheDocument()
  })

  it('非 embedded 点击模式栏关闭按钮触发 onOpenChange(false)', async () => {
    const onOpenChange = vi.fn()
    const user = userEvent.setup()
    render(<ExpressionReviewer open onOpenChange={onOpenChange} />)
    await screen.findByText('表达方式审核')

    const closeIcon = document.querySelector('svg.lucide-x')
    expect(closeIcon).not.toBeNull()
    await user.click(closeIcon!.closest('button') as HTMLElement)
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

  it('从精选切回列表模式会重新拉取列表接口', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded />)
    await screen.findByText('情景1')

    await user.click(screen.getByRole('button', { name: /精选/ }))
    expect(await screen.findByRole('listbox')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /列表模式/ }))
    expect(await screen.findByPlaceholderText('搜索情景或风格...')).toBeInTheDocument()
    await waitFor(() =>
      expect(
        vi.mocked(expressionApi.getReviewList).mock.calls.filter(
          ([params]) => params.order === undefined && params.filter_type === 'unchecked'
        ).length
      ).toBeGreaterThanOrEqual(2)
    )
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

  it('接近列表末尾时追加加载，排除已有 id 并去重', async () => {
    const firstBatch = Array.from({ length: 6 }, (_, i) => makeExpr(i + 1))
    // 含重复 id=3，用来覆盖追加去重；total 取 2 使 hasMore 在去重后关闭，避免再次追加锁键盘
    const secondBatch = [makeExpr(3), makeExpr(7), makeExpr(8)]
    vi.mocked(expressionApi.getReviewList).mockImplementation(async (params) => {
      if (params.exclude_ids?.length) {
        return makeListResponse(secondBatch, 2)
      }
      return makeListResponse(firstBatch, 20)
    })
    render(<ExpressionReviewer embedded mode="quick" />)

    await waitFor(() =>
      expect(expressionApi.getReviewList).toHaveBeenCalledWith(
        expect.objectContaining({
          exclude_ids: [1, 2, 3, 4, 5, 6],
        })
      )
    )
    // 追加后 total = 已加载 6 + 本次 total 2，进度显示 1 / 8
    await waitFor(() => expect(screen.getByText(/1\s*\/\s*8/)).toBeInTheDocument())

    // 键盘处理闭包依赖当前 index，必须逐步等待再按下一键
    for (let step = 2; step <= 7; step += 1) {
      fireEvent.keyDown(window, { key: 'ArrowDown' })
      await waitFor(() =>
        expect(screen.getByRole('listbox')).toHaveAttribute(
          'aria-activedescendant',
          `quick-expr-${step}`
        )
      )
    }
    expect(screen.getByText('情景7')).toBeInTheDocument()
    expect(screen.getByText('情景8')).toBeInTheDocument()
  })

  it('快速列表加载失败时弹出错误 toast 并显示空完成态', async () => {
    vi.mocked(expressionApi.getReviewList).mockRejectedValue(new Error('快审加载失败'))
    render(<ExpressionReviewer embedded mode="quick" />)

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载失败',
          description: '快审加载失败',
          variant: 'destructive',
        })
      )
    )
    expect(await screen.findByText('全部审核完成！')).toBeInTheDocument()
  })

  it('快速列表加载抛非 Error 时使用兜底文案', async () => {
    vi.mocked(expressionApi.getReviewList).mockRejectedValue('down')
    render(<ExpressionReviewer embedded mode="quick" />)

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载失败',
          description: '无法加载列表',
          variant: 'destructive',
        })
      )
    )
  })

  it('快速审核接口抛错后卡片保留并可再次操作', async () => {
    vi.mocked(expressionApi.batchReviewExpressions)
      .mockRejectedValueOnce(new Error('网络断开'))
      .mockResolvedValueOnce({
        success: true,
        total: 1,
        succeeded: 1,
        failed: 0,
        results: [{ id: 1, success: true, message: 'ok' }],
      })
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '操作失败',
          description: '网络断开',
          variant: 'destructive',
        })
      )
    )
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledTimes(2)
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '已通过' }))
    )
  })

  it('快速审核抛非 Error 时使用未知错误文案', async () => {
    vi.mocked(expressionApi.batchReviewExpressions).mockRejectedValue(123)
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '操作失败',
          description: '未知错误',
          variant: 'destructive',
        })
      )
    )
  })

  it('已通过筛选下右拖受阻且不触发审核', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="quick" />)
    await screen.findByRole('listbox')
    await user.click(screen.getByRole('tab', { name: /已通过/ }))
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
    )

    const card = screen.getByRole('option', { selected: true })
    fireEvent.mouseDown(card, { clientX: 200, clientY: 80 })
    fireEvent.mouseMove(card, { clientX: 420, clientY: 80 })
    fireEvent.mouseUp(card)

    await sleep(80)
    expect(expressionApi.batchReviewExpressions).not.toHaveBeenCalled()
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
  })

  it('鼠标离开会结束未完成的拖拽且不审核', async () => {
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    const card = screen.getByRole('option', { selected: true })
    fireEvent.mouseDown(card, { clientX: 120, clientY: 80 })
    fireEvent.mouseMove(card, { clientX: 160, clientY: 80 })
    fireEvent.mouseLeave(card)

    await sleep(80)
    expect(expressionApi.batchReviewExpressions).not.toHaveBeenCalled()
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
  })

  it('触摸滑动超过阈值通过当前卡片', async () => {
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    const card = screen.getByRole('option', { selected: true })
    fireEvent.touchStart(card, { touches: [{ clientX: 80, clientY: 90, identifier: 0 }] })
    fireEvent.touchMove(card, { touches: [{ clientX: 260, clientY: 90, identifier: 0 }] })
    fireEvent.touchEnd(card)

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: true, require_unchecked: true },
      ])
    )
  })

  it('拖拽超过阈值通过当前卡片', async () => {
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    const card = screen.getByRole('option', { selected: true })
    fireEvent.mouseDown(card, { clientX: 80, clientY: 90 })
    fireEvent.mouseMove(card, { clientX: 260, clientY: 90 })
    fireEvent.mouseUp(card)

    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: true, require_unchecked: true },
      ])
    )
  })

  it('移动端通过按钮审核当前卡片', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="quick" />)
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
    )

    const { approve, reject } = getQuickMobileButtons()
    expect(approve).toBeEnabled()
    expect(reject).toBeEnabled()
    await user.click(approve)
    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: true, require_unchecked: true },
      ])
    )
  })

  it('已通过筛选下移动端通过按钮禁用，拒绝按钮仍可点', async () => {
    const user = userEvent.setup()
    render(<ExpressionReviewer embedded mode="quick" />)
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
    )

    await user.click(screen.getByRole('tab', { name: /已通过/ }))
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')
    )
    const { approve, reject } = getQuickMobileButtons()
    expect(approve).toBeDisabled()
    expect(reject).toBeEnabled()

    await user.click(reject)
    await waitFor(() =>
      expect(expressionApi.batchReviewExpressions).toHaveBeenCalledWith([
        { id: 1, approved: false, require_unchecked: false },
      ])
    )
  })

  it('首尾方向键不会越界，非方向键不触发审核', async () => {
    render(<ExpressionReviewer embedded mode="quick" />)
    const listbox = await screen.findByRole('listbox')
    await waitFor(() => expect(listbox).toHaveAttribute('aria-activedescendant', 'quick-expr-1'))

    fireEvent.keyDown(window, { key: 'ArrowUp' })
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-1')

    fireEvent.keyDown(window, { key: 'ArrowDown' })
    fireEvent.keyDown(window, { key: 'ArrowDown' })
    await waitFor(() =>
      expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-3')
    )
    fireEvent.keyDown(window, { key: 'ArrowDown' })
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-activedescendant', 'quick-expr-3')

    fireEvent.keyDown(window, { key: 'Enter' })
    expect(expressionApi.batchReviewExpressions).not.toHaveBeenCalled()
  })

  it('快速模式首屏加载中显示思考插画', async () => {
    vi.mocked(expressionApi.getReviewList).mockReturnValue(new Promise(() => {}))
    render(<ExpressionReviewer embedded mode="quick" />)

    expect(await screen.findByRole('status', { name: '加载中' })).toBeInTheDocument()
  })
})
