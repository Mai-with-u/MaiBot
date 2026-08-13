/**
 * useExpressionReview 行为测试：
 * 覆盖单条翻转（通过 / 取消人工通过 / AI 精选仍走通过）、
 * 写失败 toast（Error 与非 Error）、
 * 批量空集短路、全成功 / 部分失败 / 全失败汇总，
 * 以及 Promise.allSettled 之后回调抛错落入 catch 的路径。
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { updateExpressionReviewStatus } from '@/lib/expression-api'

import { useExpressionReview } from '../useExpressionReview'

import type { Expression } from '@/types/expression'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/expression-api', () => ({
  updateExpressionReviewStatus: vi.fn(),
}))

function makeExpression(overrides: Partial<Expression> = {}): Expression {
  return {
    id: 1,
    situation: '被夸奖时',
    style: '害羞回应',
    last_active_time: 1_710_000_000,
    chat_id: 'chat-1',
    chat_name: '测试群',
    create_date: 1_710_000_000,
    checked: false,
    modified_by: null,
    ...overrides,
  }
}

function setupHook(onChanged = vi.fn()) {
  const { result } = renderHook(() => useExpressionReview({ onChanged }))
  return { result, onChanged }
}

beforeEach(() => {
  toastMock.mockClear()
  vi.mocked(updateExpressionReviewStatus).mockReset()
  vi.mocked(updateExpressionReviewStatus).mockResolvedValue(makeExpression())
})

describe('useExpressionReview 单条切换', () => {
  it('未人工通过时设为通过并刷新', async () => {
    const { result, onChanged } = setupHook()

    await result.current.toggleReviewStatus(makeExpression({ checked: false, modified_by: null }))

    expect(updateExpressionReviewStatus).toHaveBeenCalledWith(1, true)
    expect(toastMock).toHaveBeenCalledWith({
      title: '已通过',
      description: '已设为人工通过',
    })
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('已人工通过时取消通过，提示已拒绝', async () => {
    const { result, onChanged } = setupHook()

    await result.current.toggleReviewStatus(
      makeExpression({ checked: true, modified_by: 'user' })
    )

    expect(updateExpressionReviewStatus).toHaveBeenCalledWith(1, false)
    expect(toastMock).toHaveBeenCalledWith({
      title: '已拒绝',
      description: '已取消人工通过',
    })
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('仅 AI 通过时仍按「设为人工通过」调用', async () => {
    const { result } = setupHook()

    await result.current.toggleReviewStatus(makeExpression({ checked: true, modified_by: 'ai' }))

    expect(updateExpressionReviewStatus).toHaveBeenCalledWith(1, true)
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '已通过', description: '已设为人工通过' })
    )
  })

  it('接口抛 Error 时提示更新失败且不刷新', async () => {
    vi.mocked(updateExpressionReviewStatus).mockRejectedValue(new Error('无权操作'))
    const { result, onChanged } = setupHook()

    await result.current.toggleReviewStatus(makeExpression())

    expect(toastMock).toHaveBeenCalledWith({
      title: '更新审核状态失败',
      description: '无权操作',
      variant: 'destructive',
    })
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('接口抛非 Error 时使用兜底文案', async () => {
    vi.mocked(updateExpressionReviewStatus).mockRejectedValue('boom')
    const { result, onChanged } = setupHook()

    await result.current.toggleReviewStatus(makeExpression())

    expect(toastMock).toHaveBeenCalledWith({
      title: '更新审核状态失败',
      description: '无法更新表达方式审核状态',
      variant: 'destructive',
    })
    expect(onChanged).not.toHaveBeenCalled()
  })
})

describe('useExpressionReview 批量切换', () => {
  it('空 id 列表直接返回，不调接口也不弹 toast', async () => {
    const { result, onChanged } = setupHook()

    await result.current.batchReviewStatus([], true)

    expect(updateExpressionReviewStatus).not.toHaveBeenCalled()
    expect(toastMock).not.toHaveBeenCalled()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('全部成功设为通过时刷新并提示更新数量', async () => {
    const { result, onChanged } = setupHook()

    await result.current.batchReviewStatus([1, 2], true)

    expect(updateExpressionReviewStatus).toHaveBeenCalledWith(1, true)
    expect(updateExpressionReviewStatus).toHaveBeenCalledWith(2, true)
    expect(onChanged).toHaveBeenCalledTimes(1)
    expect(toastMock).toHaveBeenCalledWith({
      title: '批量设为通过完成',
      description: '已更新 2 个表达方式',
      variant: undefined,
    })
  })

  it('全部成功设为不通过时使用对应标题', async () => {
    const { result } = setupHook()

    await result.current.batchReviewStatus([3], false)

    expect(updateExpressionReviewStatus).toHaveBeenCalledWith(3, false)
    expect(toastMock).toHaveBeenCalledWith({
      title: '批量设为不通过完成',
      description: '已更新 1 个表达方式',
      variant: undefined,
    })
  })

  it('部分失败时仍刷新，并以 destructive toast 汇总成功/失败数', async () => {
    vi.mocked(updateExpressionReviewStatus)
      .mockResolvedValueOnce(makeExpression({ id: 1 }))
      .mockRejectedValueOnce(new Error('第二条失败'))
      .mockResolvedValueOnce(makeExpression({ id: 3 }))
    const { result, onChanged } = setupHook()

    await result.current.batchReviewStatus([1, 2, 3], true)

    expect(onChanged).toHaveBeenCalledTimes(1)
    expect(toastMock).toHaveBeenCalledWith({
      title: '批量设为通过完成',
      description: '成功 2 个，失败 1 个',
      variant: 'destructive',
    })
  })

  it('全部失败时不刷新，仍弹出失败汇总', async () => {
    vi.mocked(updateExpressionReviewStatus).mockRejectedValue(new Error('全部失败'))
    const { result, onChanged } = setupHook()

    await result.current.batchReviewStatus([1, 2], false)

    expect(onChanged).not.toHaveBeenCalled()
    expect(toastMock).toHaveBeenCalledWith({
      title: '批量设为不通过完成',
      description: '成功 0 个，失败 2 个',
      variant: 'destructive',
    })
  })

  it('刷新回调抛 Error 时落入批量失败 toast', async () => {
    const onChanged = vi.fn(() => {
      throw new Error('刷新失败')
    })
    const { result } = setupHook(onChanged)

    await result.current.batchReviewStatus([1], true)

    expect(toastMock).toHaveBeenCalledWith({
      title: '批量更新审核状态失败',
      description: '刷新失败',
      variant: 'destructive',
    })
  })

  it('刷新回调抛非 Error 时使用批量失败兜底文案', async () => {
    const onChanged = vi.fn(() => {
      throw 'sync-fail'
    })
    const { result } = setupHook(onChanged)

    await result.current.batchReviewStatus([1], true)

    expect(toastMock).toHaveBeenCalledWith({
      title: '批量更新审核状态失败',
      description: '无法批量更新表达方式审核状态',
      variant: 'destructive',
    })
  })
})
