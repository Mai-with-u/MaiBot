import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ChatManagementPage } from '../chat-management'
import * as api from '@/lib/sharing-groups-api'
import * as chats from '@/lib/chat-management-api'

vi.mock('@/lib/sharing-groups-api', () => ({
  getSharingGroups: vi.fn(),
  saveSharingGroup: vi.fn(),
  deleteSharingGroup: vi.fn(),
}))
vi.mock('@/lib/chat-management-api', () => ({ getChatStreams: vi.fn() }))
const group: api.SharingGroup = {
  id: 'uuid-a',
  name: '日常伙伴',
  color: '#39845A',
  note: '',
  enabled: true,
  revision: 1,
  member_ids: [],
  rules: [],
  resolved_ids: [],
  capabilities: { expression: true, memory: true },
  created_at: 1,
  updated_at: 1,
}
beforeEach(() => {
  window.history.replaceState(null, '', '/')
  vi.mocked(api.getSharingGroups).mockResolvedValue({
    data: [group],
    capabilities: { expression: '表达', jargon: '黑话', memory: '记忆' },
  })
  vi.mocked(chats.getChatStreams).mockResolvedValue([])
  vi.mocked(api.saveSharingGroup).mockResolvedValue(group)
})
afterEach(cleanup)
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ChatManagementPage />
    </QueryClientProvider>
  )
}
it('一个组只显示一个节点，能力作为开关和图层', async () => {
  show()
  const graph = await screen.findByRole('region', { name: '相通关系图' })
  expect(within(graph).getAllByRole('button', { name: /日常伙伴/ })).toHaveLength(1)
  expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
})
it('稳定 ID 直达编辑，名称颜色备注及能力一起保存', async () => {
  const user = userEvent.setup()
  window.history.replaceState(null, '', '/?group=uuid-a')
  show()
  const dialog = await screen.findByRole('dialog')
  const name = within(dialog).getByRole('textbox', { name: '名称' })
  await user.clear(name)
  await user.type(name, '新名字')
  await user.type(within(dialog).getByRole('textbox', { name: '备注' }), '备注内容')
  fireEvent.change(within(dialog).getByLabelText('颜色'), { target: { value: '#397fae' } })
  await user.click(within(dialog).getByRole('switch', { name: '共享黑话' }))
  await user.click(within(dialog).getByRole('button', { name: '保存' }))
  await waitFor(() =>
    expect(api.saveSharingGroup).toHaveBeenCalledWith(
      expect.objectContaining({
        name: '新名字',
        note: '备注内容',
        color: '#397fae',
        revision: 1,
        capabilities: { expression: true, memory: true, jargon: true },
      }),
      'uuid-a'
    )
  )
})
it('创建独立组时无需切换类别', async () => {
  const user = userEvent.setup()
  show()
  await user.click(screen.getByRole('button', { name: '新建相通组' }))
  const dialog = await screen.findByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: '名称' }), '新组')
  await user.click(within(dialog).getByRole('button', { name: '保存' }))
  await waitFor(() =>
    expect(api.saveSharingGroup).toHaveBeenCalledWith(
      expect.objectContaining({ name: '新组', note: '', member_ids: [] }),
      undefined
    )
  )
})
it('画布滚轮缩放后可重置', async () => {
  const user = userEvent.setup()
  show()
  const graph = await screen.findByRole('region', { name: '相通关系图' })
  fireEvent.wheel(graph, { deltaY: -10000, clientX: 200, clientY: 100 })
  expect(within(graph).getByText('250%')).toBeInTheDocument()
  await user.click(within(graph).getByRole('button', { name: '重置' }))
  expect(within(graph).getByText('100%')).toBeInTheDocument()
})
