import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { QQAdapterPage } from '../qq-adapter-page'
import { getPersonList } from '@/lib/person-api'
import { invokePluginWebUI } from '@/lib/plugin-webui'
import type { PersonInfo } from '@/types/person'

vi.mock('@/host/state', () => ({
  getHostRuntime: () => ({ selected_bot_id: 'bot' }),
  useHostRuntime: () => ({ selected_bot_id: 'bot', selected_bot_running: true }),
}))
vi.mock('@/lib/person-api', () => ({ getPersonList: vi.fn() }))
vi.mock('@/lib/plugin-webui', () => ({ invokePluginWebUI: vi.fn() }))
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
  Element.prototype.scrollIntoView = () => {}
  vi.mocked(getPersonList).mockResolvedValue({
    data: [{ person_id: 'person', user_id: 'openid-123', nickname: '测试用户' } as PersonInfo],
    page: 1,
    page_size: 30,
    total: 1,
  })
  vi.mocked(invokePluginWebUI).mockImplementation(async (_plugin, _page, _kind, name) =>
    name === 'identity_mode'
      ? { mapping_enabled: false }
      : {
          connected: true,
          configured_app_id: 'app',
          bot_name: '机器人',
          state: 'idle',
          message: '',
          image: '',
        }
  )
})
afterEach(cleanup)
function open() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <QQAdapterPage />
    </QueryClientProvider>
  )
}
it('非映射模式仍可检索用户并复制 ID，不展示映射或灌注标签', async () => {
  const user = userEvent.setup()
  open()
  await screen.findByText('测试用户')
  expect(screen.queryByRole('tab', { name: '群映射' })).not.toBeInTheDocument()
  expect(screen.queryByRole('tab', { name: '用户数据灌注' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '复制 openid-123' }))
  expect(await navigator.clipboard.readText()).toBe('openid-123')
  await user.type(screen.getByRole('textbox', { name: '搜索用户' }), '测试')
  await waitFor(() =>
    expect(getPersonList).toHaveBeenLastCalledWith({
      platform: 'qq',
      search: '测试',
      page: 1,
      page_size: 30,
      exclude_numeric_user_ids: true,
    })
  )
})
it('已登录时只展示状态，扫码操作在登录设置中', async () => {
  const user = userEvent.setup()
  open()
  await user.click(screen.getByRole('tab', { name: '登录状态' }))
  await screen.findByText('已连接')
  expect(screen.queryByRole('button', { name: '生成二维码' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '登录设置' }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '生成二维码' })).toBeInTheDocument()
  expect(invokePluginWebUI).not.toHaveBeenCalledWith(
    expect.anything(),
    expect.anything(),
    'actions',
    expect.anything(),
    expect.anything(),
    expect.anything()
  )
})
