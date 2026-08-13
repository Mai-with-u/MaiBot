import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpressionManagementPage } from '../index'
import * as expressionApi from '@/lib/expression-api'

import type { ChatInfo, Expression, ExpressionGroupInfo } from '@/types/expression'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

vi.mock('@/lib/expression-api', () => ({
  getChatList: vi.fn(),
  getExpressionList: vi.fn(),
  getExpressionStats: vi.fn(),
  getExpressionClusters: vi.fn(),
  getExpressionClusterMembers: vi.fn(),
  getReviewStats: vi.fn(),
  getExpressionGroups: vi.fn(),
  getExpressionDetail: vi.fn(),
  createExpression: vi.fn(),
  updateExpression: vi.fn(),
  deleteExpression: vi.fn(),
  updateExpressionReviewStatus: vi.fn(),
  batchDeleteExpressions: vi.fn(),
  exportExpressions: vi.fn(),
  importExpressions: vi.fn(),
  clearExpressions: vi.fn(),
  getExpressionChatTargets: vi.fn(),
  previewLegacyExpressionImport: vi.fn(),
  previewLegacyExpressionImportFile: vi.fn(),
  importLegacyExpressions: vi.fn(),
}))

interface ExprListProps {
  expressions: Expression[]
  total: number
  page: number
  hideChatColumn?: boolean
  reviewFilter?: string
  onDelete: (expression: Expression) => void
  onEdit: (expression: Expression) => void
  onViewDetail: (expression: Expression) => void
  onToggleReviewStatus: (expression: Expression) => void
  onToggleSelect: (id: number) => void
  onJumpToPage: (page: string) => void
  onReviewFilterChange: (filter: string) => void
}

// 子组件桩：暴露主文件传入的回调，用于驱动详情/编辑/审核/多选/跳页/筛选编排
vi.mock('../ExpressionList', () => ({
  ExpressionList: ({
    expressions,
    total,
    page,
    hideChatColumn,
    reviewFilter,
    onDelete,
    onEdit,
    onViewDetail,
    onToggleReviewStatus,
    onToggleSelect,
    onJumpToPage,
    onReviewFilterChange,
  }: ExprListProps) => (
    <div data-testid="expression-list">
      <span data-testid="list-count">{`${expressions.length}/${total}`}</span>
      <span data-testid="list-page">{String(page)}</span>
      <span data-testid="hide-chat">{String(Boolean(hideChatColumn))}</span>
      <span data-testid="review-filter">{reviewFilter ?? ''}</span>
      <button type="button" onClick={() => onJumpToPage('2')}>
        jump-2
      </button>
      <button type="button" onClick={() => onJumpToPage('0')}>
        jump-0
      </button>
      <button type="button" onClick={() => onJumpToPage('99')}>
        jump-99
      </button>
      <button type="button" onClick={() => onJumpToPage('abc')}>
        jump-abc
      </button>
      <button type="button" onClick={() => onReviewFilterChange('user_checked')}>
        filter-user-checked
      </button>
      {expressions.map((expression) => (
        <div key={expression.id}>
          <span>{expression.situation}</span>
          <button type="button" onClick={() => onDelete(expression)}>{`del-${expression.id}`}</button>
          <button type="button" onClick={() => onEdit(expression)}>{`edit-${expression.id}`}</button>
          <button
            type="button"
            onClick={() => onViewDetail(expression)}
          >{`view-${expression.id}`}</button>
          <button
            type="button"
            onClick={() => onToggleReviewStatus(expression)}
          >{`review-${expression.id}`}</button>
          <button
            type="button"
            onClick={() => onToggleSelect(expression.id)}
          >{`select-${expression.id}`}</button>
        </div>
      ))}
    </div>
  ),
}))

vi.mock('@/components/expression-reviewer', () => ({
  ExpressionReviewer: ({ onReviewed }: { onReviewed?: () => void }) => (
    <div data-testid="expression-reviewer">
      <button type="button" onClick={() => onReviewed?.()}>
        reviewer-on-reviewed
      </button>
    </div>
  ),
}))

vi.mock('../ExpressionReviewLogPanel', () => ({
  ExpressionReviewLogPanel: ({ onRescued }: { onRescued?: () => void }) => (
    <div data-testid="expression-review-log">
      <button type="button" onClick={() => onRescued?.()}>
        rescue-success
      </button>
    </div>
  ),
}))

