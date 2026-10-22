import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SelfUpdateStatus } from '@/lib/system-api'

import { SelfUpdateCard } from '../SelfUpdateCard'

const api = vi.hoisted(() => ({
  getSelfUpdateStatus: vi.fn(),
  checkSelfUpdate: vi.fn(),
  applySelfUpdate: vi.fn(),
}))
const triggerRestart = vi.hoisted(() => vi.fn())
const toast = vi.hoisted(() => vi.fn())

vi.mock('@/lib/system-api', () => api)
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast }) }))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => ({ triggerRestart, isRestarting: false }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options ? `${key} ${JSON.stringify(options)}` : key,
  }),
}))

const TARGET = 'b'.repeat(40)

function makeStatus(overrides: Partial<SelfUpdateStatus> = {}): SelfUpdateStatus {
  return {
    supported: true,
    repository_url: 'https://github.com/Mai-with-u/MaiBot.git',
    current_version: '1.3.7',
    current_commit: 'a'.repeat(40),
    branch: 'main',
    checked_at: null,
    latest_version: null,
    latest_commit: null,
    behind: 0,
    ahead: 0,
    update_available: false,
    can_apply: false,
    blockers: [],
    dirty_files: [],
    dependencies_changed: false,
    dependency_tool: 'uv',
    commits: [],
    source_id: null,
    source_name: null,
    last_result: null,
    ...overrides,
  }
}

const available = makeStatus({
  checked_at: 1_760_000_000,
  latest_version: '1.3.8',
  latest_commit: TARGET,
  behind: 2,
  update_available: true,
  can_apply: true,
  dependencies_changed: true,
  source_id: 'github',
  source_name: 'GitHub 官方源',
  commits: [
    { short_id: 'bbbbbbb', summary: '修复断句', committed_at: '2026-10-13T10:00:00+08:00' },
    { short_id: 'ccccccc', summary: '新增子代理', committed_at: '2026-10-12T10:00:00+08:00' },
  ],
})

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
})
afterEach(() => cleanup())

async function chooseSourceAndCheck() {
  fireEvent.click(screen.getByRole('button', { name: 'settings.selfUpdate.check' }))
  expect(screen.getByRole('button', { name: '检查更新' })).toBeDisabled()
  fireEvent.keyDown(screen.getByRole('combobox', { name: '更新源' }), { key: 'ArrowDown' })
  fireEvent.click(await screen.findByRole('option', { name: 'GitHub 官方源' }))
  fireEvent.click(screen.getByRole('button', { name: '检查更新' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '选择本体更新源' })).not.toBeInTheDocument())
}

describe('SelfUpdateCard', () => {
  it('检查到新版本后，确认即按界面上的提交发起更新并等待重启', async () => {
    api.getSelfUpdateStatus.mockResolvedValue(makeStatus())
    api.checkSelfUpdate.mockResolvedValue(available)
    api.applySelfUpdate.mockResolvedValue({ success: true, message: 'ok' })
    render(<SelfUpdateCard />)

    expect(await screen.findByText('settings.selfUpdate.notChecked')).toBeInTheDocument()
    await chooseSourceAndCheck()

    expect(await screen.findByText('修复断句')).toBeInTheDocument()
    expect(screen.getByText(/settings\.selfUpdate\.newVersion.*1\.3\.8/)).toBeInTheDocument()
    expect(screen.getByText(/settings\.selfUpdate\.source .*GitHub 官方源/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'settings.selfUpdate.apply' }))
    fireEvent.click(await screen.findByRole('button', { name: 'settings.selfUpdate.confirmAction' }))

    await waitFor(() => expect(triggerRestart).toHaveBeenCalledTimes(1))
    expect(api.checkSelfUpdate).toHaveBeenCalledWith({ source_id: 'github' })
    expect(api.applySelfUpdate).toHaveBeenCalledWith(TARGET, { source_id: 'github' })
    expect(triggerRestart).toHaveBeenCalledWith({ skipApiCall: true, maxAttempts: 450 })
    expect(sessionStorage.getItem('maibot-self-update-pending')).toBe(TARGET)
  })

  it('存在阻止项时列出原因并禁用更新按钮', async () => {
    api.getSelfUpdateStatus.mockResolvedValue({
      ...available,
      can_apply: false,
      blockers: ['dirty_worktree'],
      dirty_files: ['src/main.py'],
    })
    render(<SelfUpdateCard />)

    expect(
      await screen.findByText('settings.selfUpdate.blockers.dirty_worktree')
    ).toBeInTheDocument()
    expect(screen.getByText(/src\/main\.py/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'settings.selfUpdate.apply' })).toBeDisabled()
  })

  it('不支持自更新的部署只说明原因；登记失败时不进入重启等待', async () => {
    api.getSelfUpdateStatus.mockResolvedValue(
      makeStatus({ supported: false, blockers: ['not_git_repo'], branch: null, current_commit: null })
    )
    const { unmount } = render(<SelfUpdateCard />)
    expect(
      await screen.findByText('settings.selfUpdate.blockers.not_git_repo')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'settings.selfUpdate.check' })).toBeDisabled()
    unmount()

    api.getSelfUpdateStatus.mockResolvedValue(available)
    api.checkSelfUpdate.mockResolvedValue(available)
    api.applySelfUpdate.mockRejectedValue(new Error('存在未提交的本地改动'))
    render(<SelfUpdateCard />)
    await screen.findByRole('button', { name: 'settings.selfUpdate.apply' })
    await chooseSourceAndCheck()
    fireEvent.click(await screen.findByRole('button', { name: 'settings.selfUpdate.apply' }))
    fireEvent.click(await screen.findByRole('button', { name: 'settings.selfUpdate.confirmAction' }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ description: '存在未提交的本地改动', variant: 'destructive' })
      )
    )
    expect(triggerRestart).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('maibot-self-update-pending')).toBeNull()
  })

  it('展示上次更新失败与回滚结果', async () => {
    api.getSelfUpdateStatus.mockResolvedValue(
      makeStatus({
        last_result: {
          success: false,
          started_at: 1_760_000_000,
          finished_at: 1_760_000_060,
          branch: 'main',
          from_commit: 'a'.repeat(40),
          to_commit: TARGET,
          from_version: '1.3.7',
          to_version: '1.3.8',
          stage: 'dependencies',
          error_code: 'dependency_failed',
          error: '依赖同步失败: uv sync',
          rolled_back: true,
          rollback_error: '',
          dependencies_synced: false,
          source_name: 'ghproxy.vip 镜像',
        },
      })
    )
    render(<SelfUpdateCard />)

    expect(await screen.findByText('依赖同步失败: uv sync')).toBeInTheDocument()
    expect(screen.getByText('settings.selfUpdate.rolledBack')).toBeInTheDocument()
  })
})
