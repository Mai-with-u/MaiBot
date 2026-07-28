/**
 * useExpressionImportExport 行为测试：
 * 覆盖导出（未选聊天 / 未选条目 / 成功下载 / 接口失败）、
 * 导入（未选聊天 / 非法 JSON / 空载荷 / 裸数组 / 包装对象 / 接口失败）、
 * 清除（成功回调链 / 失败不触发回调）三条链路。
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChangeEvent } from 'react'

import { clearExpressions, exportExpressions, importExpressions } from '@/lib/expression-api'

import { useExpressionImportExport } from '../useExpressionImportExport'
import type { UseExpressionImportExportOptions } from '../useExpressionImportExport'

import type { ChatInfo, ExpressionExportItem, ExpressionExportResponse } from '@/types/expression'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/expression-api', () => ({
  clearExpressions: vi.fn(),
  exportExpressions: vi.fn(),
  importExpressions: vi.fn(),
}))

/** 构造一个具体聊天（名称带非法文件名字符，用于验证文件名清洗） */
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

function makeExportItem(): ExpressionExportItem {
  return {
    situation: '打招呼',
    style: '轻松',
    content_list: '[]',
    count: 1,
    last_active_time: null,
    create_time: null,
    checked: false,
    modified_by: null,
  }
}

function makeExportResponse(count: number): ExpressionExportResponse {
  return {
    success: true,
    version: 1,
    type: 'maibot.expression.export',
    exported_at: '2026-01-01T00:00:00Z',
    source_chat_name: 'Chat/1:测试',
    count,
    expressions: [makeExportItem()],
  }
}

/** 构造 <input type="file"> 的 onChange 事件桩（target.value 可被 hook 清空） */
function makeFileChangeEvent(file: File | null): {
  event: ChangeEvent<HTMLInputElement>
  target: { files: File[]; value: string }
} {
  const target = { files: file ? [file] : [], value: 'C:\\fakepath\\import.json' }
  return {
    event: { target } as unknown as ChangeEvent<HTMLInputElement>,
    target,
  }
}

function setupHook(overrides: Partial<UseExpressionImportExportOptions> = {}) {
  const options: UseExpressionImportExportOptions = {
    currentChat: makeChat(),
    selectedIds: new Set([1, 2]),
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

describe('useExpressionImportExport 导出', () => {
  it('未选择聊天时弹提示且不调用导出接口', async () => {
    const { result } = setupHook({ currentChat: null })

    await result.current.exportSelectedExpressionsToFile()

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '请选择聊天', variant: 'destructive' })
    )
    expect(exportExpressions).not.toHaveBeenCalled()
  })

  it('没有选中条目时弹提示且不调用导出接口', async () => {
    const { result } = setupHook({ selectedIds: new Set() })

    await result.current.exportSelectedExpressionsToFile()

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '没有选中项目', variant: 'destructive' })
    )
    expect(exportExpressions).not.toHaveBeenCalled()
  })

  it('导出成功时下载清洗后的文件名并弹成功提示', async () => {
    vi.mocked(exportExpressions).mockResolvedValue(makeExportResponse(2))
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const { result } = setupHook()

    await result.current.exportSelectedExpressionsToFile()

    expect(exportExpressions).toHaveBeenCalledWith({ chat_id: 'chat-1', ids: [1, 2] })
    expect(clickSpy).toHaveBeenCalledTimes(1)
    // 通过 mock.contexts 拿到触发下载的 <a> 元素，验证文件名清洗（/ 与 : 均替换为 _）
    const anchor = clickSpy.mock.contexts[0] as HTMLAnchorElement
    expect(anchor.download).toBe('expressions-Chat_1_测试-selected.json')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '导出成功', description: '已导出 2 个表达方式' })
    )
  })

  it('导出接口失败时弹错误提示且不触发下载', async () => {
    vi.mocked(exportExpressions).mockRejectedValue(new Error('后端炸了'))
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const { result } = setupHook()

    await result.current.exportSelectedExpressionsToFile()

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '导出失败', description: '后端炸了', variant: 'destructive' })
    )
    expect(clickSpy).not.toHaveBeenCalled()
  })
})