vi.mock('../ExpressionClusterBrowser', () => ({
  ExpressionClusterBrowser: ({
    onOpenExpression,
  }: {
    onOpenExpression: (expressionId: number) => void
  }) => (
    <div data-testid="expression-clusters">
      <button type="button" onClick={() => onOpenExpression(42)}>
        open-cluster-expr
      </button>
    </div>
  ),
}))

vi.mock('../ExpressionDialogs', () => ({
  ExpressionDetailDialog: ({
    open,
    expression,
  }: {
    open: boolean
    expression: Expression | null
  }) =>
    open && expression ? <div data-testid="detail-dialog">{expression.situation}</div> : null,
  ExpressionCreateDialog: ({ open, onSuccess }: { open: boolean; onSuccess: () => void }) =>
    open ? (
      <button type="button" onClick={onSuccess}>
        create-success
      </button>
    ) : null,
  ExpressionEditDialog: ({ open, onSuccess }: { open: boolean; onSuccess: () => void }) =>
    open ? (
      <button type="button" onClick={onSuccess}>
        edit-success
      </button>
    ) : null,
  LegacyExpressionImportDialog: ({ open, onSuccess }: { open: boolean; onSuccess: () => void }) =>
    open ? (
      <button type="button" onClick={onSuccess}>
        legacy-import-success
      </button>
    ) : null,
  DeleteConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? (
      <div data-testid="delete-confirm">
        <button type="button" onClick={onConfirm}>
          confirm-delete
        </button>
      </div>
    ) : null,
  BatchDeleteConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? (
      <div data-testid="batch-delete-confirm">
        <button type="button" onClick={onConfirm}>
          confirm-batch-delete
        </button>
      </div>
    ) : null,
  ClearChatExpressionsConfirmDialog: ({
    open,
    onConfirm,
    chatName,
  }: {
    open: boolean
    onConfirm: () => void
    chatName: string
  }) =>
    open ? (
      <div data-testid="clear-confirm">
        <span>{chatName}</span>
        <button type="button" onClick={onConfirm}>
          confirm-clear
        </button>
      </div>
    ) : null,
}))

function makeExpr(id: number, situation: string, overrides: Partial<Expression> = {}): Expression {
  return {
    id,
    situation,
    style: 'casual',
    last_active_time: 1_710_000_000,
    chat_id: 'chat-1',
    chat_name: '测试群A',
    create_date: 1_710_000_000,
    checked: false,
    modified_by: null,
    ...overrides,
  }
}

function makeChat(overrides: Partial<ChatInfo> = {}): ChatInfo {
  return {
    chat_id: 'chat-1',
    chat_name: '测试群A',
    platform: 'qq',
    account_id: 'acc-1',
    is_group: false,
    use_expression: true,
    enable_learning: true,
    ...overrides,
  }
}

function makeGroup(overrides: Partial<ExpressionGroupInfo> = {}): ExpressionGroupInfo {
  return {
    index: 1,
    name: '混合组',
    chat_ids: ['chat-1', 'chat-2'],
    is_global: false,
    members: [
      makeChat({ use_expression: true, enable_learning: true }),
      makeChat({
        chat_id: 'chat-2',
        chat_name: '测试群B',
        account_id: null,
        use_expression: false,
        enable_learning: false,
      }),
    ],
    ...overrides,
  }
}

