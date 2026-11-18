import { backendApi } from '@/lib/http'

export interface DeveloperReply {
  body: string
  login: string
  updated: number
}

export interface PublicPluginReview {
  id: string
  username: string | null
  rating: number | null
  comment: string
  created_at: string
  updated_at: string
  reply: DeveloperReply | null
}

export interface PluginFeedbackPage {
  plugin_id: string
  snapshot_at: number
  items: PublicPluginReview[]
  total: number
  next_offset: number | null
}

export function getPluginFeedback(pluginId: string, offset: number, signal?: AbortSignal) {
  return backendApi.get<PluginFeedbackPage>(
    `/api/webui/plugins/marketplace/${encodeURIComponent(pluginId)}/feedback`,
    { query: { offset }, signal, cache: 'no-store', errorMessage: '获取评论与开发者回复失败' },
  )
}
