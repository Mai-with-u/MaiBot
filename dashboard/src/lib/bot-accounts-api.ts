import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/bot-accounts'

export interface BotPlatformAccount {
  id: number
  platform: string
  account_id: string
  disabled: boolean
  first_seen_at: string
  last_seen_at: string
  disabled_at: string | null
  last_source: 'ready' | 'inbound' | string
  last_adapter_id: string | null
  last_plugin_id: string | null
  last_gateway_name: string | null
  online: boolean
}

interface BotPlatformAccountListResponse {
  success: boolean
  data: BotPlatformAccount[]
}

interface BotPlatformAccountMutationResponse {
  success: boolean
  data: BotPlatformAccount
}

export async function getDiscoveredBotAccounts(): Promise<BotPlatformAccount[]> {
  const response = await backendApi.get<BotPlatformAccountListResponse>(API_BASE, {
    cache: 'no-store',
    errorMessage: '读取适配器账号失败',
  })
  return response.data
}

export async function setDiscoveredBotAccountDisabled(
  accountId: number,
  disabled: boolean,
): Promise<BotPlatformAccount> {
  const action = disabled ? 'disable' : 'restore'
  const response = await backendApi.post<BotPlatformAccountMutationResponse>(
    `${API_BASE}/${accountId}/${action}`,
    { errorMessage: disabled ? '禁用适配器账号失败' : '恢复适配器账号失败' },
  )
  return response.data
}