function defaultGroups(): ExpressionGroupInfo[] {
  return [
    makeGroup(),
    makeGroup({
      index: 2,
      name: '空聊天组',
      chat_ids: [],
      members: [],
    }),
    makeGroup({
      index: 3,
      name: '全局组',
      is_global: true,
      chat_ids: ['ignored'],
      members: [
        makeChat({ use_expression: true, enable_learning: true }),
        makeChat({
          chat_id: 'chat-2',
          chat_name: '测试群B',
          account_id: null,
          use_expression: true,
          enable_learning: true,
        }),
      ],
    }),
    makeGroup({
      index: 4,
      name: '全关组',
      chat_ids: ['chat-2'],
      members: [
        makeChat({
          chat_id: 'chat-2',
          chat_name: '测试群B',
          account_id: null,
          use_expression: false,
          enable_learning: false,
        }),
        makeChat({
          chat_id: 'chat-3',
          chat_name: '测试群C',
          account_id: null,
          use_expression: false,
          enable_learning: false,
        }),
      ],
    }),
    makeGroup({
      index: 5,
      name: '无成员组',
      chat_ids: ['chat-1'],
      members: [],
    }),
  ]
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function installPointerCaptureStub() {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
}

beforeEach(() => {
  toastMock.mockClear()
  installPointerCaptureStub()
  vi.mocked(expressionApi.getExpressionList).mockResolvedValue({
    success: true,
    total: 2,
    page: 1,
    page_size: 20,
    data: [makeExpr(1, '情境A'), makeExpr(2, '情境B', { checked: true, modified_by: 'user' })],
  })
  vi.mocked(expressionApi.getExpressionStats).mockResolvedValue({
    total: 2,
    recent_7days: 1,
    chat_count: 1,
    top_chats: {},
  })
  vi.mocked(expressionApi.getExpressionClusters).mockResolvedValue({
    success: true,
    index_exists: true,
    index_path: 'data/expression_selection/expression_vector_index.json',
    generated_at: null,
    updated_at: null,
    embedding_model: 'test',
    embedding_dimension: 3,
    sample_count: 1,
    clusters: [],
  })
  vi.mocked(expressionApi.getExpressionClusterMembers).mockResolvedValue({
    success: true,
    cluster: null,
    data: [],
  })
  vi.mocked(expressionApi.getReviewStats).mockResolvedValue({
    total: 2,
    unchecked: 1,
    passed: 1,
    ai_checked: 0,
    user_checked: 1,
  })
  vi.mocked(expressionApi.getChatList).mockResolvedValue([
    makeChat(),
    makeChat({
      chat_id: 'chat-2',
      chat_name: '测试群B',
      account_id: null,
      use_expression: false,
      enable_learning: false,
    }),
  ])
  vi.mocked(expressionApi.getExpressionGroups).mockResolvedValue([])
  vi.mocked(expressionApi.getExpressionDetail).mockResolvedValue(makeExpr(1, '详情情境'))
  vi.mocked(expressionApi.deleteExpression).mockResolvedValue({} as never)
  vi.mocked(expressionApi.batchDeleteExpressions).mockResolvedValue({} as never)
  vi.mocked(expressionApi.updateExpressionReviewStatus).mockResolvedValue(makeExpr(1, '情境A'))
  vi.mocked(expressionApi.exportExpressions).mockResolvedValue({
    success: true,
    version: 1,
    type: 'maibot.expression.export',
    exported_at: '2026-01-01T00:00:00Z',
    source_chat_name: '测试群A',
    count: 1,
    expressions: [],
  })
  vi.mocked(expressionApi.importExpressions).mockResolvedValue({
    success: true,
    message: 'ok',
    imported_count: 1,
    skipped_count: 0,
    failed_count: 0,
  })
  vi.mocked(expressionApi.clearExpressions).mockResolvedValue({
    success: true,
    message: '已清除 3 条',
    deleted_count: 3,
  })
})

async function renderPage() {
  render(<ExpressionManagementPage />, { wrapper: makeWrapper() })
  await screen.findByRole('tab', { name: '表达' })
  await screen.findByTestId('list-count')
}

async function waitForSelectedChat(chatId = 'chat-1') {
  await waitFor(() =>
    expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
      expect.objectContaining({ chat_id: chatId })
    )
  )
}

describe('ExpressionManagementPage 特征化', () => {
  it('初始加载拉取列表/统计/审核统计/聊天流/共享组', async () => {
    await renderPage()
    await waitFor(() => expect(expressionApi.getExpressionList).toHaveBeenCalled())
    expect(expressionApi.getExpressionStats).toHaveBeenCalled()
    expect(expressionApi.getReviewStats).toHaveBeenCalled()
    expect(expressionApi.getChatList).toHaveBeenCalled()
    expect(expressionApi.getExpressionGroups).toHaveBeenCalled()
    expect(await screen.findByTestId('list-count')).toHaveTextContent('2/2')
    expect(screen.getByText('总数量')).toBeInTheDocument()
    expect(screen.getByText('近7天新增')).toBeInTheDocument()
  })

  it('切到精选显示审核器，切回显示列表', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('tab', { name: /精选/ }))
    expect(await screen.findByTestId('expression-reviewer')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '表达' }))
    expect(await screen.findByTestId('expression-list')).toBeInTheDocument()
  })

  it('单条删除：确认后调用 deleteExpression', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('del-1'))
    await user.click(await screen.findByText('confirm-delete'))
    await waitFor(() => expect(expressionApi.deleteExpression).toHaveBeenCalledWith(1))
  })

  it('单条审核切换调用 updateExpressionReviewStatus', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('review-1'))
    await waitFor(() =>
      expect(expressionApi.updateExpressionReviewStatus).toHaveBeenCalledWith(1, true)
    )
  })

  it('批量删除：选中后确认调用 batchDeleteExpressions', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('select-1'))
    await user.click(await screen.findByText('select-2'))
    await user.click(await screen.getByRole('button', { name: /批量删除/ }))
    await user.click(await screen.findByText('confirm-batch-delete'))
    await waitFor(() => expect(expressionApi.batchDeleteExpressions).toHaveBeenCalledWith([1, 2]))
  })
})

