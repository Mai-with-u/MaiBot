import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CommandPermissions } from './CommandPermissions'

import { getChatStreams, type ChatStream } from '@/lib/chat-management-api'
import { getRuntimeCommands, type RuntimeCommand } from '@/lib/plugin-api'

vi.mock('@/lib/plugin-api', () => ({
  getRuntimeCommands: vi.fn(),
}))

vi.mock('@/lib/chat-management-api', () => ({
  getChatStreams: vi.fn(),
}))

// MultiSelect 是 Radix 浮层，jsdom 下交互受限，改为按钮驱动 onChange
vi.mock('@/components/ui/multi-select', () => ({
  MultiSelect: ({
    emptyText,
    onChange,
    options,
    placeholder,
    selected,
  }: {
    emptyText?: string
    onChange: (values: string[]) => void
    options: Array<{ label: string; value: string }>
    placeholder?: string
    selected: string[]
  }) => (
    <div data-empty-text={emptyText} data-testid="command-chats">
      <span>{selected.length === 0 ? placeholder : selected.join(',')}</span>
      {options.map((option) => (
        <button key={option.value} type="button" onClick={() => onChange([...selected, option.value])}>
          {option.label}
        </button>
      ))}
    </div>
  ),
}))

const getRuntimeCommandsMock = vi.mocked(getRuntimeCommands)
const getChatStreamsMock = vi.mocked(getChatStreams)

const operatorCommand: RuntimeCommand = {
  aliases: ['k'],
  description: '踢出用户',
  enabled: true,
  id: 'cmd-kick',
  name: 'kick',
  pattern: '/kick <user>',
  permission: 'operator',
  plugin_name: '管理插件',
}

const publicCommand: RuntimeCommand = {
  aliases: [],
  description: '',
  enabled: true,
  id: 'cmd-help',
  name: 'help',
  pattern: '/help',
  permission: 'public',
  plugin_name: '核心插件',
}

const chatStreams = [
  { display_name: '测试群', platform: 'qq', session_id: 'session-group' },
  { display_name: '小明的私聊', platform: 'qq', session_id: 'session-private' },
] as ChatStream[]

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  getRuntimeCommandsMock.mockResolvedValue([operatorCommand, publicCommand])
  getChatStreamsMock.mockResolvedValue(chatStreams)
})

function renderPermissions(
  pluginSection: Record<string, unknown> | null = {},
  onChange = vi.fn()
) {
  const view = render(<CommandPermissions pluginSection={pluginSection} onChange={onChange} />)
  return { ...view, onChange }
}

