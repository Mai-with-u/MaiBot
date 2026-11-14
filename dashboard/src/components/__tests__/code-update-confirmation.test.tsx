import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CodeUpdateConfirmation } from '@/components/code-update-confirmation'
import { ApiError, backendApi } from '@/lib/http'
import { updatePlugin } from '@/lib/plugin-api/install-flow'
import { confirmCodeUpdate, resolveUpdateConfirmation } from '@/lib/update-confirmation'

vi.mock('@/lib/http', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/http')>(),
  backendApi: { post: vi.fn() },
}))

afterEach(() => { resolveUpdateConfirmation(false); vi.clearAllMocks() })

const target = 'a'.repeat(40)
const conflict = new ApiError('local changes', {
  status: 409,
  detail: { detail: { code: 'local_changes', message: '存在本地修改', files: ['plugin.py'], diverged: true, target_commit: target } },
})

describe('备份并强制更新确认', () => {
  it('显示冲突，确认后才携带目标提交强制重试', async () => {
    const response = { success: true, old_version: '1', new_version: '2', backup_path: '/plugins/.update_backups/demo' }
    vi.mocked(backendApi.post).mockRejectedValueOnce(conflict).mockResolvedValueOnce(response)
    render(<CodeUpdateConfirmation />)
    const operation = updatePlugin('demo', 'https://github.com/test/demo')
    await screen.findByText('plugin.py')
    expect(backendApi.post).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/本地提交与目标版本分叉/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '备份并强制更新' }))
    await expect(operation).resolves.toBe(response)
    expect(backendApi.post).toHaveBeenLastCalledWith('/api/webui/plugins/update', expect.objectContaining({
      body: expect.objectContaining({ force: true, expected_commit: target }),
    }))
  })

  it('取消确认不发送强制请求', async () => {
    vi.mocked(backendApi.post).mockRejectedValueOnce(conflict)
    render(<CodeUpdateConfirmation />)
    const operation = updatePlugin('demo', 'https://github.com/test/demo').catch((error: unknown) => error)
    await screen.findByText('plugin.py')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(await operation).toBe(conflict)
    expect(backendApi.post).toHaveBeenCalledTimes(1)
  })

  it('请求中止时关闭待确认弹窗', async () => {
    const controller = new AbortController()
    render(<CodeUpdateConfirmation />)
    let operation: Promise<boolean>
    act(() => {
      operation = confirmCodeUpdate({ message: '本地修改', files: ['plugin.py'], diverged: false }, controller.signal)
    })
    expect(screen.getByText(/将备份插件代码和配置/)).toBeInTheDocument()
    act(() => controller.abort())
    expect(await operation!).toBe(false)
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })
})