describe('ExpressionManagementPage 视图切换', () => {
  it('未审核数超过 99 时精选页签显示 99+', async () => {
    vi.mocked(expressionApi.getReviewStats).mockResolvedValue({
      total: 200,
      unchecked: 120,
      passed: 80,
      ai_checked: 0,
      user_checked: 80,
    })
    await renderPage()
    expect(await screen.findByText('99+')).toBeInTheDocument()
  })

  it('精选可进入 AI 审核记录，聚类可打开表达，切回表达会重新拉取列表与统计', async () => {
    const user = userEvent.setup()
    await renderPage()
    await waitForSelectedChat()

    const listCallsAfterLoad = vi.mocked(expressionApi.getExpressionList).mock.calls.length
    const statsCallsAfterLoad = vi.mocked(expressionApi.getExpressionStats).mock.calls.length
    const reviewCallsAfterLoad = vi.mocked(expressionApi.getReviewStats).mock.calls.length

    await user.click(screen.getByRole('tab', { name: /精选/ }))
    expect(await screen.findByTestId('expression-reviewer')).toBeInTheDocument()
    await waitFor(() =>
      expect(vi.mocked(expressionApi.getReviewStats).mock.calls.length).toBeGreaterThan(
        reviewCallsAfterLoad
      )
    )

    await user.click(screen.getByRole('button', { name: /AI审核记录/ }))
    expect(await screen.findByTestId('expression-review-log')).toBeInTheDocument()
    await user.click(screen.getByText('rescue-success'))
    await waitFor(() =>
      expect(vi.mocked(expressionApi.getExpressionList).mock.calls.length).toBeGreaterThan(
        listCallsAfterLoad
      )
    )

    await user.click(screen.getByRole('tab', { name: /精选/ }))
    await user.click(screen.getByText('reviewer-on-reviewed'))

    await user.click(screen.getByRole('tab', { name: '聚类' }))
    expect(await screen.findByTestId('expression-clusters')).toBeInTheDocument()
    await user.click(screen.getByText('open-cluster-expr'))
    await waitFor(() => expect(expressionApi.getExpressionDetail).toHaveBeenCalledWith(42))
    expect(await screen.findByTestId('detail-dialog')).toHaveTextContent('详情情境')

    await user.click(screen.getByRole('tab', { name: '表达' }))
    expect(await screen.findByTestId('expression-list')).toBeInTheDocument()
    await waitFor(() => {
      expect(vi.mocked(expressionApi.getExpressionList).mock.calls.length).toBeGreaterThan(
        listCallsAfterLoad
      )
      expect(vi.mocked(expressionApi.getExpressionStats).mock.calls.length).toBeGreaterThan(
        statsCallsAfterLoad
      )
    })
  })
})

