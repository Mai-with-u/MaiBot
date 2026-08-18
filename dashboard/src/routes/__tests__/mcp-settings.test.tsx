import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MCPSettingsPage } from '../mcp-settings'
import * as configApi from '@/lib/config-api'
import { fieldHooks } from '@/lib/field-hooks'
import * as mcpApi from '@/lib/mcp-api'
import type { MCPConnectionTestResponse, MCPServerStatus } from '@/lib/mcp-api'

afterEach(() => {
  cleanup()
  fieldHooks.clear()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useRestart: () => ({ isRestarting: false, triggerRestart: vi.fn() }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))

// 保留 edit-field 以驱动脏跟踪，同时把页面注册的 Roots hook 渲染出来
vi.mock('@/components/dynamic-form', async () => {
  const { fieldHooks: hooks } = await import('@/lib/field-hooks')

  return {
    DynamicConfigForm: ({
      onChange,
      values,
    }: {
      onChange: (path: string, value: unknown) => void
      values?: Record<string, unknown>
    }) => {
      const Hook = hooks.get('mcp.client.roots.items')?.component
      const mcp =
        values?.mcp && typeof values.mcp === 'object' && !Array.isArray(values.mcp)
          ? (values.mcp as Record<string, unknown>)
          : {}
      const client =
        mcp.client && typeof mcp.client === 'object' && !Array.isArray(mcp.client)
          ? (mcp.client as Record<string, unknown>)
          : {}
      const roots =
        client.roots && typeof client.roots === 'object' && !Array.isArray(client.roots)
          ? (client.roots as Record<string, unknown>)
          : {}

      return (
        <div>
          <button type="button" onClick={() => onChange('mcp.enabled', true)}>
            edit-field
          </button>
          {Hook ? (
            <Hook
              fieldPath="mcp.client.roots.items"
              value={roots.items}
              onChange={(next) => onChange('mcp.client.roots.items', next)}
            />
          ) : null}
        </div>
      )
    },
  }
})

vi.mock('@/lib/config-api', () => ({
  getBotConfig: vi.fn(),
  getBotConfigSchema: vi.fn(),
  updateBotConfigSection: vi.fn(),
}))
vi.mock('@/lib/mcp-api', () => ({
  getMCPStatus: vi.fn(),
  testMCPConnection: vi.fn(),
}))

// Radix Select 在 jsdom 下需要 PointerCapture
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function makeServer(overrides: Record<string, unknown> = {}) {
  return {
    name: 'demo',
    enabled: true,
    transport: 'stdio',
    command: 'uvx',
    args: [],
    env: {},
    url: '',
    headers: {},
    http_timeout_seconds: 30,
    read_timeout_seconds: 300,
    authorization: { mode: 'none', bearer_token: '' },
    ...overrides,
  }
}

function makeRuntimeServer(overrides: Partial<MCPServerStatus> = {}): MCPServerStatus {
  return {
    name: 'demo',
    transport: 'stdio',
    connected: true,
    protocol_version: '2024-11-05',
    tool_count: 0,
    error: '',
    ...overrides,
  }
}

function mockMcpConfig(mcp: Record<string, unknown>) {
  vi.mocked(configApi.getBotConfig).mockResolvedValue({ mcp } as never)
}

function renderPage() {
  render(<MCPSettingsPage />, { wrapper: makeWrapper() })
}

beforeEach(() => {
  vi.mocked(configApi.getBotConfig).mockResolvedValue({
    mcp: { enabled: false, servers: [] },
  } as never)
  vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
    nested: { mcp: { className: 'MCP', classDoc: 'MCP 设置', fields: [], nested: {} } },
  } as never)
  vi.mocked(configApi.updateBotConfigSection).mockResolvedValue({} as never)
  vi.mocked(mcpApi.getMCPStatus).mockResolvedValue({
    initialized: true,
    server_count: 0,
    tool_count: 0,
    servers: [],
  })
  vi.mocked(mcpApi.testMCPConnection).mockReset()
})

