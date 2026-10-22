import type { PluginInfo, PluginReleaseCatalog, PluginType } from '@/types/plugin'

import { ApiError, backendApi } from '@/lib/http'
import { pluginProgressClient } from '@/lib/plugin-progress-client'

import type { GitStatus, MaimaiVersion } from './types'

/**
 * 插件市场展示缓存
 */
const PLUGIN_LIST_CACHE_TTL = 5 * 60 * 1000
const PLUGIN_LIST_STORAGE_KEY = 'maibot-plugin-market-list-cache-v4'
const PLUGIN_TYPES = new Set<PluginType>([
  'adapter',
  'chat',
  'creative',
  'provider',
  'management',
  'search',
  'knowledge',
  'media',
  'game',
  'security',
  'automation',
  'extension',
  'other',
])

const pluginListCaches = new Map<boolean, { timestamp: number; result: PluginInfo[] }>()
const pluginListRequests = new Map<boolean, Promise<PluginInfo[]>>()
let cacheGeneration = 0

export function invalidatePluginMarketCache(): void {
  cacheGeneration += 1
  pluginListCaches.clear()
  pluginListRequests.clear()
  localStorage.removeItem(PLUGIN_LIST_STORAGE_KEY)
  localStorage.removeItem(`${PLUGIN_LIST_STORAGE_KEY}-compatible`)
}

interface PluginListStorageCache {
  timestamp: number
  data: PluginInfo[]
}

/**
 * 插件列表 API 响应类型（只包含我们需要的字段）
 */
interface PluginApiResponse {
  id: string
  marketplace_id: string
  releases: PluginReleaseCatalog
  stats: import('@/lib/plugin-stats').PluginStatsData | null
  published_at?: string | number | null
  updated_at?: string | number | null
  changelog?: string | null
  assets?: PluginInfo['assets']
  manifest: {
    manifest_version: number
    id?: string
    name: string
    version: string
    description: string
    author: {
      name: string
      url?: string
    }
    license: string
    host_application: {
      min_version: string
      max_version?: string
    }
    sdk?: PluginInfo['manifest']['sdk']
    homepage_url?: string
    repository_url?: string
    urls?: {
      repository?: string
      homepage?: string
      documentation?: string
      issues?: string
    }
    keywords: string[]
    plugin_type?: string
    display?: PluginInfo['manifest']['display']
    changelog?: string
    default_locale?: string
    i18n?: { default_locale: string; locales_path?: string }
    locales_path?: string
  }
  // 可能还有其他字段,但我们不关心
  [key: string]: unknown
}

function normalizeOptionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function normalizePluginType(value: unknown): PluginType {
  if (typeof value !== 'string' || !value.trim()) {
    return 'extension'
  }

  const normalizedValue = value.trim()
  if (PLUGIN_TYPES.has(normalizedValue as PluginType)) {
    return normalizedValue as PluginType
  }

  return 'other'
}

function normalizePluginManifest(manifest: PluginApiResponse['manifest']): PluginInfo['manifest'] {
  const repositoryUrl = manifest.repository_url || manifest.urls?.repository
  const homepageUrl = manifest.homepage_url || manifest.urls?.homepage

  return {
    manifest_version: manifest.manifest_version || 1,
    id: manifest.id,
    name: manifest.name,
    version: manifest.version,
    description: manifest.description || '',
    author: manifest.author || { name: 'Unknown' },
    license: manifest.license || 'Unknown',
    host_application: manifest.host_application || { min_version: '0.0.0' },
    sdk: manifest.sdk,
    homepage_url: homepageUrl,
    repository_url: repositoryUrl,
    urls: manifest.urls,
    keywords: manifest.keywords || [],
    plugin_type: normalizePluginType(manifest.plugin_type),
    display: manifest.display,
    changelog: normalizeOptionalString(manifest.changelog),
    default_locale: manifest.i18n?.default_locale || manifest.default_locale || 'zh-CN',
    locales_path: manifest.i18n?.locales_path || manifest.locales_path,
  }
}

function normalizeDateString(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'number' && Number.isFinite(value)) {
    return new Date(value).toISOString()
  }

  return ''
}

function readPluginListStorageCache(compatibleOnly: boolean): PluginListStorageCache | null {
  if (typeof localStorage === 'undefined') {
    return null
  }

  try {
    const rawCache = localStorage.getItem(compatibleOnly ? `${PLUGIN_LIST_STORAGE_KEY}-compatible` : PLUGIN_LIST_STORAGE_KEY)
    if (!rawCache) {
      return null
    }

    const cache = JSON.parse(rawCache) as Partial<PluginListStorageCache>
    if (!cache.timestamp || !Array.isArray(cache.data)) {
      return null
    }

    return {
      timestamp: Number(cache.timestamp),
      data: cache.data,
    }
  } catch (error) {
    console.warn('读取插件市场缓存失败:', error)
    return null
  }
}