describe('useExpressionImportExport 导入', () => {
  it('未选择聊天时拦截并清空 input 值', async () => {
    const { result } = setupHook({ currentChat: null })
    const file = new File(['[]'], 'import.json', { type: 'application/json' })
    const { event, target } = makeFileChangeEvent(file)

    await result.current.handleImportFileChange(event)

    expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: '请选择聊天' }))
    expect(target.value).toBe('')
    expect(importExpressions).not.toHaveBeenCalled()
  })

  it('非法 JSON 时弹导入失败提示', async () => {
    const { result } = setupHook()
    const file = new File(['not-json'], 'import.json', { type: 'application/json' })
    const { event } = makeFileChangeEvent(file)

    await result.current.handleImportFileChange(event)

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '导入失败', variant: 'destructive' })
    )
    expect(importExpressions).not.toHaveBeenCalled()
  })

  it('JSON 中没有可导入条目时弹提示且不调用接口', async () => {
    const { result, options } = setupHook()
    const file = new File([JSON.stringify({ foo: 1 })], 'import.json', {
      type: 'application/json',
    })
    const { event } = makeFileChangeEvent(file)

    await result.current.handleImportFileChange(event)

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '导入失败',
        description: 'JSON 中没有可导入的表达方式',
        variant: 'destructive',
      })
    )
    expect(importExpressions).not.toHaveBeenCalled()
    expect(options.onChanged).not.toHaveBeenCalled()
  })

  it('裸数组载荷导入成功并刷新列表', async () => {
    vi.mocked(importExpressions).mockResolvedValue({
      success: true,
      message: 'ok',
      imported_count: 1,
      skipped_count: 0,
      failed_count: 0,
    })
    const { result, options } = setupHook()
    const items = [makeExportItem()]
    const file = new File([JSON.stringify(items)], 'import.json', { type: 'application/json' })
    const { event } = makeFileChangeEvent(file)

    await result.current.handleImportFileChange(event)

    expect(importExpressions).toHaveBeenCalledWith({ chat_id: 'chat-1', expressions: items })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '导入成功',
        description: '成功 1 个，跳过 0 个，失败 0 个',
      })
    )
    expect(options.onChanged).toHaveBeenCalledTimes(1)
  })

  it('包装对象 { expressions: [...] } 载荷也能规范化导入', async () => {
    vi.mocked(importExpressions).mockResolvedValue({
      success: true,
      message: 'ok',
      imported_count: 2,
      skipped_count: 1,
      failed_count: 3,
    })
    const { result } = setupHook()
    const items = [makeExportItem(), makeExportItem()]
    const file = new File([JSON.stringify({ expressions: items })], 'import.json', {
      type: 'application/json',
    })
    const { event } = makeFileChangeEvent(file)

    await result.current.handleImportFileChange(event)

    expect(importExpressions).toHaveBeenCalledWith({ chat_id: 'chat-1', expressions: items })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ description: '成功 2 个，跳过 1 个，失败 3 个' })
    )
  })

  it('导入接口失败时弹错误提示且不刷新', async () => {
    vi.mocked(importExpressions).mockRejectedValue(new Error('导入接口失败'))
    const { result, options } = setupHook()
    const file = new File([JSON.stringify([makeExportItem()])], 'import.json', {
      type: 'application/json',
    })
    const { event } = makeFileChangeEvent(file)

    await result.current.handleImportFileChange(event)

    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '导入失败',
        description: '导入接口失败',
        variant: 'destructive',
      })
    )
    expect(options.onChanged).not.toHaveBeenCalled()
  })

  it('未选择文件时静默返回', async () => {
    const { result } = setupHook()
    const { event } = makeFileChangeEvent(null)

    await result.current.handleImportFileChange(event)

    expect(toastMock).not.toHaveBeenCalled()
    expect(importExpressions).not.toHaveBeenCalled()
  })
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
