import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getMemoryBundles, getMemoryImportTasks, getMemorySources } from '@/lib/memory-api'

import { MemoryBundleCard } from '../MemoryBundleCard'

if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}

vi.mock('@/lib/memory-api', () => ({
  downloadMemoryBundle: vi.fn(),
  exportMemoryBundle: vi.fn(),
  getMemoryBundles: vi.fn(),
  getMemoryImportTasks: vi.fn(),
  getMemorySources: vi.fn(),
  importMemoryBundle: vi.fn(),
  uninstallMemoryBundle: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getMemoryBundles).mockResolvedValue({
    success: true,
    count: 1,
    items: [
      {
        installation_id: 'install-1',
        package_id: 'example.bundle',
        version: '1.0.0',
        name: '示例知识包',
        content_level: 'knowledge',
        content_digest: 'digest-1',
        scope_type: 'global',
        scope_key: 'global',
        status: 'installed',
        installed_at: 1,
        updated_at: 1,
        paragraph_count: 2,
      },
    ],
  })
  vi.mocked(getMemorySources).mockResolvedValue({
    success: true,
    count: 1,
    items: [{ source: 'manual:test', count: 3 }],
  })
  vi.mocked(getMemoryImportTasks).mockResolvedValue({
    success: true,
    count: 1,
    items: [
      {
        task_id: 'task-example-123456',
        source: 'upload',
        status: 'completed',
        current_step: 'completed',
        total_chunks: 1,
        done_chunks: 1,
        failed_chunks: 0,
        cancelled_chunks: 0,
        progress: 100,
        error: '',
        file_count: 1,
        created_at: 1,
        updated_at: 1,
      },
    ],
  })
})

describe('MemoryBundleCard', () => {
  it('挂载后自动读取并展示已安装知识包', async () => {
    render(<MemoryBundleCard chatTargets={[]} />)

    await waitFor(() => {
      expect(getMemoryBundles).toHaveBeenCalledWith(50)
    })
    expect(await screen.findByText('示例知识包')).toBeInTheDocument()
    expect(screen.getByText('example.bundle · 1.0.0 · 2 条')).toBeInTheDocument()
  })

  it('为来源、导入任务和已安装知识包提供候选下拉列表', async () => {
    const user = userEvent.setup()
    render(<MemoryBundleCard chatTargets={[]} />)

    await waitFor(() => {
      expect(getMemorySources).toHaveBeenCalledOnce()
      expect(getMemoryImportTasks).toHaveBeenCalledWith(200)
    })

    const rangeSelect = screen.getByLabelText('记忆包导出范围')
    await user.click(rangeSelect)
    await user.click(screen.getByRole('option', { name: '指定来源' }))
    await user.click(screen.getByLabelText('记忆包导出来源'))
    expect(screen.getByRole('option', { name: 'manual:test · 3 条段落' })).toBeInTheDocument()
    await user.keyboard('{Escape}')

    await user.click(rangeSelect)
    await user.click(screen.getByRole('option', { name: '指定导入任务' }))
    await user.click(screen.getByLabelText('记忆包导出任务'))
    expect(
      screen.getByRole('option', { name: 'upload · task-example · 已完成' })
    ).toBeInTheDocument()
    await user.keyboard('{Escape}')

    await user.click(rangeSelect)
    await user.click(screen.getByRole('option', { name: '已安装知识包' }))
    await user.click(screen.getByLabelText('记忆包导出知识包'))
    expect(screen.getByRole('option', { name: '示例知识包 · 1.0.0 · 全局' })).toBeInTheDocument()
  })
})