describe('MCPSettingsPage 特征化', () => {
  it('初始加载 config + schema 并渲染（未改动时按钮为「已应用」）', async () => {
    renderPage()
    await waitFor(() => expect(configApi.getBotConfig).toHaveBeenCalled())
    expect(configApi.getBotConfigSchema).toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: '已应用' })).toBeDisabled()
    expect(screen.getByText('尚未配置 MCP 服务。添加一个服务后，MaiSaka 可以调用它暴露的工具。')).toBeInTheDocument()
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

describe('MCPSettingsPage 连接状态与工具列表', () => {
  it('运行时已连接时展示服务数，空工具列表显示 0 工具', async () => {
    // seed 兼容 { config: { mcp } } 信封，避免只覆盖裸对象回退
    vi.mocked(configApi.getBotConfig).mockResolvedValue({
      config: { mcp: { servers: [makeServer({ name: 'weather' })] } },
    } as never)
    vi.mocked(mcpApi.getMCPStatus).mockResolvedValue({
      initialized: true,
      server_count: 1,
      tool_count: 0,
      servers: [makeRuntimeServer({ name: 'weather', connected: true, tool_count: 0 })],
    })

    renderPage()

    expect(await screen.findByText('已连接 · 0 工具')).toBeInTheDocument()
    expect(screen.getByText(/当前已连接 1 个服务，共\s*0\s*个工具/)).toBeInTheDocument()
  })

  it('运行时未连接时显示连接异常', async () => {
    mockMcpConfig({ servers: [makeServer({ name: 'weather' })] })
    vi.mocked(mcpApi.getMCPStatus).mockResolvedValue({
      initialized: true,
      server_count: 0,
      tool_count: 0,
      servers: [
        makeRuntimeServer({
          name: 'weather',
          connected: false,
          tool_count: 0,
          error: 'handshake failed',
        }),
      ],
    })

    renderPage()

    const badge = await screen.findByText('连接异常')
    expect(badge).toHaveAttribute('title', 'handshake failed')
    expect(screen.queryByText(/已连接 ·/)).not.toBeInTheDocument()
  })

  it('未初始化时不展示已连接服务摘要', async () => {
    vi.mocked(mcpApi.getMCPStatus).mockResolvedValue({
      initialized: false,
      server_count: 2,
      tool_count: 5,
      servers: [],
    })

    renderPage()

    await screen.findByRole('button', { name: '已应用' })
    expect(screen.queryByText(/当前已连接/)).not.toBeInTheDocument()
  })

  it('禁用服务相当于断开配置，无效项不再阻止保存', async () => {
    const user = userEvent.setup()
    mockMcpConfig({
      servers: [makeServer({ name: 'broken', command: '' })],
    })
    renderPage()

    expect(await screen.findByText('stdio 模式必须填写启动命令')).toBeInTheDocument()
    // 载入即无效时草稿未脏，保存按钮仍是「已应用」
    expect(screen.getByRole('button', { name: '已应用' })).toBeDisabled()

    await user.click(screen.getAllByRole('switch')[0])
    expect(screen.getByText('禁用')).toBeInTheDocument()
    expect(screen.queryByText('stdio 模式必须填写启动命令')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存并应用' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: '保存并应用' }))
    await waitFor(() => expect(configApi.updateBotConfigSection).toHaveBeenCalled())
    const saved = vi.mocked(configApi.updateBotConfigSection).mock.calls[0][1] as {
      servers: Array<{ enabled: boolean; name: string }>
    }
    expect(saved.servers[0]).toMatchObject({ name: 'broken', enabled: false })
    expect(saved.servers[0]).not.toHaveProperty('_uuid')
  })
})

describe('MCPSettingsPage 远程 URL 校验', () => {
  it('空 URL、非法协议与格式错误分别提示，并禁用测试', async () => {
    mockMcpConfig({
      servers: [
        makeServer({
          name: 'remote',
          transport: 'streamable_http',
          command: '',
          url: '',
        }),
      ],
    })
    renderPage()

    const urlInput = await screen.findByPlaceholderText('https://example.com/mcp')
    const testButton = screen.getByRole('button', { name: '测试' })

    expect(screen.getByText('远程传输必须填写服务 URL')).toBeInTheDocument()
    expect(testButton).toBeDisabled()
    expect(testButton).toHaveAttribute('title', '远程传输必须填写服务 URL')

    fireEvent.change(urlInput, { target: { value: 'not-a-url' } })
    expect(await screen.findByText('服务 URL 格式不正确')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试' })).toBeDisabled()

    fireEvent.change(urlInput, { target: { value: 'ftp://example.com/mcp' } })
    expect(await screen.findByText('服务 URL 必须使用 http 或 https')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试' })).toBeDisabled()

    fireEvent.change(urlInput, { target: { value: 'https://example.com/mcp' } })
    await waitFor(() => {
      expect(screen.queryByText('服务 URL 必须使用 http 或 https')).not.toBeInTheDocument()
      expect(screen.queryByText('服务 URL 格式不正确')).not.toBeInTheDocument()
      expect(screen.queryByText('远程传输必须填写服务 URL')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '测试' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '测试' })).toHaveAttribute(
      'title',
      '测试连接并发现工具'
    )
  })

  it('切换到远程 HTTP 后要求填写 URL', async () => {
    const user = userEvent.setup()
    mockMcpConfig({ servers: [makeServer({ name: 'local', command: 'uvx' })] })
    renderPage()

    await screen.findByDisplayValue('local')
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '远程 HTTP' }))

    expect(await screen.findByPlaceholderText('https://example.com/mcp')).toBeInTheDocument()
    expect(screen.getByText('远程传输必须填写服务 URL')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试' })).toBeDisabled()
  })
})

describe('MCPSettingsPage 测试连接', () => {
  it('连接成功且工具列表为空时只展示发现 0 个工具', async () => {
    const user = userEvent.setup()
    mockMcpConfig({ servers: [makeServer({ name: 'weather' })] })
    let resolveTest!: (value: MCPConnectionTestResponse) => void
    vi.mocked(mcpApi.testMCPConnection).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveTest = resolve
        })
    )

    renderPage()
    const testButton = await screen.findByRole('button', { name: '测试' })
    await user.click(testButton)

    expect(screen.getByRole('button', { name: '测试' })).toBeDisabled()

    resolveTest({
      success: true,
      error: '',
      protocol_version: '2024-11-05',
      tools: [],
    })

    expect(await screen.findByText(/连接成功/)).toBeInTheDocument()
    expect(screen.getByText(/协议 2024-11-05/)).toBeInTheDocument()
    expect(screen.getByText(/发现 0 个工具/)).toBeInTheDocument()
    await waitFor(() => expect(mcpApi.testMCPConnection).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(mcpApi.testMCPConnection).mock.calls[0][0]
    expect(payload).not.toHaveProperty('_uuid')
    expect(payload).toEqual(
      expect.objectContaining({
        name: 'weather',
        transport: 'stdio',
        command: 'uvx',
      })
    )
  })

  it('连接成功时按 title 或 name 列出工具', async () => {
    const user = userEvent.setup()
    mockMcpConfig({ servers: [makeServer({ name: 'weather' })] })
    vi.mocked(mcpApi.testMCPConnection).mockResolvedValue({
      success: true,
      error: '',
      protocol_version: '',
      tools: [
        { name: 'search', title: '搜索', description: '', read_only: null, destructive: null },
        { name: 'lookup', title: '', description: '', read_only: null, destructive: null },
      ],
    })

    renderPage()
    await user.click(await screen.findByRole('button', { name: '测试' }))

    expect(await screen.findByText(/发现 2 个工具/)).toBeInTheDocument()
    expect(screen.getByText('搜索、lookup')).toBeInTheDocument()
    expect(screen.queryByText(/协议 /)).not.toBeInTheDocument()
  })

  it('测试返回失败时展示接口错误', async () => {
    const user = userEvent.setup()
    mockMcpConfig({ servers: [makeServer({ name: 'weather' })] })
    vi.mocked(mcpApi.testMCPConnection).mockResolvedValue({
      success: false,
      error: '远程拒绝连接',
      protocol_version: '',
      tools: [],
    })

    renderPage()
    await user.click(await screen.findByRole('button', { name: '测试' }))

    expect(await screen.findByText('远程拒绝连接')).toBeInTheDocument()
    expect(screen.queryByText(/连接成功/)).not.toBeInTheDocument()
  })

  it('测试抛出 Error 时展示 message，非 Error 回退为连接测试失败', async () => {
    const user = userEvent.setup()
    mockMcpConfig({
      servers: [
        makeServer({ name: 'alpha', command: 'uvx' }),
        makeServer({ name: 'beta', command: 'npx' }),
      ],
    })
    vi.mocked(mcpApi.testMCPConnection)
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockRejectedValueOnce('timeout')

    renderPage()
    const testButtons = await screen.findAllByRole('button', { name: '测试' })
    await user.click(testButtons[0])
    expect(await screen.findByText('网络中断')).toBeInTheDocument()

    await user.click(testButtons[1])
    expect(await screen.findByText('连接测试失败')).toBeInTheDocument()
  })
})

