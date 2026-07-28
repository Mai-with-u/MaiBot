import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MemoryDeleteDialog } from '../MemoryDeleteDialog'
import type {
  MemoryDeleteExecutePayload,
  MemoryDeletePreviewItemPayload,
  MemoryDeletePreviewPayload,
} from '@/lib/memory-api'

afterEach(() => {
  cleanup()
})

/** 构造一条预览明细项 */
function makePreviewItem(index: number, overrides: Partial<MemoryDeletePreviewItemPayload> = {}): MemoryDeletePreviewItemPayload {
  return {
    item_type: 'relation',
    item_hash: `hash-${index}`,
    item_key: `key-${index}`,
    label: `标签${index}`,
    preview: `预览内容${index}`,
    source: `来源${index}`,
    ...overrides,
  }
}

/** 构造删除预览载荷 */
function makePreview(items: MemoryDeletePreviewItemPayload[], overrides: Partial<MemoryDeletePreviewPayload> = {}): MemoryDeletePreviewPayload {
  return {
    success: true,
    mode: 'entity',
    selector: { keyword: '测试' },
    counts: { entities: 2, relations: 3, paragraphs: 0, sources: 0 },
    sources: ['文档A', '文档B'],
    items,
    item_count: items.length,
    ...overrides,
  }
}

/** 构造删除执行结果载荷 */
function makeResult(overrides: Partial<MemoryDeleteExecutePayload> = {}): MemoryDeleteExecutePayload {
  return {
    success: true,
    mode: 'entity',
    operation_id: 'op-42',
    counts: {},
    sources: [],
    deleted_count: 6,
    deleted_entity_count: 1,
    deleted_relation_count: 2,
    deleted_paragraph_count: 3,
    deleted_source_count: 0,
    ...overrides,
  }
}

/** 渲染对话框，返回常用回调桩 */
function renderDialog(props: Partial<Parameters<typeof MemoryDeleteDialog>[0]> = {}) {
  const onOpenChange = vi.fn()
  const onExecute = vi.fn()
  render(
    <MemoryDeleteDialog
      open
      onOpenChange={onOpenChange}
      title="删除记忆"
      description="请确认删除范围"
      preview={null}
      result={null}
      onExecute={onExecute}
      {...props}
    />,
  )
  return { onOpenChange, onExecute }
}

