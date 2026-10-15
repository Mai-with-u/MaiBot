/** 聊天管理页共享组的可见行为与配置写入。 */
import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatManagementPage } from '../chat-management'
import * as chatApi from '@/lib/chat-management-api'
import * as configApi from '@/lib/config-api'
import type { ChatStream } from '@/lib/chat-management-api'

const toastMock = vi.fn()
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@/lib/chat-management-api', () => ({ getChatStreams: vi.fn() }))
vi.mock('@/lib/config-api', () => ({
  getBotConfig: vi.fn(),
  updateBotConfigSection: vi.fn(),
}))

/** 构造一个字段完整的聊天流（按需覆盖）。 */
function makeChat(id: number, overrides: Partial<ChatStream> = {}): ChatStream {
  return {
    id,
    session_id: `sess-${id}`,
    display_name: `聊天流${id}`,
    chat_type: 'group',
    target_id: `${10000 + id}`,
    platform: 'qq',
    account_id: null,
    scope: null,
    user_id: null,
    user_nickname: null,
    user_cardname: null,
    group_id: `${10000 + id}`,
    group_name: `聊天流${id}`,
    message_count: 10 * id,
    expression_count: id,
    jargon_count: 0,
    created_at: null,
    last_active_at: null,
    latest_message: '',
    latest_message_at: null,
    ...overrides,
  }
}

// 两条基础聊天流：群聊带账号后缀，私聊在 telegram 平台
const groupChat = makeChat(1, {
  display_name: '测试群',
  account_id: '123',
  target_id: '10001',
  group_id: '10001',
  message_count: 42,
  expression_count: 7,
  jargon_count: 3,
})
const privateChat = makeChat(2, {
  display_name: '小明的私聊',
  chat_type: 'private',
  platform: 'telegram',
  target_id: '20002',
  user_id: '20002',
  user_nickname: '小明',
  group_id: null,
  group_name: null,
})

/** Bot 配置：表达组含一个在册目标与一个找不到聊天流的目标；记忆开启全局共享。 */
function makeBotConfig(): Record<string, unknown> {
  return {
    expression: {
      enabled: true,
      expression_groups: [
        {
          targets: [
            { platform: 'qq', item_id: '10001', rule_type: 'group' },
            { platform: 'qq', item_id: '99999', rule_type: 'private' },
          ],
        },
      ],
    },
    jargon: { jargon_groups: [] },
    a_memorix: { global_memory_sharing_enabled: true, shared_memory_groups: [] },
  }
}

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

/** 渲染共享组管理页，可通过 URL 指定共享组类型。 */
async function renderGroupsView(query = '') {
  window.history.replaceState(null, '', `/${query}`)
  render(<ChatManagementPage />, { wrapper: makeWrapper() })
  await screen.findByText('共享组管理')
}

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  vi.mocked(chatApi.getChatStreams).mockResolvedValue([groupChat, privateChat])
  vi.mocked(configApi.getBotConfig).mockResolvedValue(makeBotConfig())
  vi.mocked(configApi.updateBotConfigSection).mockResolvedValue({})
})

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})

describe('ChatManagementPage 共享组管理', () => {
  it('渲染共享组成员：在册聊天流显示名称，缺失目标显示未找到', async () => {
    await renderGroupsView()

    expect(await screen.findByText('共享组 1')).toBeInTheDocument()
    expect(screen.getByText('测试群 · 账号 123')).toBeInTheDocument()
    expect(screen.getByText('未找到聊天流')).toBeInTheDocument()
  })

  it('新建共享组：保留原组并追加空组写回表达配置', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '新建共享组' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('expression', {
        enabled: true,
        expression_groups: [
          {
            targets: [
              { platform: 'qq', item_id: '10001', rule_type: 'group' },
              { platform: 'qq', item_id: '99999', rule_type: 'private' },
            ],
          },
          { targets: [] },
        ],
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '共享组已保存',
        description: '表达共享组配置已更新。',
      })
    )
  })

  it('移除组内成员与删除整组分别写回收缩后的配置', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '移除 测试群 · 账号 123' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith(
        'expression',
        expect.objectContaining({
          expression_groups: [
            { targets: [{ platform: 'qq', item_id: '99999', rule_type: 'private' }] },
          ],
        })
      )
    )

    await user.click(screen.getByRole('button', { name: '删除共享组 1' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith(
        'expression',
        expect.objectContaining({ expression_groups: [] })
      )
    )
  })

  it('添加聊天弹窗：过滤已在组内的聊天流，支持搜索与勾选加入', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '添加聊天' }))
    const dialog = await screen.findByRole('dialog')
    // 已在组内的“测试群”不再出现在候选列表
    expect(within(dialog).queryByText('测试群 · 账号 123')).not.toBeInTheDocument()

    // 搜索无匹配时显示空态
    const searchInput = within(dialog).getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID')
    await user.type(searchInput, '不存在的关键词')
    expect(await within(dialog).findByText('没有可加入的聊天流')).toBeInTheDocument()
    await user.clear(searchInput)

    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 小明的私聊' }))
    await user.click(within(dialog).getByRole('button', { name: '加入 1 个聊天' }))

    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('expression', {
        enabled: true,
        expression_groups: [
          {
            targets: [
              { platform: 'qq', item_id: '10001', rule_type: 'group' },
              { platform: 'qq', item_id: '99999', rule_type: 'private' },
              { platform: 'telegram', item_id: '20002', rule_type: 'private' },
            ],
          },
        ],
      })
    )
    // 提交后弹窗关闭
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('记忆共享组在开启全局共享时禁止编辑', async () => {
    const user = userEvent.setup()
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '记忆' }))
    expect(
      await screen.findByText('全局共享记忆已开启，记忆共享组暂不参与普通记忆检索范围控制。')
    ).toBeInTheDocument()
    expect(screen.getByText('暂无记忆共享组。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建共享组' })).toBeDisabled()
    expect(configApi.updateBotConfigSection).not.toHaveBeenCalled()
  })

  it('URL kind=jargon 时初始进入黑话共享组视图', async () => {
    await renderGroupsView('?kind=jargon')

    expect(await screen.findByText('暂无黑话共享组。')).toBeInTheDocument()
  })

  it('共享组配置加载失败时显示错误提示', async () => {
    vi.mocked(configApi.getBotConfig).mockRejectedValue(new Error('config boom'))
    await renderGroupsView()

    expect(await screen.findByText('加载共享组失败')).toBeInTheDocument()
  })
})