function writePluginListStorageCache(data: PluginInfo[], compatibleOnly: boolean): void {
  if (typeof localStorage === 'undefined') {
    return
  }

  try {
    localStorage.setItem(
      compatibleOnly ? `${PLUGIN_LIST_STORAGE_KEY}-compatible` : PLUGIN_LIST_STORAGE_KEY,
      JSON.stringify({
        timestamp: Date.now(),
        data,
      })
    )
  } catch (error) {
    console.warn('写入插件市场缓存失败:', error)
  }
}

export function getCachedPluginList(compatibleOnly = false): PluginInfo[] | null {
  const pluginListCache = pluginListCaches.get(compatibleOnly)
  if (pluginListCache) {
    return pluginListCache.result
  }

  const storedCache = readPluginListStorageCache(compatibleOnly)
  if (!storedCache) {
    return null
  }

  pluginListCaches.set(compatibleOnly, { timestamp: storedCache.timestamp, result: storedCache.data })
  return storedCache.data
}

/**
 * 从远程获取插件列表(通过后端代理避免 CORS)
 */
async function fetchPluginListUncached(compatibleOnly: boolean, forceRefresh: boolean): Promise<PluginInfo[]> {
  const market = await backendApi.get<{
    source: 'official' | 'cnb' | 'github'
    plugins: PluginApiResponse[]
  }>('/api/webui/plugins/marketplace', {
    query: { compatible_only: compatibleOnly, force_refresh: forceRefresh }, errorMessage: '获取插件市场数据失败',
  })
  // 选源、清单与发布版本合并均由公共市场服务完成；这里只转换展示模型。
  return market.plugins.map((item, index) => ({
    market_data_source: market.source,
    marketplace_stats: item.stats ?? undefined,
    id: item.id,
    marketplace_id: item.marketplace_id,
    marketplace_order: index,
    stats_ids: [item.id],
    manifest: normalizePluginManifest(item.manifest),
    releases: item.releases,
    assets: item.assets ?? undefined,
    downloads: item.stats?.downloads ?? 0,
    rating: item.stats?.rating ?? 0,
    review_count: item.stats?.comment_count ?? 0,
    installed: false,
    source: 'market' as const,
    changelog: normalizeOptionalString(item.changelog),
    published_at: normalizeDateString(item.published_at),
    updated_at: normalizeDateString(item.updated_at),
  }))
}

export async function fetchPluginList(
  options: { forceRefresh?: boolean; compatibleOnly?: boolean } = {}
): Promise<PluginInfo[]> {
  const compatibleOnly = options.compatibleOnly ?? false
  const pluginListCache = pluginListCaches.get(compatibleOnly)
  if (
    !options.forceRefresh &&
    pluginListCache &&
    Date.now() - pluginListCache.timestamp < PLUGIN_LIST_CACHE_TTL
  ) {
    return pluginListCache.result
  }

  if (!options.forceRefresh && !pluginListCache) {
    const storedCache = readPluginListStorageCache(compatibleOnly)
    if (storedCache && Date.now() - storedCache.timestamp < PLUGIN_LIST_CACHE_TTL) {
      pluginListCaches.set(compatibleOnly, { timestamp: storedCache.timestamp, result: storedCache.data })
      return storedCache.data
    }
  }

  let pluginListRequest = pluginListRequests.get(compatibleOnly)
  if (!pluginListRequest || options.forceRefresh) {
    const generation = cacheGeneration
    // 仅在成功（fetchPluginListUncached 未抛错）时写入内存/本地缓存
    const request: Promise<PluginInfo[]> = fetchPluginListUncached(compatibleOnly, Boolean(options.forceRefresh))
      .then((result) => {
        if (generation === cacheGeneration && pluginListRequests.get(compatibleOnly) === request) {
          pluginListCaches.set(compatibleOnly, { timestamp: Date.now(), result })
          writePluginListStorageCache(result, compatibleOnly)
        }
        return result
      })
      .finally(() => {
        if (pluginListRequests.get(compatibleOnly) === request) pluginListRequests.delete(compatibleOnly)
      })
    pluginListRequest = request
    pluginListRequests.set(compatibleOnly, request)
  }

  return pluginListRequest
}

/** 插件中心仅在打开详情时拉取完整历史版本。 */
export async function fetchPluginDetail(plugin: PluginInfo): Promise<PluginInfo> {
  if (plugin.source === 'local') return plugin
  const detail = await backendApi.get<{
    plugin: PluginApiResponse
    releases: PluginReleaseCatalog
  }>(`/api/webui/plugins/marketplace/${encodeURIComponent(plugin.id)}`, {
    errorMessage: '获取插件详情失败',
  })
  const recommended = detail.releases.versions.find(
    (release) => release.version === detail.releases.recommended_version
  )
  return {
    ...plugin,
    manifest: normalizePluginManifest(recommended?.manifest ?? detail.plugin.manifest),
    releases: detail.releases,
    marketplace_stats: detail.plugin.stats ?? undefined,
  }
}