describe('MemoryDeleteDialog 基础渲染', () => {
  it('open 为 false 时不渲染任何内容', () => {
    renderDialog({ open: false })
    expect(screen.queryByText('删除记忆')).not.toBeInTheDocument()
  })

  it('加载预览中时显示提示文案且确认按钮禁用', () => {
    renderDialog({ loadingPreview: true })
    expect(screen.getByText('正在生成删除预览...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /确认删除/ })).toBeDisabled()
  })

  it('preview 为空时确认按钮禁用，传入 preview 后可点击并触发 onExecute', () => {
    const { onExecute } = renderDialog()
    expect(screen.getByRole('button', { name: /确认删除/ })).toBeDisabled()
    cleanup()

    const { onExecute: onExecute2 } = renderDialog({ preview: makePreview([makePreviewItem(1)]) })
    const confirmButton = screen.getByRole('button', { name: /确认删除/ })
    expect(confirmButton).toBeEnabled()
    fireEvent.click(confirmButton)
    expect(onExecute2).toHaveBeenCalledTimes(1)
    expect(onExecute).not.toHaveBeenCalled()
  })

  it('错误信息展示在警示框中', () => {
    renderDialog({ error: '删除预览生成失败' })
    expect(screen.getByText('删除预览生成失败')).toBeInTheDocument()
  })

  it('点击底部关闭按钮调用 onOpenChange(false)', () => {
    const { onOpenChange } = renderDialog()
    // Radix 右上角的 X 按钮也叫「关闭」，取最后一个即页脚按钮
    const closeButtons = screen.getAllByRole('button', { name: '关闭' })
    fireEvent.click(closeButtons[closeButtons.length - 1])
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('MemoryDeleteDialog 预览内容', () => {
  it('渲染模式徽章、计数徽章（仅显示大于零的项）和来源列表', () => {
    renderDialog({
      preview: makePreview([makePreviewItem(1)], {
        matched_source_count: 2,
        requested_source_count: 3,
      }),
    })
    // mode 'entity' 翻译为「实体删除」
    expect(screen.getByText('实体删除')).toBeInTheDocument()
    expect(screen.getByText('预览项 1')).toBeInTheDocument()
    expect(screen.getByText('实体 2')).toBeInTheDocument()
    expect(screen.getByText('关系 3')).toBeInTheDocument()
    // 段落/来源计数为 0，不应渲染
    expect(screen.queryByText('段落 0')).not.toBeInTheDocument()
    expect(screen.queryByText('来源 0')).not.toBeInTheDocument()
    expect(screen.getByText('关联来源：文档A、文档B')).toBeInTheDocument()
    expect(screen.getByText(/命中来源 2/)).toBeInTheDocument()
    expect(screen.getByText(/请求来源 3/)).toBeInTheDocument()
  })

  it('未知模式回退显示原始 mode 字符串', () => {
    renderDialog({ preview: makePreview([makePreviewItem(1)], { mode: 'custom_mode' }) })
    expect(screen.getByText('custom_mode')).toBeInTheDocument()
  })

  it('明细项渲染标签、预览与 hash', () => {
    renderDialog({ preview: makePreview([makePreviewItem(7)]) })
    expect(screen.getByText('标签7')).toBeInTheDocument()
    expect(screen.getByText('预览内容7')).toBeInTheDocument()
    expect(screen.getByText('hash-7')).toBeInTheDocument()
  })

  it('明细项为空时显示占位文案', () => {
    renderDialog({ preview: makePreview([]) })
    expect(screen.getByText('当前预览没有可展示的明细项。')).toBeInTheDocument()
  })

  it('搜索关键词过滤明细并更新命中统计', () => {
    const items = [makePreviewItem(1), makePreviewItem(2, { label: '特殊目标' })]
    renderDialog({ preview: makePreview(items) })
    expect(screen.getByText(/命中 2 \/ 2 项/)).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('搜索类型 / hash / item_key / source'), {
      target: { value: '特殊' },
    })
    expect(screen.getByText(/命中 1 \/ 2 项/)).toBeInTheDocument()
    expect(screen.getByText('特殊目标')).toBeInTheDocument()
    expect(screen.queryByText('标签1')).not.toBeInTheDocument()
  })

  it('分页：每页 8 项，翻页按钮按边界禁用', () => {
    const items = Array.from({ length: 10 }, (_, index) => makePreviewItem(index + 1))
    renderDialog({ preview: makePreview(items) })

    // 第一页显示前 8 项
    expect(screen.getByText(/第 1 \/ 2 页/)).toBeInTheDocument()
    expect(screen.getByText('标签1')).toBeInTheDocument()
    expect(screen.queryByText('标签9')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()

    // 翻到第二页
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText(/第 2 \/ 2 页/)).toBeInTheDocument()
    expect(screen.getByText('标签9')).toBeInTheDocument()
    expect(screen.queryByText('标签1')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()

    // 回到第一页
    fireEvent.click(screen.getByRole('button', { name: '上一页' }))
    expect(screen.getByText(/第 1 \/ 2 页/)).toBeInTheDocument()
  })

  it('修改搜索关键词后页码重置回第一页', () => {
    const items = Array.from({ length: 10 }, (_, index) => makePreviewItem(index + 1))
    renderDialog({ preview: makePreview(items) })
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText(/第 2 \/ 2 页/)).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('搜索类型 / hash / item_key / source'), {
      target: { value: 'hash-' },
    })
    expect(screen.getByText(/第 1 \/ 2 页/)).toBeInTheDocument()
  })
})

describe('MemoryDeleteDialog 执行结果', () => {
  it('执行成功后显示操作 ID 与删除统计，并隐藏确认按钮', () => {
    renderDialog({ result: makeResult() })
    expect(screen.getByText('op-42')).toBeInTheDocument()
    expect(screen.getByText(/实体 1，关系 2，段落 3，来源 0/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /确认删除/ })).not.toBeInTheDocument()
  })

  it('提供 onRestore 时显示恢复按钮，点击触发回调', () => {
    const onRestore = vi.fn()
    renderDialog({ result: makeResult(), onRestore })
    const restoreButton = screen.getByRole('button', { name: /恢复本次删除/ })
    fireEvent.click(restoreButton)
    expect(onRestore).toHaveBeenCalledTimes(1)
  })

  it('恢复进行中时按钮禁用并显示进行中文案', () => {
    renderDialog({ result: makeResult(), onRestore: vi.fn(), restoring: true })
    const restoreButton = screen.getByRole('button', { name: /恢复中/ })
    expect(restoreButton).toBeDisabled()
  })

  it('执行失败（success 为 false）时仍保留确认删除按钮', () => {
    renderDialog({
      preview: makePreview([makePreviewItem(1)]),
      result: makeResult({ success: false }),
    })
    expect(screen.getByRole('button', { name: /确认删除/ })).toBeInTheDocument()
    expect(screen.queryByText('op-42')).not.toBeInTheDocument()
  })
})