describe('CommandPermissions', () => {
  it('加载失败时展示错误信息', async () => {
    getRuntimeCommandsMock.mockRejectedValue(new Error('网络断开'))

    renderPermissions()

    expect(await screen.findByText('网络断开')).toBeInTheDocument()
  })

  it('加载失败且原因不是 Error 时展示默认文案', async () => {
    getChatStreamsMock.mockRejectedValue('timeout')

    renderPermissions()

    expect(await screen.findByText('加载命令失败')).toBeInTheDocument()
  })

  it('加载成功后列出命令并默认选中第一条', async () => {
    renderPermissions()

    const kickButton = await screen.findByRole('button', { name: /\/kick/ })
    const helpButton = screen.getByRole('button', { name: /\/help/ })

    expect(kickButton).toHaveTextContent('受保护')
    expect(kickButton).toHaveTextContent('管理插件')
    expect(helpButton).toHaveTextContent('公开')
    expect(helpButton).toHaveTextContent('核心插件')
    expect(screen.getByRole('heading', { name: '/kick' })).toBeInTheDocument()
    expect(screen.getByText('踢出用户')).toBeInTheDocument()
    expect(screen.getByText('/kick <user>')).toBeInTheDocument()
    expect(screen.getByText('测试群 · qq')).toBeInTheDocument()
    expect(screen.getByText('小明的私聊 · qq')).toBeInTheDocument()
    expect(screen.getByText('选择群聊或私聊')).toBeInTheDocument()
  })

  it('命令列表为空时提示选择命令', async () => {
    getRuntimeCommandsMock.mockResolvedValue([])

    renderPermissions()

    expect(await screen.findByText('请选择一个命令')).toBeInTheDocument()
    expect(screen.getByText('没有匹配的命令')).toBeInTheDocument()
  })

  it('搜索可按名称、描述和插件名过滤命令', async () => {
    renderPermissions()
    const search = await screen.findByLabelText('搜索命令或插件')

    fireEvent.change(search, { target: { value: 'KICK' } })
    expect(screen.getByRole('button', { name: /\/kick/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /\/help/ })).not.toBeInTheDocument()

    fireEvent.change(search, { target: { value: '踢出' } })
    expect(screen.getByRole('button', { name: /\/kick/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /\/help/ })).not.toBeInTheDocument()

    fireEvent.change(search, { target: { value: '核心' } })
    expect(screen.getByRole('button', { name: /\/help/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /\/kick/ })).not.toBeInTheDocument()

    fireEvent.change(search, { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: /\/kick/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /\/help/ })).toBeInTheDocument()
  })

  it('没有匹配的命令时展示空状态且保留当前选中命令', async () => {
    renderPermissions()
    const search = await screen.findByLabelText('搜索命令或插件')

    fireEvent.change(search, { target: { value: '不存在的命令' } })

    expect(screen.getByText('没有匹配的命令')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '/kick' })).toBeInTheDocument()
  })

  it('点击命令切换选中项，公开命令展示无需授权提示', async () => {
    renderPermissions()
    await screen.findByRole('heading', { name: '/kick' })

    fireEvent.click(screen.getByRole('button', { name: /\/help/ }))

    expect(screen.getByRole('heading', { name: '/help' })).toBeInTheDocument()
    expect(screen.getByText('该命令未提供说明。')).toBeInTheDocument()
    expect(screen.getByText('/help', { selector: 'code' })).toBeInTheDocument()
    expect(screen.getByText('公开命令')).toBeInTheDocument()
    expect(screen.getByText('此命令由插件声明为所有用户可用，不需要额外授权。')).toBeInTheDocument()
    expect(screen.queryByLabelText('全局管理员')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('仅为此命令放行用户')).not.toBeInTheDocument()
  })

  it('空 pluginSection 下修改放行用户会写入 command_permissions', async () => {
    const { onChange } = renderPermissions(null)
    await screen.findByRole('heading', { name: '/kick' })

    expect(screen.getByLabelText('全局管理员')).toHaveValue('')
    expect(screen.getByLabelText('仅为此命令放行用户')).toHaveValue('')

    fireEvent.change(screen.getByLabelText('仅为此命令放行用户'), {
      target: { value: ' qq:234567890，qq:111, qq:234567890 ' },
    })

    expect(onChange).toHaveBeenCalledWith({
      command_permissions: {
        'cmd-kick': {
          allow_chats: [],
          allow_users: ['qq:234567890', 'qq:111'],
        },
      },
    })
  })

  it('修改放行用户和放行聊天时调用 onChange 并保留已有权限', async () => {
    const pluginSection = {
      enabled: true,
      command_permissions: {
        'cmd-kick': {
          allow_chats: ['session-group'],
          allow_users: ['qq:111'],
        },
      },
    }
    const { onChange } = renderPermissions(pluginSection)
    await screen.findByRole('heading', { name: '/kick' })

    expect(screen.getByLabelText('仅为此命令放行用户')).toHaveValue('qq:111')
    expect(screen.getByTestId('command-chats')).toHaveTextContent('session-group')

    fireEvent.change(screen.getByLabelText('仅为此命令放行用户'), {
      target: { value: 'qq:999' },
    })
    expect(onChange).toHaveBeenCalledWith({
      enabled: true,
      command_permissions: {
        'cmd-kick': {
          allow_chats: ['session-group'],
          allow_users: ['qq:999'],
        },
      },
    })

    fireEvent.click(screen.getByRole('button', { name: '小明的私聊 · qq' }))
    expect(onChange).toHaveBeenCalledWith({
      enabled: true,
      command_permissions: {
        'cmd-kick': {
          allow_chats: ['session-group', 'session-private'],
          allow_users: ['qq:111'],
        },
      },
    })
  })

  it('展示全局管理员并允许修改', async () => {
    const pluginSection = {
      command_permissions: {},
      permission: ['qq:admin1', 'qq:admin2'],
    }
    const { onChange } = renderPermissions(pluginSection)
    await screen.findByRole('heading', { name: '/kick' })

    const operators = screen.getByLabelText('全局管理员')
    expect(operators).toHaveValue('qq:admin1, qq:admin2')

    fireEvent.change(operators, { target: { value: 'qq:9 qq:8' } })
    expect(onChange).toHaveBeenCalledWith({
      command_permissions: {},
      permission: ['qq:9', 'qq:8'],
    })
  })

  it('permission 非数组时全局管理员为空', async () => {
    renderPermissions({ permission: 'qq:admin' })
    await screen.findByRole('heading', { name: '/kick' })

    expect(screen.getByLabelText('全局管理员')).toHaveValue('')
  })
})