describe('MCPSettingsPage 服务编辑与校验', () => {
  it('空名称与启用服务重名会阻止保存和测试', async () => {
    mockMcpConfig({
      servers: [
        makeServer({ name: 'dup', command: 'uvx' }),
        makeServer({ name: 'dup', command: 'npx' }),
      ],
    })
    renderPage()

    expect(await screen.findAllByText('服务名称必须唯一')).toHaveLength(2)
    expect(screen.getByRole('button', { name: '已应用' })).toBeDisabled()
    expect(screen.getAllByRole('button', { name: '测试' })[0]).toBeDisabled()

    fireEvent.change(screen.getAllByPlaceholderText('服务名称，必须唯一')[0], {
      target: { value: '' },
    })
    expect(await screen.findByText('请填写服务名称')).toBeInTheDocument()
    expect(screen.queryByText('服务名称必须唯一')).not.toBeInTheDocument()
  })

  it('Bearer 认证缺少 Token 时提示并禁用测试', async () => {
    const user = userEvent.setup()
    mockMcpConfig({
      servers: [
        makeServer({
          name: 'remote',
          transport: 'streamable_http',
          command: '',
          url: 'https://example.com/mcp',
          authorization: { mode: 'bearer', bearer_token: '' },
        }),
      ],
    })
    renderPage()

    expect(await screen.findByText('Bearer 认证必须填写 Token')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '测试' })).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText('HTTP Bearer Token'), {
      target: { value: 'secret-token' },
    })
    await waitFor(() => {
      expect(screen.queryByText('Bearer 认证必须填写 Token')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '测试' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: '测试' }))
    await waitFor(() => expect(mcpApi.testMCPConnection).toHaveBeenCalled())
    expect(vi.mocked(mcpApi.testMCPConnection).mock.calls[0][0]).toEqual(
      expect.objectContaining({
        authorization: { mode: 'bearer', bearer_token: 'secret-token' },
      })
    )
  })

  it('复制服务会插入 copy 名称，删除后回到空列表', async () => {
    const user = userEvent.setup()
    mockMcpConfig({ servers: [makeServer({ name: 'weather' })] })
    renderPage()

    await screen.findByDisplayValue('weather')
    await user.click(screen.getByTitle('复制服务'))

    expect(await screen.findByDisplayValue('weather-copy')).toBeInTheDocument()
    expect(screen.getByText('2 个')).toBeInTheDocument()

    const deleteButtons = screen.getAllByTitle('删除服务')
    await user.click(deleteButtons[1])
    await user.click(deleteButtons[0])

    expect(
      await screen.findByText('尚未配置 MCP 服务。添加一个服务后，MaiSaka 可以调用它暴露的工具。')
    ).toBeInTheDocument()
    expect(screen.getByText('0 个')).toBeInTheDocument()
  })

  it('加载对象型 env/headers 时按字符串映射展示', async () => {
    mockMcpConfig({
      servers: [
        makeServer({
          name: 'mapped',
          env: { FOO: 'bar', COUNT: 2 },
          headers: { 'X-Trace': '1' },
        }),
      ],
    })

    renderPage()

    expect(await screen.findByDisplayValue('mapped')).toBeInTheDocument()
    expect(screen.getByDisplayValue('FOO')).toBeInTheDocument()
    expect(screen.getByDisplayValue('bar')).toBeInTheDocument()
    expect(screen.getByDisplayValue('COUNT')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2')).toBeInTheDocument()
  })
})

