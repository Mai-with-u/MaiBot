import type { PluginInfo, PluginType } from '@/types/plugin'

import { ApiError, backendApi } from '@/lib/http'
import { pluginProgressClient } from '@/lib/plugin-progress-client'
import type { GitStatus, MaimaiVersion } from './types'

/**
 * 插件仓库配置
 */
const PLUGIN_REPO_OWNER = 'Mai-with-u'
const PLUGIN_REPO_NAME = 'plugin-repo'
const PLUGIN_REPO_BRANCH = 'main'
const PLUGIN_DETAILS_FILE = 'plugin_details.json'
const PLUGIN_LIST_CACHE_TTL = 5 * 60 * 1000
const PLUGIN_LIST_STORAGE_KEY = 'maibot-plugin-market-list-cache'
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

let pluginListCache: { timestamp: number; result: PluginInfo[] } | null = null
let pluginListRequest: Promise<PluginInfo[]> | null = null

interface PluginListStorageCache {
  timestamp: number
  data: PluginInfo[]
}

/**
 * 插件列表 API 响应类型（只包含我们需要的字段）
 */
interface PluginApiResponse {
  id?: string
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
    default_locale: string
    locales_path?: string
  }
  // 可能还有其他字段,但我们不关心
  [key: string]: unknown
}

function uniqueNonEmptyValues(values: Array<string | undefined>): string[] {
  return Array.from(new Set(values.map(value => value?.trim()).filter((value): value is string => Boolean(value))))
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
    homepage_url: homepageUrl,
    repository_url: repositoryUrl,
    urls: manifest.urls,
    keywords: manifest.keywords || [],
    plugin_type: normalizePluginType(manifest.plugin_type),
    display: manifest.display,
    default_locale: manifest.default_locale || 'zh-CN',
    locales_path: manifest.locales_path,
  }
}

function normalizePluginAssetUrl(value: unknown): string | undefined {
  if (typeof value !== 'string' || !value.trim()) {
    return undefined
  }

  const normalizedValue = value.trim()
  if (/^https?:\/\//.test(normalizedValue)) {
    return normalizedValue
  }

  const normalizedPath = normalizedValue.replace(/^\/+/, '')
  if (!normalizedPath || normalizedPath.includes('..')) {
    return undefined
  }

  return `https://raw.githubusercontent.com/${PLUGIN_REPO_OWNER}/${PLUGIN_REPO_NAME}/${PLUGIN_REPO_BRANCH}/${normalizedPath}`
}

function normalizePluginAssets(assets: PluginApiResponse['assets']): PluginInfo['assets'] | undefined {
  const icon64 = normalizePluginAssetUrl(assets?.icon_64)
  if (!icon64) {
    return undefined
  }

  return {
    icon_64: icon64,
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

function readPluginListStorageCache(): PluginListStorageCache | null {
  if (typeof localStorage === 'undefined') {
    return null
  }

  try {
    const rawCache = localStorage.getItem(PLUGIN_LIST_STORAGE_KEY)
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

function writePluginListStorageCache(data: PluginInfo[]): void {
  if (typeof localStorage === 'undefined') {
    return
  }

  try {
    localStorage.setItem(
      PLUGIN_LIST_STORAGE_KEY,
      JSON.stringify({
        timestamp: Date.now(),
        data,
      })
    )
  } catch (error) {
    console.warn('写入插件市场缓存失败:', error)
  }
}

export function getCachedPluginList(): PluginInfo[] | null {
  if (pluginListCache) {
    return pluginListCache.result
  }

  const storedCache = readPluginListStorageCache()
  if (!storedCache) {
    return null
  }

  pluginListCache = { timestamp: storedCache.timestamp, result: storedCache.data }
  return storedCache.data
}

/**
 * 从远程获取插件列表(通过后端代理避免 CORS)
 */
async function fetchPluginListUncached(): Promise<PluginInfo[]> {
  const result = await backendApi.post<{ success: boolean; data: string; error?: string }>(
    '/api/webui/plugins/fetch-raw',
    {
      body: {
        owner: PLUGIN_REPO_OWNER,
        repo: PLUGIN_REPO_NAME,
        branch: PLUGIN_REPO_BRANCH,
        file_path: PLUGIN_DETAILS_FILE
      },
      errorMessage: '获取插件列表失败',
    }
  )

  // 业务级失败：该 endpoint 的错误字段是 error 而非 message，不走 requireSuccess
  if (!result.success || !result.data) {
    throw new ApiError(result.error || '获取插件列表失败', { detail: result })
  }

  const data: PluginApiResponse[] = JSON.parse(result.data)

  const pluginList = data
    .filter(item => {
        if (!item?.manifest) {
          console.warn('跳过无效插件数据:', item)
          return false
        }
        const pluginId = item.manifest.id || item.id
        if (!pluginId) {
          console.warn('跳过缺少 ID 的插件:', item)
          return false
        }
        if (!item.manifest.name || !item.manifest.version) {
          console.warn('跳过缺少必需字段的插件:', item.id)
          return false
        }
        return true
      })
      .map((item, index) => {
        const manifestId = item.manifest.id?.trim()
        const marketplaceId = item.id?.trim()
        const pluginId = manifestId || marketplaceId!

        return {
          id: pluginId,
          marketplace_id: marketplaceId,
          marketplace_order: index,
          stats_ids: uniqueNonEmptyValues([manifestId]),
          manifest: normalizePluginManifest({ ...item.manifest, id: pluginId }),
          assets: normalizePluginAssets(item.assets),
          downloads: 0,
          rating: 0,
          review_count: 0,
          installed: false,
          source: 'market' as const,
          published_at: normalizeDateString(item.published_at ?? item.created_at ?? item.added_at),
          updated_at: normalizeDateString(item.updated_at ?? item.modified_at),
        }
      })

  return pluginList
}

export async function fetchPluginList(options: { forceRefresh?: boolean } = {}): Promise<PluginInfo[]> {
  if (
    !options.forceRefresh
    && pluginListCache
    && Date.now() - pluginListCache.timestamp < PLUGIN_LIST_CACHE_TTL
  ) {
    return pluginListCache.result
  }

  if (!options.forceRefresh && !pluginListCache) {
    const storedCache = readPluginListStorageCache()
    if (storedCache && Date.now() - storedCache.timestamp < PLUGIN_LIST_CACHE_TTL) {
      pluginListCache = { timestamp: storedCache.timestamp, result: storedCache.data }
      return storedCache.data
    }
  }

  if (!pluginListRequest || options.forceRefresh) {
    // 仅在成功（fetchPluginListUncached 未抛错）时写入内存/本地缓存
    pluginListRequest = fetchPluginListUncached()
      .then((result) => {
        pluginListCache = { timestamp: Date.now(), result }
        writePluginListStorageCache(result)
        return result
      })
      .finally(() => {
        pluginListRequest = null
      })
  }

  return pluginListRequest
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
        error: '无法检测 Git 安装状态'
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
        version_patch: 0
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
  const parts = normalizedVersion.split('.').map(part => Number.parseInt(part, 10))
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

    // 与运行时 manifest 校验保持一致：同一主版本内高于声明上限时，以兼容模式允许。
    if (isHigherThanMax && currentVersion[0] !== maxVersion[0]) {
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
