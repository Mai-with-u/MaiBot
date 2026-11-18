/**
 * 已安装插件 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与响应体 success 标记的解包规则。
 */
import { ApiError, backendApi } from '@/lib/http'

import type { InstalledPlugin } from './types'

const INSTALLED_PLUGINS_CACHE_TTL = 1500

let installedPluginsCache: { timestamp: number; result: InstalledPlugin[] } | null = null
let installedPluginsRequest: Promise<InstalledPlugin[]> | null = null

/**
 * 获取已安装插件列表
 *
 * 请求失败向上抛出，只有成功读取的列表才会进入缓存。
 */
async function fetchInstalledPluginsUncached(): Promise<InstalledPlugin[]> {
  const data = await backendApi.get<{
    success: boolean
    plugins?: InstalledPlugin[]
    message?: string
  }>('/api/webui/plugins/installed', { errorMessage: '获取已安装插件列表失败' })

  if (!data.success) {
    throw new ApiError(data.message || '获取已安装插件列表失败', { detail: data })
  }
  if (!Array.isArray(data.plugins)) {
    throw new ApiError('已安装插件列表响应缺少有效的 plugins 数组', { detail: data })
  }

  return data.plugins
}

export async function getInstalledPlugins(
  options: { forceRefresh?: boolean } = {}
): Promise<InstalledPlugin[]> {
  if (
    !options.forceRefresh &&
    installedPluginsCache &&
    Date.now() - installedPluginsCache.timestamp < INSTALLED_PLUGINS_CACHE_TTL
  ) {
    return installedPluginsCache.result
  }

  if (!installedPluginsRequest || options.forceRefresh) {
    installedPluginsRequest = fetchInstalledPluginsUncached()
      .then((result) => {
        installedPluginsCache = { timestamp: Date.now(), result }
        return result
      })
      .finally(() => {
        installedPluginsRequest = null
      })
  }

  return installedPluginsRequest
}

/** 读取后端累计运行时长；此接口不重新扫描插件目录。 */
export async function getPluginUsageSeconds(): Promise<Record<string, number>> {
  const data = await backendApi.get<{ usage_seconds: Record<string, number> }>(
    '/api/webui/plugins/usage', { errorMessage: '获取插件运行时长失败' }
  )
  return data.usage_seconds
}

/**
 * 检查插件是否已安装
 */
export function checkPluginInstalled(
  pluginId: string,
  installedPlugins: InstalledPlugin[]
): boolean {
  return installedPlugins.some((p) => p.id === pluginId)
}

/**
 * 获取已安装插件的版本
 */
export function getInstalledPluginVersion(
  pluginId: string,
  installedPlugins: InstalledPlugin[]
): string | undefined {
  return installedPlugins.find((plugin) => plugin.id === pluginId)?.manifest.version
}
