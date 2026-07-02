import { backendApi } from '@/lib/http'

export type PluginHomeCardWidth = 'small' | 'medium' | 'large' | 'wide' | 'full'

export type PluginHomeCardContent =
  | string
  | Record<string, unknown>
  | Array<Record<string, unknown>>

export interface PluginHomeCard {
  id: string
  name: string
  plugin_id: string
  title: string
  description: string
  content: PluginHomeCardContent
  link_url: string
  link_label: string
  icon: string
  width: PluginHomeCardWidth
  order: number
  enabled: boolean
}

export async function getPluginHomeCards(): Promise<PluginHomeCard[]> {
  const response = await backendApi.get<{ success: boolean; cards: PluginHomeCard[] }>(
    '/api/webui/plugins/runtime/home-cards',
    { errorMessage: '加载插件首页卡片失败' }
  )
  return response.cards ?? []
}
