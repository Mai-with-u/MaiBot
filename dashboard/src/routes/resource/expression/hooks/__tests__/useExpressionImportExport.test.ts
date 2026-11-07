/**
 * useExpressionImportExport 行为测试：清除（成功回调链 / 失败不触发回调 / 未选聊天）。
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { clearExpressions } from '@/lib/expression-api'

import { useExpressionImportExport } from '../useExpressionImportExport'
import type { UseExpressionImportExportOptions } from '../useExpressionImportExport'

import type { ChatInfo } from '@/types/expression'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/expression-api', () => ({
  clearExpressions: vi.fn(),
}))

/** 构造一个具体聊天 */
function makeChat(): ChatInfo {
  return {
    chat_id: 'chat-1',
    chat_name: 'Chat/1:测试',
    platform: 'qq',
    account_id: null,
    is_group: false,
    use_expression: true,
    enable_learning: true,
  }
}

function setupHook(overrides: Partial<UseExpressionImportExportOptions> = {}) {
  const options: UseExpressionImportExportOptions = {
    currentChat: makeChat(),
    onChanged: vi.fn(),
    onClearSelection: vi.fn(),
    onCloseClearConfirm: vi.fn(),
    ...overrides,
  }
  const { result } = renderHook(() => useExpressionImportExport(options))
  return { result, options }
}

beforeEach(() => {
  toastMock.mockClear()
})

describe('useExpressionImportExport 清除', () => {
  it('清除成功时依次触发清空选中、关闭确认框与刷新', async () => {
    vi.mocked(clearExpressions).mockResolvedValue({
      success: true,
      message: '已清除 5 条',
      deleted_count: 5,
    })
    const { result, options } = setupHook()

    await result.current.clearCurrentChat()

    expect(clearExpressions).toHaveBeenCalledWith({ chat_id: 'chat-1' })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '清除成功', description: '已清除 5 条' })
    )
    expect(options.onClearSelection).toHaveBeenCalledTimes(1)
    expect(options.onCloseClearConfirm).toHaveBeenCalledTimes(1)
    expect(options.onChanged).toHaveBeenCalledTimes(1)
  })

  it('清除失败时弹错误提示且不触发任何回调', async () => {
    vi.mocked(clearExpressions).mockRejectedValue(new Error('清除接口失败'))
    const { result, options } = setupHook()

    await result.current.clearCurrentChat()

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '清除失败',
        description: '清除接口失败',
        variant: 'destructive',
      })
    )
    expect(options.onClearSelection).not.toHaveBeenCalled()
    expect(options.onCloseClearConfirm).not.toHaveBeenCalled()
    expect(options.onChanged).not.toHaveBeenCalled()
  })

  it('未选择聊天时不调用清除接口', async () => {
    const { result } = setupHook({ currentChat: null })

    await result.current.clearCurrentChat()

    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '请选择聊天' }))
    expect(clearExpressions).not.toHaveBeenCalled()
  })
})
