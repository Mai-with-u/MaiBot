import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MCPSettingsPage } from '../mcp-settings'
import * as configApi from '@/lib/config-api'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => ({ isRestarting: false, triggerRestart: vi.fn() }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))
vi.mock('@/lib/field-hooks', () => ({ fieldHooks: { register: vi.fn(), unregister: vi.fn() } }))

// DynamicConfigForm stub：暴露 onChange，以驱动草稿编辑 → 脏跟踪
vi.mock('@/components/dynamic-form', () => ({
  DynamicConfigForm: ({ onChange }: { onChange: (path: string, value: unknown) => void }) => (
    <button type="button" onClick={() => onChange('mcp.enabled', true)}>edit-field</button>
  ),
}))

vi.mock('@/lib/config-api', () => ({
  getBotConfig: vi.fn(),
  getBotConfigSchema: vi.fn(),
  updateBotConfigSection: vi.fn(),
}))

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.mocked(configApi.getBotConfig).mockResolvedValue({ mcp: { enabled: false, servers: [] } } as never)
  vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
    nested: { mcp: { className: 'MCP', classDoc: 'MCP 设置', fields: [], nested: {} } },
  } as never)
  vi.mocked(configApi.updateBotConfigSection).mockResolvedValue({} as never)
})

function renderPage() {
  render(<MCPSettingsPage />, { wrapper: makeWrapper() })
}

describe('MCPSettingsPage 特征化', () => {
  it('初始加载 config + schema 并渲染（未改动时按钮为「已保存」）', async () => {
    renderPage()
    await waitFor(() => expect(configApi.getBotConfig).toHaveBeenCalled())
    expect(configApi.getBotConfigSchema).toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: '已保存' })).toBeDisabled()
  })

  it('编辑字段后脏跟踪翻转，保存按钮变为「保存」', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('edit-field'))
    expect(await screen.findByRole('button', { name: '保存' })).toBeEnabled()
  })

  it('保存调用 updateBotConfigSection(mcp, ...)', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('edit-field'))
    await user.click(await screen.findByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('mcp', expect.objectContaining({ enabled: true })),
    )
  })
})
