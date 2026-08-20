import { backendApi, requireSuccess } from '@/lib/http'

import type { PluginPagesResponse } from './types'

/** 获取 WebUI Host 当前允许加载的插件页面清单。 */
export async function fetchPluginPages(signal?: AbortSignal): Promise<PluginPagesResponse> {
  const response = await backendApi.get<PluginPagesResponse>('/api/webui/plugins/pages', {
    signal,
  })
  return requireSuccess(response, '加载插件页面失败')
}