describe('ChatManagementPage 共享组批量与边界', () => {
  it('添加弹窗限制 50 条，可反选后批量加入多个聊天', async () => {
    const user = userEvent.setup()
    const extraChats = Array.from({ length: 51 }, (_, index) =>
      makeChat(index + 3, { display_name: `批量聊天${index + 3}` })
    )
    vi.mocked(chatApi.getChatStreams).mockResolvedValue([groupChat, privateChat, ...extraChats])
    await renderGroupsView()
    await screen.findByText('共享组 1')

    await user.click(screen.getByRole('button', { name: '添加聊天' }))
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByText('仅显示前 50 个匹配项，请输入关键词缩小范围。')
    ).toBeInTheDocument()

    const searchInput = within(dialog).getByPlaceholderText('搜索名称、平台、用户、群号或会话 ID')
    await user.type(searchInput, '小明')
    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 小明的私聊' }))
    await user.click(within(dialog).getByRole('checkbox', { name: '选择 小明的私聊' }))
    expect(within(dialog).getByRole('button', { name: '加入 0 个聊天' })).toBeDisabled()

    await user.clear(searchInput)
    await user.type(searchInput, '批量聊天3')
    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 批量聊天3' }))
    await user.clear(searchInput)
    await user.type(searchInput, '小明')
    await user.click(await within(dialog).findByRole('checkbox', { name: '选择 小明的私聊' }))
    await user.click(within(dialog).getByRole('button', { name: '加入 2 个聊天' }))

    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('expression', {
        enabled: true,
        expression_groups: [
          {
            targets: [
              { platform: 'qq', item_id: '10001', rule_type: 'group' },
              { platform: 'qq', item_id: '99999', rule_type: 'private' },
              { platform: 'telegram', item_id: '20002', rule_type: 'private' },
              { platform: 'qq', item_id: '10003', rule_type: 'group' },
            ],
          },
        ],
      })
    )
  })

  it('取消添加、空组、黑话新建、记忆可编辑与保存失败', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.getBotConfig).mockResolvedValue({
      expression: {
        enabled: true,
        expression_groups: [
          { targets: [] },
          {
            targets: [
              null,
              { platform: '', item_id: 'x' },
              { platform: 'qq', item_id: '10001', type: 'group' },
            ],
          },
        ],
      },
      jargon: { jargon_groups: 'not-array' },
      a_memorix: {
        global_memory_sharing_enabled: false,
        shared_memory_groups: [
          {
            expression_groups: [{ platform: 'telegram', item_id: '20002', rule_type: 'private' }],
          },
        ],
      },
    })
    await renderGroupsView()
    expect(await screen.findByText('空共享组')).toBeInTheDocument()
    expect(screen.getByText('测试群 · 账号 123')).toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: '添加聊天' })[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: '黑话' }))
    expect(await screen.findByText('暂无黑话共享组。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '新建共享组' }))
    await waitFor(() =>
      expect(configApi.updateBotConfigSection).toHaveBeenCalledWith(
        'jargon',
        expect.objectContaining({ jargon_groups: [{ targets: [] }] })
      )
    )

    await user.click(screen.getByRole('button', { name: '记忆' }))
    expect(await screen.findByText('共享组 1')).toBeInTheDocument()
    expect(screen.getByText('小明的私聊')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建共享组' })).toBeEnabled()

    vi.mocked(configApi.updateBotConfigSection).mockRejectedValue(new Error('写配置失败'))
    await user.click(screen.getByRole('button', { name: '新建共享组' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存共享组失败',
        description: '写配置失败',
        variant: 'destructive',
      })
    )
  })

})
