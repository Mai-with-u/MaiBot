import { expect, it, vi } from 'vitest'
import { invokePluginWebUI } from '@/lib/plugin-webui'
import { invokeQQWebUI } from '@/lib/qq-webui'

vi.mock('@/host/state', () => ({ getHostRuntime: () => ({ selected_bot_id: 'bot' }) }))
vi.mock('@/lib/plugin-webui', () => ({ invokePluginWebUI: vi.fn() }))
it('模式、映射与操作共享队列，失败后后续请求仍能完成', async () => {
  let active = 0
  let maximum = 0
  vi.mocked(invokePluginWebUI).mockImplementation(async (_plugin, _page, _kind, name) => {
    active++
    maximum = Math.max(maximum, active)
    await new Promise((resolve) => setTimeout(resolve, 5))
    active--
    if (name === 'failure') throw new Error('failed')
    return name
  })
  const results = await Promise.allSettled([
    invokeQQWebUI('queries', 'identity_mode'),
    invokeQQWebUI('queries', 'failure'),
    invokeQQWebUI('queries', 'mappings'),
    invokeQQWebUI('actions', 'delete_bindings'),
  ])
  expect(maximum).toBe(1)
  expect(results.map((result) => result.status)).toEqual([
    'fulfilled',
    'rejected',
    'fulfilled',
    'fulfilled',
  ])
})
