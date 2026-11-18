import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { LearningExportDialog } from './learning-export-dialog'

const toast = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast }) }))

afterEach(cleanup)
beforeEach(() => {
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

function setup(kind: 'expression' | 'jargon', selectedIds = new Set<number>()) {
  const onExport = vi.fn().mockResolvedValue({ count: 2 })
  const onBack = vi.fn()
  render(
    <LearningExportDialog
      kind={kind}
      selectedIds={selectedIds}
      chats={[{ id: 'chat-a', name: '测试群' }]}
      groups={[]}
      loading={false}
      error={false}
      onRetry={vi.fn()}
      onBack={onBack}
      onExport={onExport}
    />
  )
  return { onExport, onBack }
}

it.each(['expression', 'jargon'] as const)('%s 未勾选也能打开卡片并导出全部', async (kind) => {
  const { onExport } = setup(kind)
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: '已选内容（0 个）' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '导出 JSON' }))
  await waitFor(() =>
    expect(onExport).toHaveBeenCalledWith({
      ids: undefined,
      chatIds: undefined,
      includeChatInfo: false,
    })
  )
})

it('导出跨聊天勾选的内容无需选择聊天', async () => {
  const { onExport } = setup('expression', new Set([1, 2]))
  fireEvent.click(screen.getByRole('button', { name: '导出 JSON' }))
  await waitFor(() =>
    expect(onExport).toHaveBeenCalledWith({
      ids: [1, 2],
      chatIds: undefined,
      includeChatInfo: false,
    })
  )
})

it('按聊天或共享组导出必须先选择目标', () => {
  const { onExport } = setup('jargon')
  fireEvent.click(screen.getByRole('radio', { name: '按聊天导出' }))
  expect(screen.getByRole('combobox', { name: '选择导出聊天' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '导出 JSON' })).toBeDisabled()
  fireEvent.click(screen.getByRole('radio', { name: '按共享组导出' }))
  expect(screen.getByText('暂无可选的共享组。')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '导出 JSON' })).toBeDisabled()
  expect(onExport).not.toHaveBeenCalled()
})

it('导出失败保留卡片并显示后端错误', async () => {
  const { onExport, onBack } = setup('jargon')
  onExport.mockRejectedValue(new Error('部分黑话不存在'))
  fireEvent.click(screen.getByRole('button', { name: '导出 JSON' }))
  await waitFor(() =>
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '导出失败',
        description: '部分黑话不存在',
      })
    )
  )
  expect(onBack).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: '导出 JSON' })).toBeEnabled()
})
