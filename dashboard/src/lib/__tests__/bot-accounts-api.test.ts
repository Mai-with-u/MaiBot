import { beforeEach, describe, expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'

import {
  getDiscoveredBotAccounts,
  setDiscoveredBotAccountDisabled,
  type BotPlatformAccount,
} from '../bot-accounts-api'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)

function makeAccount(overrides: Partial<BotPlatformAccount> = {}): BotPlatformAccount {
  return {
    id: 7,
    platform: 'qq',
    account_id: '10001',
    disabled: false,
    first_seen_at: '2026-01-01T00:00:00Z',
    last_seen_at: '2026-01-02T00:00:00Z',
    disabled_at: null,
    last_source: 'ready',
    last_adapter_id: 'adapter-1',
    last_plugin_id: null,
    last_gateway_name: null,
    online: true,
    ...overrides,
  }
}

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
})

describe('getDiscoveredBotAccounts', () => {
  it('以 no-store 读取适配器账号列表并返回 data', async () => {
    const accounts = [makeAccount(), makeAccount({ id: 8, account_id: '10002' })]
    getMock.mockResolvedValue({ success: true, data: accounts })

    await expect(getDiscoveredBotAccounts()).resolves.toBe(accounts)
    expect(getMock).toHaveBeenCalledWith('/api/webui/bot-accounts', {
      cache: 'no-store',
      errorMessage: '读取适配器账号失败',
    })
  })
})

describe('setDiscoveredBotAccountDisabled', () => {
  it('disabled=true 时 POST disable 并返回更新后的账号', async () => {
    const disabledAccount = makeAccount({
      disabled: true,
      disabled_at: '2026-01-03T00:00:00Z',
    })
    postMock.mockResolvedValue({ success: true, data: disabledAccount })

    await expect(setDiscoveredBotAccountDisabled(7, true)).resolves.toBe(disabledAccount)
    expect(postMock).toHaveBeenCalledWith('/api/webui/bot-accounts/7/disable', {
      errorMessage: '禁用适配器账号失败',
    })
  })

  it('disabled=false 时 POST restore 并返回更新后的账号', async () => {
    const restoredAccount = makeAccount({ id: 9, disabled: false, disabled_at: null })
    postMock.mockResolvedValue({ success: true, data: restoredAccount })

    await expect(setDiscoveredBotAccountDisabled(9, false)).resolves.toBe(restoredAccount)
    expect(postMock).toHaveBeenCalledWith('/api/webui/bot-accounts/9/restore', {
      errorMessage: '恢复适配器账号失败',
    })
  })
})