describe('ExpressionManagementPage 详情与对话框回调', () => {
  it('查看详情成功打开对话框，失败分别展示 Error 与兜底文案', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(await screen.findByText('view-1'))
    await waitFor(() => expect(expressionApi.getExpressionDetail).toHaveBeenCalledWith(1))
    expect(await screen.findByTestId('detail-dialog')).toHaveTextContent('详情情境')

    vi.mocked(expressionApi.getExpressionDetail).mockRejectedValueOnce(new Error('记录不存在'))
    await user.click(screen.getByText('view-1'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载详情失败',
          description: '记录不存在',
          variant: 'destructive',
        })
      )
    )

    vi.mocked(expressionApi.getExpressionDetail).mockRejectedValueOnce('bad')
    await user.click(screen.getByText('view-1'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载详情失败',
          description: '无法加载表达方式详情',
          variant: 'destructive',
        })
      )
    )
  })

  it('创建/编辑成功关闭对话框并刷新；旧版导入成功也会刷新', async () => {
    const user = userEvent.setup()
    await renderPage()
    await waitForSelectedChat()

    const listCallsAfterLoad = vi.mocked(expressionApi.getExpressionList).mock.calls.length

    await user.click(await screen.findByRole('button', { name: /新增/ }))
    await user.click(await screen.findByText('create-success'))
    await waitFor(() => expect(screen.queryByText('create-success')).not.toBeInTheDocument())

    await user.click(screen.getByText('edit-1'))
    await user.click(await screen.findByText('edit-success'))
    await waitFor(() => expect(screen.queryByText('edit-success')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /从旧版本导入/ }))
    await user.click(await screen.findByText('legacy-import-success'))

    await waitFor(() =>
      expect(vi.mocked(expressionApi.getExpressionList).mock.calls.length).toBeGreaterThan(
        listCallsAfterLoad
      )
    )
  })
})

describe('ExpressionManagementPage 浏览维度与筛选', () => {
  it('按组浏览：带 chat_ids、空组短路、全局组不传 chat_ids；切到全部清空范围', async () => {
    const user = userEvent.setup()
    vi.mocked(expressionApi.getExpressionGroups).mockResolvedValue(defaultGroups())
    await renderPage()
    await waitForSelectedChat()
    expect(screen.getByTestId('hide-chat')).toHaveTextContent('true')

    await user.click(screen.getByRole('button', { name: '组' }))
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({
          chat_id: undefined,
          chat_ids: ['chat-1', 'chat-2'],
        })
      )
    )
    expect(screen.getByTestId('hide-chat')).toHaveTextContent('true')

    vi.mocked(expressionApi.getExpressionList).mockClear()
    // 组按钮无障碍名包含成员描述，按名称子串匹配
    await user.click(screen.getByRole('button', { name: /空聊天组/ }))
    await waitFor(() => expect(screen.getByTestId('list-count')).toHaveTextContent('0/0'))
    expect(expressionApi.getExpressionList).not.toHaveBeenCalledWith(
      expect.objectContaining({ chat_ids: [] })
    )

    await user.click(screen.getByRole('button', { name: /全局组/ }))
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({
          chat_id: undefined,
          chat_ids: undefined,
        })
      )
    )

    await user.click(screen.getByRole('button', { name: '全部' }))
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({
          chat_id: undefined,
          chat_ids: undefined,
        })
      )
    )
    expect(screen.getByTestId('hide-chat')).toHaveTextContent('false')
    expect(screen.getByText('当前显示全部表达方式')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '聊天' }))
    await waitForSelectedChat('chat-1')
  })

  it('切换聊天、显示旧格式、搜索与精选筛选都会改写列表请求', async () => {
    const user = userEvent.setup()
    await renderPage()
    await waitForSelectedChat()

    await user.click(screen.getByRole('button', { name: '测试群B' }))
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({ chat_id: 'chat-2' })
      )
    )

    await user.click(screen.getByRole('switch', { name: '显示旧格式' }))
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({ include_legacy: true })
      )
    )
    expect(expressionApi.getChatList).toHaveBeenCalledWith({ include_legacy: true })
    expect(expressionApi.getExpressionGroups).toHaveBeenCalledWith({ include_legacy: true })
    expect(expressionApi.getExpressionStats).toHaveBeenCalledWith({ include_legacy: true })

    await user.type(screen.getByLabelText('搜索'), '情境')
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({ search: '情境' })
      )
    )

    await user.click(screen.getByText('filter-user-checked'))
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({ review_filter: 'user_checked' })
      )
    )
    expect(screen.getByTestId('review-filter')).toHaveTextContent('user_checked')
  })

  it('空聊天列表不自动选中；空共享组显示占位文案', async () => {
    const user = userEvent.setup()
    vi.mocked(expressionApi.getChatList).mockResolvedValue([])
    await renderPage()
    await waitFor(() => expect(expressionApi.getExpressionList).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /新增/ })).not.toBeInTheDocument()
    expect(expressionApi.getExpressionList).not.toHaveBeenCalledWith(
      expect.objectContaining({ chat_id: expect.any(String) })
    )

    await user.click(screen.getByRole('button', { name: '组' }))
    expect(await screen.findByText('暂无共享组')).toBeInTheDocument()
  })
})

