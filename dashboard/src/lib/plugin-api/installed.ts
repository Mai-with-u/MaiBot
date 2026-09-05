/**
 * 已安装插件 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint、业务错误文案与响应体 success 标记的解包规则。
 * 公开函数遵循 throw 契约：成功返回数据，失败抛 ApiError。
 */
import { backendApi, requireSuccess } from '@/lib/http'

import type { InstalledPlugin, LegacyInstalledPlugin } from './types'

const INSTALLED_PLUGINS_CACHE_TTL = 1500

let installedPluginsCache: { timestamp: number; result: InstalledPlugin[] } | null = null
let installedPluginsRequest: Promise<InstalledPlugin[]> | null = null

/**
 * 获取已安装插件列表。
 *
 * HTTP 失败与业务级 success === false 均抛出 ApiError；
 * 空列表仅表示后端确认当前没有已安装插件。
 * 认证失效（401）由 backendApi 处理并向上抛出。
 */
async function fetchInstalledPluginsUncached(): Promise<InstalledPlugin[]> {
  const data = await backendApi.get<{
    success: boolean
    plugins?: InstalledPlugin[]
    message?: string
  }>('/api/webui/plugins/installed', { errorMessage: '获取已安装插件列表失败' })

  return requireSuccess(data, '获取已安装插件列表失败').plugins || []
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

/**
 * 获取本地已安装插件 README。
 */
export async function getLocalPluginReadme(pluginId: string): Promise<string> {
  const data = await backendApi.get<{ success: boolean; data?: string; error?: string }>(
    `/api/webui/plugins/local-readme/${encodeURIComponent(pluginId)}`,
    { errorMessage: '获取 README 失败' }
  )

  if (!data.success) {
    return ''
  }

  return data.data || ''
}

/**
 * 获取本地已安装插件更新日志。
 */
export async function getLocalPluginChangelog(pluginId: string): Promise<string> {
  const data = await backendApi.get<{ success: boolean; data?: string; error?: string }>(
    `/api/webui/plugins/local-changelog/${encodeURIComponent(pluginId)}`,
    { errorMessage: '获取更新日志失败' }
  )

  if (!data.success) {
    return ''
  }

  return data.data || ''
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
  installedPlugins: (InstalledPlugin | LegacyInstalledPlugin)[]
): string | undefined {
  const plugin = installedPlugins.find((p) => p.id === pluginId)
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