describe('MCPSettingsPage Roots 编辑器', () => {
  it('可添加、启用、填写并删除 Root，缺 URI 时阻止保存', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText('尚未暴露任何 Root。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '添加 Root' }))

    expect(screen.queryByText('尚未暴露任何 Root。')).not.toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('显示名称，例如 project'), 'project')
    await user.click(screen.getByRole('switch', { name: '启用 Root 1' }))

    expect(screen.getByText('启用的 Root 必须填写 URI')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存并应用' })).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText('file:///path/to/project'), {
      target: { value: 'file:///tmp/proj' },
    })
    await waitFor(() => {
      expect(screen.queryByText('启用的 Root 必须填写 URI')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '保存并应用' })).toBeEnabled()

    await user.click(screen.getByTitle('删除 Root'))
    expect(screen.getByText('尚未暴露任何 Root。')).toBeInTheDocument()
  })

  it('规范化已有 Roots，非对象项回退为空 URI 并标红', async () => {
    mockMcpConfig({
      client: {
        roots: {
          items: [{ enabled: true, uri: 'file:///ok', name: 'ok' }, 'invalid', { name: 1 }],
        },
      },
      servers: [],
    })

    renderPage()

    expect(await screen.findByDisplayValue('ok')).toBeInTheDocument()
    expect(screen.getByDisplayValue('file:///ok')).toBeInTheDocument()
    expect(screen.getAllByText('启用的 Root 必须填写 URI')).toHaveLength(2)
    expect(screen.getByRole('switch', { name: '启用 Root 1' })).toBeChecked()
    expect(screen.getByRole('switch', { name: '启用 Root 2' })).toBeChecked()
    expect(screen.getByRole('switch', { name: '启用 Root 3' })).toBeChecked()
  })
})

describe('MCPSettingsPage 加载与 schema', () => {
  it('配置加载失败时展示错误并支持重试', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.getBotConfig)
      .mockRejectedValueOnce(new Error('后端不可用'))
      .mockResolvedValue({ mcp: { enabled: false, servers: [] } } as never)

    renderPage()

    expect(await screen.findByText('后端不可用')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByRole('button', { name: '已应用' })).toBeInTheDocument()
    expect(screen.getByText(/尚未配置 MCP 服务/)).toBeInTheDocument()
  })

  it('schema 中没有 mcp 节时提示当前配置 schema 中没有找到 MCP 设置', async () => {
    vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({ nested: {} } as never)

    renderPage()

    expect(await screen.findByText('当前配置 schema 中没有找到 MCP 设置。')).toBeInTheDocument()
    expect(screen.queryByText('尚未暴露任何 Root。')).not.toBeInTheDocument()
  })
})