describe('ExpressionManagementPage 作用域指示', () => {
  it('按聊天显示开/关，按组显示混合/全开/全关，无成员组不渲染指示条', async () => {
    const user = userEvent.setup()
    vi.mocked(expressionApi.getExpressionGroups).mockResolvedValue(defaultGroups())
    await renderPage()
    await waitForSelectedChat()

    expect(screen.getByText('开启学习')).toBeInTheDocument()
    expect(screen.getByText('开启使用')).toBeInTheDocument()
    expect(screen.getByText('开启学习').previousElementSibling).toHaveClass('bg-green-500')
    expect(screen.getByText('开启学习').parentElement).not.toHaveClass('border-l')
    expect(screen.getByText('开启使用').parentElement).toHaveClass('border-l')

    await user.click(screen.getByRole('button', { name: '测试群B' }))
    await waitFor(() => expect(screen.getByText('关闭学习')).toBeInTheDocument())
    expect(screen.getByText('关闭使用')).toBeInTheDocument()
    expect(screen.getByText('关闭学习').previousElementSibling).toHaveClass('bg-muted-foreground')

    await user.click(screen.getByRole('button', { name: '组' }))
    await waitFor(() => expect(screen.getByText('部分学习')).toBeInTheDocument())
    expect(screen.getByText('部分使用')).toBeInTheDocument()
    expect(screen.getByText('部分学习').previousElementSibling).toHaveClass('bg-amber-500')

    await user.click(screen.getByRole('button', { name: /全局组/ }))
    await waitFor(() => expect(screen.getByText('开启学习')).toBeInTheDocument())
    expect(screen.getByText('开启使用')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /全关组/ }))
    await waitFor(() => expect(screen.getByText('关闭学习')).toBeInTheDocument())
    expect(screen.getByText('关闭使用')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /无成员组/ }))
    await waitFor(() => expect(screen.queryByText('开启学习')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /新增/ })).not.toBeInTheDocument()
  })
})

describe('ExpressionManagementPage 审核与选择', () => {
  it('人工精选行切换为拒绝；接口失败弹出更新失败', async () => {
    const user = userEvent.setup()
    await renderPage()

    await user.click(await screen.findByText('review-2'))
    await waitFor(() =>
      expect(expressionApi.updateExpressionReviewStatus).toHaveBeenCalledWith(2, false)
    )
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '已拒绝', description: '已取消人工通过' })
    )

    vi.mocked(expressionApi.updateExpressionReviewStatus).mockRejectedValueOnce(
      new Error('审核接口挂了')
    )
    await user.click(screen.getByText('review-1'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '更新审核状态失败',
          description: '审核接口挂了',
          variant: 'destructive',
        })
      )
    )
  })

  it('批量通过/不通过调用审核接口，取消选择后隐藏工具栏', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('select-1'))
    await user.click(screen.getByText('select-2'))
    expect(screen.getByText('已选择 2 个表达方式')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /批量通过/ }))
    await waitFor(() => {
      expect(expressionApi.updateExpressionReviewStatus).toHaveBeenCalledWith(1, true)
      expect(expressionApi.updateExpressionReviewStatus).toHaveBeenCalledWith(2, true)
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '批量设为通过完成' })
    )

    await user.click(screen.getByRole('button', { name: /批量不通过/ }))
    await waitFor(() => {
      expect(expressionApi.updateExpressionReviewStatus).toHaveBeenCalledWith(1, false)
      expect(expressionApi.updateExpressionReviewStatus).toHaveBeenCalledWith(2, false)
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '批量设为不通过完成' })
    )

    await user.click(screen.getByRole('button', { name: '取消选择' }))
    expect(screen.queryByText('已选择 2 个表达方式')).not.toBeInTheDocument()
  })
})

