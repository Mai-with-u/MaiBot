/**
 * ExpressionReviewLogPanel 行为测试：
 * 覆盖首屏加载参数 / 空态 / 通过与未通过徽标 / 救回徽标与按钮禁用 /
 * 人工通过成功与失败链路 / 刷新与结果筛选重新拉取。
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  approveExpressionReviewLog,
  getExpressionChatTargets,
  getExpressionReviewLogs,
} from '@/lib/expression-api'

import { ExpressionReviewLogPanel } from '../ExpressionReviewLogPanel'

import type { Expression, ExpressionReviewLogEntry } from '@/types/expression'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/expression-api', () => ({
  approveExpressionReviewLog: vi.fn(),
  getExpressionChatTargets: vi.fn(),
  getExpressionReviewLogs: vi.fn(),
}))

afterEach(() => {
  cleanup()
})

function makeEntry(overrides: Partial<ExpressionReviewLogEntry> = {}): ExpressionReviewLogEntry {
  return {
    id: 'log-1',
    created_at: 1_710_000_000,
    expression_id: null,
    session_id: 'session-1',
    chat_name: '测试群',
    passed: false,
    reason: '内容重复',
    situation: '被夸奖时',
    style: '害羞回应',
    source: 'learning',
    error: null,
    rescued: false,
    rescued_expression_id: null,
    rescued_at: null,
    ...overrides,
  }
}

function makeExpression(): Expression {
  return {
    id: 9,
    situation: '被夸奖时',
    style: '害羞回应',
    last_active_time: 1_710_000_000,
    chat_id: 'session-1',
    chat_name: '测试群',
    create_date: 1_710_000_000,
    checked: true,
    modified_by: 'user',
  }
}

function mockLogs(entries: ExpressionReviewLogEntry[]) {
  vi.mocked(getExpressionReviewLogs).mockResolvedValue({
    success: true,
    total: entries.length,
    data: entries,
  })
}

beforeEach(() => {
  toastMock.mockClear()
  vi.mocked(getExpressionChatTargets).mockResolvedValue([])
  mockLogs([])
  // Radix Select 在 jsdom 下需要 pointer-capture 相关桩
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

function getDesktopTable() {
  return screen.getByRole('table', { name: '表达方式 AI 审核记录' })
}

describe('ExpressionReviewLogPanel', () => {
  it('挂载时按默认筛选加载日志并额外拉取聊天流映射', async () => {
    render(<ExpressionReviewLogPanel />)
    await waitFor(() => {
      expect(getExpressionReviewLogs).toHaveBeenCalledWith({
        limit: 100,
        passed: undefined,
        chat_id: undefined,
      })
    })
    // loadChatList 里还会以 limit 200 再拉一次日志
    expect(getExpressionReviewLogs).toHaveBeenCalledWith({ limit: 200 })
    expect(getExpressionChatTargets).toHaveBeenCalledTimes(1)
    expect(await screen.findAllByText('暂无 AI 审核记录')).toHaveLength(2)
  })

  it('渲染通过/未通过徽标与救回徽标', async () => {
    mockLogs([
      makeEntry({ id: 'log-pass', passed: true, situation: '通过的情境', reason: '表达自然' }),
      makeEntry({
        id: 'log-rescued',
        situation: '被救回的情境',
        rescued: true,
        rescued_expression_id: 42,
      }),
    ])
    render(<ExpressionReviewLogPanel />)
    const table = getDesktopTable()
    expect(await within(table).findByText('通过的情境')).toBeInTheDocument()
    expect(within(table).getByText('通过')).toBeInTheDocument()
    expect(within(table).getByText('未通过')).toBeInTheDocument()
    expect(within(table).getByText('已救回 #42')).toBeInTheDocument()
    // 头部展示条数
    expect(screen.getByText('最近 2 条表达方式学习写入前审核情况')).toBeInTheDocument()
    // 理由列展示 reason
    expect(within(table).getByText('内容重复')).toBeInTheDocument()
  })

  it('已救回条目的人工通过按钮禁用', async () => {
    mockLogs([makeEntry({ id: 'log-rescued', rescued: true, rescued_expression_id: 7 })])
    render(<ExpressionReviewLogPanel />)
    const table = getDesktopTable()
    const button = await within(table).findByRole('button', { name: '人工通过' })
    expect(button).toBeDisabled()
  })

  it('人工通过成功后弹提示、刷新日志并回调 onRescued', async () => {
    const user = userEvent.setup()
    const onRescued = vi.fn()
    mockLogs([makeEntry({ id: 'log-3' })])
    vi.mocked(approveExpressionReviewLog).mockResolvedValue({
      success: true,
      message: '已恢复为表达方式 #9',
      data: makeExpression(),
    })
    render(<ExpressionReviewLogPanel onRescued={onRescued} />)
    const table = getDesktopTable()
    const button = await within(table).findByRole('button', { name: '人工通过' })
    const callsBefore = vi.mocked(getExpressionReviewLogs).mock.calls.length

    await user.click(button)

    await waitFor(() => {
      expect(approveExpressionReviewLog).toHaveBeenCalledWith('log-3')
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '已人工通过', description: '已恢复为表达方式 #9' })
    )
    await waitFor(() => {
      expect(vi.mocked(getExpressionReviewLogs).mock.calls.length).toBeGreaterThan(callsBefore)
    })
    expect(onRescued).toHaveBeenCalledTimes(1)
  })

  it('人工通过失败时弹恢复失败提示且不回调 onRescued', async () => {
    const user = userEvent.setup()
    const onRescued = vi.fn()
    mockLogs([makeEntry({ id: 'log-4' })])
    vi.mocked(approveExpressionReviewLog).mockRejectedValue(new Error('已存在相同表达'))
    render(<ExpressionReviewLogPanel onRescued={onRescued} />)
    const table = getDesktopTable()

    await user.click(await within(table).findByRole('button', { name: '人工通过' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '恢复失败',
          description: '已存在相同表达',
          variant: 'destructive',
        })
      )
    })
    expect(onRescued).not.toHaveBeenCalled()
  })

  it('结果筛选切到未通过时按 passed=false 重新拉取', async () => {
    const user = userEvent.setup()
    mockLogs([makeEntry()])
    render(<ExpressionReviewLogPanel />)
    await screen.findAllByText('被夸奖时')

    // 第二个 combobox 是结果筛选（第一个是聊天流筛选）
    const selects = screen.getAllByRole('combobox')
    await user.click(selects[1])
    await user.click(await screen.findByRole('option', { name: '未通过' }))

    await waitFor(() => {
      expect(getExpressionReviewLogs).toHaveBeenCalledWith({
        limit: 100,
        passed: false,
        chat_id: undefined,
      })
    })
  })

  it('加载失败时弹加载失败提示', async () => {
    vi.mocked(getExpressionReviewLogs).mockRejectedValue(new Error('网络错误'))
    render(<ExpressionReviewLogPanel />)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载失败',
          description: '网络错误',
          variant: 'destructive',
        })
      )
    })
  })
})
