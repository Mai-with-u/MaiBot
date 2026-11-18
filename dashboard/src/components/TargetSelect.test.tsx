import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { backendApi } from '@/lib/http'
import { TargetSelect, type ConfigTarget } from './TargetSelect'

vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn() }, requireSuccess: (value: unknown) => value }))
afterEach(cleanup)

it('搜索目标并保存身份三元组，移除后清空选择', async () => {
  Element.prototype.scrollIntoView = () => {}
  const target: ConfigTarget = { platform: 'qq', type: 'group', id: 'openid', name: '测试群' }
  vi.mocked(backendApi.get).mockResolvedValue({ success: true, targets: [target] })
  const changed = vi.fn()
  function Demo() {
    const [value, setValue] = useState<ConfigTarget[]>([])
    return <TargetSelect value={value} onChange={items => { setValue(items); changed(items) }} type="group" platform="qq" label="允许群" />
  }
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Demo /></QueryClientProvider>)
  await userEvent.click(screen.getByRole('combobox', { name: '允许群' }))
  await userEvent.click(await screen.findByRole('option', { name: /测试群/ }))
  expect(changed).toHaveBeenLastCalledWith([target])
  await userEvent.type(screen.getByPlaceholderText('搜索名称或 ID'), '测试')
  await waitFor(() => expect(backendApi.get).toHaveBeenLastCalledWith('/api/chat/targets', expect.objectContaining({ query: { target_type: 'group', platform: 'qq', search: '测试', limit: 50 } })))
  await userEvent.keyboard('{Escape}')
  await userEvent.click(screen.getByRole('button', { name: '移除 测试群' }))
  expect(changed).toHaveBeenLastCalledWith([])
})
