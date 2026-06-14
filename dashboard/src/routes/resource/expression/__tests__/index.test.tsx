import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ExpressionManagementPage } from '../index'
import * as expressionApi from '@/lib/expression-api'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))

vi.mock('@/lib/expression-api', () => ({
  getChatList: vi.fn(),
  getExpressionList: vi.fn(),
  getExpressionStats: vi.fn(),
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
  expressions: { id: number; situation: string }[]
  total: number
  onDelete: (e: { id: number }) => void
  onToggleReviewStatus: (e: { id: number }) => void
  onToggleSelect: (id: number) => void
}

// 子组件桩：暴露主文件传入的回调，用于驱动 CRUD/审核/多选编排
vi.mock('../ExpressionList', () => ({
  ExpressionList: ({ expressions, total, onDelete, onToggleReviewStatus, onToggleSelect }: ExprListProps) => (
    <div data-testid="expression-list">
      <span data-testid="list-count">{`${expressions.length}/${total}`}</span>
      {expressions.map((e) => (
        <div key={e.id}>
          <span>{e.situation}</span>
          <button type="button" onClick={() => onDelete(e)}>{`del-${e.id}`}</button>
          <button type="button" onClick={() => onToggleReviewStatus(e)}>{`review-${e.id}`}</button>
          <button type="button" onClick={() => onToggleSelect(e.id)}>{`select-${e.id}`}</button>
        </div>
      ))}
    </div>
  ),
}))

vi.mock('@/components/expression-reviewer', () => ({
  ExpressionReviewer: () => <div data-testid="expression-reviewer" />,
}))
vi.mock('../ExpressionReviewLogPanel', () => ({
  ExpressionReviewLogPanel: () => <div data-testid="expression-review-log" />,
}))

vi.mock('../ExpressionDialogs', () => ({
  ExpressionDetailDialog: () => null,
  ExpressionCreateDialog: () => null,
  ExpressionEditDialog: () => null,
  LegacyExpressionImportDialog: () => null,
  DeleteConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? (
      <div data-testid="delete-confirm">
        <button type="button" onClick={onConfirm}>confirm-delete</button>
      </div>
    ) : null,
  BatchDeleteConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? (
      <div data-testid="batch-delete-confirm">
        <button type="button" onClick={onConfirm}>confirm-batch-delete</button>
      </div>
    ) : null,
  ClearChatExpressionsConfirmDialog: () => null,
}))

function makeExpr(id: number, situation: string) {
  return {
    id,
    situation,
    style: 'casual',
    last_active_time: 1_710_000_000,
    chat_id: 'chat-1',
    chat_name: 'Chat 1',
    create_date: 1_710_000_000,
    checked: false,
    modified_by: null,
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.mocked(expressionApi.getExpressionList).mockResolvedValue({
    success: true, total: 2, page: 1, page_size: 20, data: [makeExpr(1, '情境A'), makeExpr(2, '情境B')],
  } as never)
  vi.mocked(expressionApi.getExpressionStats).mockResolvedValue({
    total: 2, recent_7days: 1, chat_count: 1, top_chats: {},
  } as never)
  vi.mocked(expressionApi.getReviewStats).mockResolvedValue({
    total: 2, unchecked: 1, passed: 1, ai_checked: 0, user_checked: 1,
  } as never)
  vi.mocked(expressionApi.getChatList).mockResolvedValue(
    [{ chat_id: 'chat-1', chat_name: 'Chat 1', is_group: false, use_expression: true, enable_learning: true, platform: null }] as never
  )
  vi.mocked(expressionApi.getExpressionGroups).mockResolvedValue([] as never)
  vi.mocked(expressionApi.deleteExpression).mockResolvedValue({} as never)
  vi.mocked(expressionApi.batchDeleteExpressions).mockResolvedValue({} as never)
  vi.mocked(expressionApi.updateExpressionReviewStatus).mockResolvedValue(makeExpr(1, '情境A') as never)
})

async function renderPage() {
  render(<ExpressionManagementPage />, { wrapper: makeWrapper() })
  await screen.findByRole('tab', { name: '表达' })
}

describe('ExpressionManagementPage 特征化', () => {
  it('初始加载拉取列表/统计/审核统计/聊天流/互通组', async () => {
    await renderPage()
    await waitFor(() => expect(expressionApi.getExpressionList).toHaveBeenCalled())
    expect(expressionApi.getExpressionStats).toHaveBeenCalled()
    expect(expressionApi.getReviewStats).toHaveBeenCalled()
    expect(expressionApi.getChatList).toHaveBeenCalled()
    expect(expressionApi.getExpressionGroups).toHaveBeenCalled()
    expect(await screen.findByTestId('list-count')).toHaveTextContent('2/2')
  })

  it('切到快速审核显示审核器，切回显示列表', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(screen.getByRole('tab', { name: /快速审核/ }))
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
    await waitFor(() => expect(expressionApi.updateExpressionReviewStatus).toHaveBeenCalled())
  })

  it('批量删除：选中后确认调用 batchDeleteExpressions', async () => {
    const user = userEvent.setup()
    await renderPage()
    await user.click(await screen.findByText('select-1'))
    await user.click(await screen.findByText('select-2'))
    await user.click(await screen.findByRole('button', { name: /批量删除/ }))
    await user.click(await screen.findByText('confirm-batch-delete'))
    await waitFor(() => expect(expressionApi.batchDeleteExpressions).toHaveBeenCalledWith([1, 2]))
  })
})
