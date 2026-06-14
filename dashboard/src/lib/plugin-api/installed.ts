/**
 * 已安装插件 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与响应体 success 标记的解包规则。
 */
import { ApiError, backendApi } from '@/lib/http'

import type { InstalledPlugin, LegacyInstalledPlugin } from './types'

const INSTALLED_PLUGINS_CACHE_TTL = 1500

let installedPluginsCache: { timestamp: number; result: InstalledPlugin[] } | null = null
let installedPluginsRequest: Promise<InstalledPlugin[]> | null = null

/**
 * 获取已安装插件列表
 *
 * 保持原有行为：HTTP 错误 / 响应解析失败 / 业务级失败都返回空列表而不是错误；
 * 网络层失败与认证失效（401）仍向上抛出。
 */
async function fetchInstalledPluginsUncached(): Promise<InstalledPlugin[]> {
  let data: { success: boolean; plugins?: InstalledPlugin[]; message?: string }
  try {
    data = await backendApi.get<{ success: boolean; plugins?: InstalledPlugin[]; message?: string }>(
      '/api/webui/plugins/installed',
      { errorMessage: '获取已安装插件列表失败' }
    )
  } catch (error) {
    if (error instanceof ApiError && error.status !== undefined && error.status !== 401) {
      return []
    }
    throw error
  }

  if (!data.success) {
    return []
  }

  return data.plugins || []
}

export async function getInstalledPlugins(options: { forceRefresh?: boolean } = {}): Promise<InstalledPlugin[]> {
  if (
    !options.forceRefresh
    && installedPluginsCache
    && Date.now() - installedPluginsCache.timestamp < INSTALLED_PLUGINS_CACHE_TTL
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

/**
 * 检查插件是否已安装
 */
export function checkPluginInstalled(pluginId: string, installedPlugins: InstalledPlugin[]): boolean {
  return installedPlugins.some(p => p.id === pluginId)
}

/**
 * 获取已安装插件的版本
 */
export function getInstalledPluginVersion(pluginId: string, installedPlugins: (InstalledPlugin | LegacyInstalledPlugin)[]): string | undefined {
  const plugin = installedPlugins.find(p => p.id === pluginId)
  if (!plugin) return undefined

  // 兼容两种格式：新格式有 manifest，旧格式直接有 version
  if ('manifest' in plugin && plugin.manifest) {
    return plugin.manifest.version
  }
  // 旧版本格式
  if ('version' in plugin) {
    return plugin.version
  }
  return undefined
}