describe('ExpressionManagementPage 删除失败', () => {
  it('单条与批量删除失败分别展示 Error 与兜底文案', async () => {
    const user = userEvent.setup()
    await renderPage()

    vi.mocked(expressionApi.deleteExpression).mockRejectedValueOnce(new Error('删不掉'))
    await user.click(await screen.findByText('del-1'))
    await user.click(await screen.findByText('confirm-delete'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '删除失败',
          description: '删不掉',
          variant: 'destructive',
        })
      )
    )
    expect(screen.getByTestId('delete-confirm')).toBeInTheDocument()

    vi.mocked(expressionApi.deleteExpression).mockRejectedValueOnce('nope')
    await user.click(screen.getByText('confirm-delete'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '删除失败',
          description: '无法删除表达方式',
          variant: 'destructive',
        })
      )
    )

    await user.click(screen.getByText('select-1'))
    await user.click(screen.getByRole('button', { name: /批量删除/ }))
    vi.mocked(expressionApi.batchDeleteExpressions).mockRejectedValueOnce(new Error('批量失败'))
    await user.click(await screen.findByText('confirm-batch-delete'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量删除失败',
          description: '批量失败',
          variant: 'destructive',
        })
      )
    )

    vi.mocked(expressionApi.batchDeleteExpressions).mockRejectedValueOnce(0)
    await user.click(screen.getByText('confirm-batch-delete'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '批量删除失败',
          description: '无法批量删除表达方式',
          variant: 'destructive',
        })
      )
    )
  })
})

describe('ExpressionManagementPage 导入导出清除与分页', () => {
  it('导出所选、导入文件与清除当前聊天接到对应接口', async () => {
    const user = userEvent.setup()
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await renderPage()
    await waitForSelectedChat()

    expect(screen.getByRole('button', { name: /导出所选/ })).toBeDisabled()
    await user.click(screen.getByText('select-1'))
    await user.click(screen.getByRole('button', { name: /导出所选/ }))
    await waitFor(() =>
      expect(expressionApi.exportExpressions).toHaveBeenCalledWith({
        chat_id: 'chat-1',
        ids: [1],
      })
    )
    expect(clickSpy).toHaveBeenCalled()

    const file = new File(
      [
        JSON.stringify([
          {
            situation: '打招呼',
            style: '轻松',
            content_list: '[]',
            count: 1,
            last_active_time: null,
            create_time: null,
            checked: false,
            modified_by: null,
          },
        ]),
      ],
      'import.json',
      { type: 'application/json' }
    )
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, file)
    await waitFor(() =>
      expect(expressionApi.importExpressions).toHaveBeenCalledWith({
        chat_id: 'chat-1',
        expressions: [
          expect.objectContaining({ situation: '打招呼', style: '轻松' }),
        ],
      })
    )

    await user.click(screen.getByRole('button', { name: '更多操作' }))
    await user.click(await screen.findByRole('menuitem', { name: /清除/ }))
    expect(await screen.findByTestId('clear-confirm')).toHaveTextContent('测试群A')
    await user.click(screen.getByText('confirm-clear'))
    await waitFor(() =>
      expect(expressionApi.clearExpressions).toHaveBeenCalledWith({ chat_id: 'chat-1' })
    )
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '清除成功', description: '已清除 3 条' })
    )
    clickSpy.mockRestore()
  })

  it('合法页码跳转会改 page；非法页码不发请求', async () => {
    const user = userEvent.setup()
    vi.mocked(expressionApi.getExpressionList).mockImplementation(async (params) => ({
      success: true,
      total: 50,
      page: params.page,
      page_size: params.page_size,
      data: [makeExpr(1, '情境A'), makeExpr(2, '情境B')],
    }))
    await renderPage()
    await waitFor(() => expect(screen.getByTestId('list-count')).toHaveTextContent('2/50'))

    await user.click(screen.getByText('jump-2'))
    await waitFor(() =>
      expect(expressionApi.getExpressionList).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2 })
      )
    )
    expect(screen.getByTestId('list-page')).toHaveTextContent('2')

    const pagesBeforeInvalid = vi
      .mocked(expressionApi.getExpressionList)
      .mock.calls.map((call) => call[0]?.page)
    await user.click(screen.getByText('jump-0'))
    await user.click(screen.getByText('jump-99'))
    await user.click(screen.getByText('jump-abc'))
    const pagesAfterInvalid = vi
      .mocked(expressionApi.getExpressionList)
      .mock.calls.map((call) => call[0]?.page)
    expect(pagesAfterInvalid).toEqual(pagesBeforeInvalid)
  })
})
