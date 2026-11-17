import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { QQMappingTable } from '../qq-mapping-table'
import { invokeQQWebUI } from '@/lib/qq-webui'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))
vi.mock('@/host/state', () => ({ useHostRuntime: () => ({ selected_bot_id: 'bot' }) }))
vi.mock('@/lib/qq-webui', () => ({ invokeQQWebUI: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
  Element.prototype.scrollIntoView = () => {}
  vi.mocked(invokeQQWebUI).mockImplementation(async (_kind, name) =>
    name === 'mappings'
      ? {
          group: Array.from({ length: 40 }, (_, i) => ({
            openid: `group-${i}`,
            id: String(100 + i),
            source: 'manual',
            nickname: '',
            cardname: '',
            group_id: '',
            status: '已绑定',
            conflict: false,
          })),
        }
      : {}
  )
})
afterEach(cleanup)
function open() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <QQMappingTable kind="group" />
    </QueryClientProvider>
  )
}
it('分页浏览，删除全选绑定前必须确认且提交全部选中 ID', async () => {
  const user = userEvent.setup()
  open()
  await screen.findByText('group-0')
  expect(screen.queryByText('group-39')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '下一页' }))
  await screen.findByText('group-39')
  await user.click(screen.getByRole('button', { name: '全选 40 条' }))
  await user.click(screen.getByRole('button', { name: '删除所选 (40)' }))
  expect(invokeQQWebUI).not.toHaveBeenCalledWith(
    'actions',
    expect.anything(),
    expect.anything(),
    true
  )
  await user.click(screen.getByRole('button', { name: '确认' }))
  await waitFor(() =>
    expect(invokeQQWebUI).toHaveBeenCalledWith(
      'actions',
      'delete_bindings',
      {
        kind: 'group',
        openids_json: JSON.stringify(Array.from({ length: 40 }, (_, i) => `group-${i}`)),
      },
      true
    )
  )
})
it('取消删除不发送修改请求', async () => {
  const user = userEvent.setup()
  open()
  await user.click(await screen.findByRole('button', { name: '删除 group-0' }))
  await user.click(screen.getByRole('button', { name: '取消' }))
  expect(invokeQQWebUI).not.toHaveBeenCalledWith(
    'actions',
    expect.anything(),
    expect.anything(),
    true
  )
})

it('暂缓发布用户迁移，不展示入口也不查询迁移进度', async () => {
  vi.mocked(invokeQQWebUI).mockImplementation(async (_kind, name) => {
    if (name === 'mappings') return { user: [{ openid: 'OPENID', id: '123456789', source: 'manual', nickname: '用户', cardname: '', group_id: '', status: '已绑定', conflict: false }] }
    if (name === 'mapping_migrations') return { candidates: [{ source: '123456789', target: 'OPENID', status: 'pending' }] }
    return {}
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><QQMappingTable kind="user" /></QueryClientProvider>)
  await screen.findByText('OPENID')
  expect(screen.queryByRole('button', { name: '迁回此 OpenID' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '完成迁移并关闭映射' })).not.toBeInTheDocument()
  expect(invokeQQWebUI).not.toHaveBeenCalledWith('queries', 'mapping_migrations')
  expect(navigate).not.toHaveBeenCalled()
})