/** 读取插件文档；本地路径、仓库、分支及信息源均由公共市场服务解析。 */
export async function fetchPluginDocument(
  pluginId: string, kind: 'readme' | 'changelog',
  options: { preferLocal?: boolean; branch?: string; signal?: AbortSignal } = {}
): Promise<string> {
  const result = await backendApi.get<{ content: string }>(
    `/api/webui/plugins/marketplace/${encodeURIComponent(pluginId)}/${kind}`,
    { query: { prefer_local: options.preferLocal ?? true, branch: options.branch ?? 'main' },
      signal: options.signal, errorMessage: '获取插件文档失败' }
  )
  return result.content
}

/**
 * 检查本机 Git 安装状态
 */
export async function checkGitStatus(): Promise<GitStatus> {
  try {
    return await backendApi.get<GitStatus>('/api/webui/plugins/git-status', {
      errorMessage: '无法检测 Git 安装状态',
    })
  } catch (error) {
    // 保持原有行为：HTTP 错误 / 响应解析失败时按“无法检测”处理；网络层失败与认证失效（401）仍向上抛出
    if (error instanceof ApiError && error.status !== undefined && error.status !== 401) {
      return {
        installed: false,
        error: '无法检测 Git 安装状态',
      }
    }
    throw error
  }
}

/**
 * 获取麦麦版本信息
 */
export async function getMaimaiVersion(): Promise<MaimaiVersion> {
  try {
    return await backendApi.get<MaimaiVersion>('/api/webui/plugins/version', {
      errorMessage: '获取麦麦版本信息失败',
    })
  } catch (error) {
    // 保持原有行为：HTTP 错误 / 响应解析失败时回退为 0.0.0；网络层失败与认证失效（401）仍向上抛出
    if (error instanceof ApiError && error.status !== undefined && error.status !== 401) {
      return {
        version: '0.0.0',
        version_major: 0,
        version_minor: 0,
        version_patch: 0,
      }
    }
    throw error
  }
}

type VersionTuple = [number, number, number]

function parseVersionTuple(version: string | undefined): VersionTuple {
  if (!version) {
    return [0, 0, 0]
  }

  const normalizedVersion = version.trim().replace(/-snapshot\.\d+$/, '')
  const parts = normalizedVersion.split('.').map((part) => Number.parseInt(part, 10))
  return [
    Number.isFinite(parts[0]) ? parts[0] : 0,
    Number.isFinite(parts[1]) ? parts[1] : 0,
    Number.isFinite(parts[2]) ? parts[2] : 0,
  ]
}

function compareVersionTuple(left: VersionTuple, right: VersionTuple): number {
  for (let index = 0; index < 3; index += 1) {
    if (left[index] < right[index]) return -1
    if (left[index] > right[index]) return 1
  }

  return 0
}

/**
 * 比较版本号
 *
 * @param pluginMinVersion 插件要求的最小版本
 * @param pluginMaxVersion 插件要求的最大版本(可选)
 * @param maimaiVersion 麦麦当前版本
 * @returns true 表示兼容,false 表示不兼容
 */
export function isPluginCompatible(
  pluginMinVersion: string,
  pluginMaxVersion: string | undefined,
  maimaiVersion: MaimaiVersion
): boolean {
  const currentVersion: VersionTuple = [
    maimaiVersion.version_major,
    maimaiVersion.version_minor,
    maimaiVersion.version_patch,
  ]
  const minVersion = parseVersionTuple(pluginMinVersion)

  if (compareVersionTuple(currentVersion, minVersion) < 0) {
    return false
  }

  // 检查最大版本(如果有)
  if (pluginMaxVersion) {
    const maxVersion = parseVersionTuple(pluginMaxVersion)
    const isHigherThanMax = compareVersionTuple(currentVersion, maxVersion) > 0

    // 与运行时 manifest 校验保持一致：仅修订号高于声明上限时，以兼容模式允许。
    const isSameMajorMinor =
      currentVersion[0] === maxVersion[0] && currentVersion[1] === maxVersion[1]
    if (isHigherThanMax && !isSameMajorMinor) {
      return false
    }
  }

  return true
}

/**
 * 连接插件加载进度 WebSocket
 *
 * 使用临时 token 进行认证,异步获取 token 后连接
 */
export async function connectPluginProgressWebSocket(
  onProgress: (progress: import('./types').PluginLoadProgress) => void,
  onError?: (error: Error) => void
): Promise<() => Promise<void>> {
  try {
    return await pluginProgressClient.subscribe(onProgress)
  } catch (error) {
    const normalizedError = error instanceof Error ? error : new Error('插件进度订阅失败')
    onError?.(normalizedError)
    return async () => {}
  }
}
