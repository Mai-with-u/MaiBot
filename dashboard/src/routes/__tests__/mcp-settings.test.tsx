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
    <button type="button" onClick={() => onChange('mcp.enabled', true)}>
      edit-field
    </button>
  ),
}))

vi.mock('@/lib/config-api', () => ({
  getBotConfig: vi.fn(),
  getBotConfigSchema: vi.fn(),
  updateBotConfigSection: vi.fn(),
}))
vi.mock('@/lib/mcp-api', () => ({
  getMCPStatus: vi.fn().mockResolvedValue({
    initialized: true,
    server_count: 0,
    tool_count: 0,
    servers: [],
  }),
  testMCPConnection: vi.fn(),
}))

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.mocked(configApi.getBotConfig).mockResolvedValue({
    mcp: { enabled: false, servers: [] },
  } as never)
  vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
    nested: { mcp: { className: 'MCP', classDoc: 'MCP 设置', fields: [], nested: {} } },
  } as never)
  vi.mocked(configApi.updateBotConfigSection).mockResolvedValue({} as never)
})

function renderPage() {
  render(<MCPSettingsPage />, { wrapper: makeWrapper() })
}

describe('MCPSettingsPage 特征化', () => {
  it('初始加载 config + schema 并渲染（未改动时按钮为「已应用」）', async () => {
    renderPage()
    await waitFor(() => expect(configApi.getBotConfig).toHaveBeenCalled())
    expect(configApi.getBotConfigSchema).toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: '已应用' })).toBeDisabled()
  })

  it('编辑字段后脏跟踪翻转，保存按钮变为「保存并应用」', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('edit-field'))
    expect(await screen.findByRole('button', { name: '保存并应用' })).toBeEnabled()
  })

  it('保存调用 updateBotConfigSection(mcp, ...)', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText('edit-field'))
    await user.click(await screen.findByRole('button', { name: '保存并应用' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith(
        'mcp',
        expect.objectContaining({ enabled: true })
      )
    )
  })

  it('编辑旧版 SSE 服务时保留 transport，不会静默改写为 stdio', async () => {
    vi.mocked(configApi.getBotConfig).mockResolvedValue({
      mcp: {
        enabled: true,
        servers: [
          {
            name: 'legacy-sse',
            enabled: true,
            transport: 'sse',
            command: '',
            args: [],
            env: {},
            url: 'https://example.com/sse',
            headers: {},
            http_timeout_seconds: 30,
            read_timeout_seconds: 300,
            authorization: { mode: 'none', bearer_token: '' },
          },
        ],
      },
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click((await screen.findAllByRole('switch'))[0])
    await user.click(await screen.findByRole('button', { name: '保存并应用' }))

    await waitFor(() => expect(configApi.updateBotConfigSection).toHaveBeenCalled())
    const savedConfig = vi.mocked(configApi.updateBotConfigSection).mock.calls[0][1] as {
      servers: Array<{ transport: string }>
    }
    expect(savedConfig.servers[0].transport).toBe('sse')
  })

  it('新增但未填写命令的启用服务会阻止保存', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '添加服务' }))

    expect(await screen.findByText('stdio 模式必须填写启动命令')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存并应用' })).toBeDisabled()
  })
})
