import type { ApiResponse } from '@/types/api'
import type { PluginInfo } from '@/types/plugin'

import { getWsBaseUrl } from '@/lib/api-base'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import { parseResponse } from '@/lib/api-helpers'
import type { GitStatus, MaimaiVersion } from './types'

/**
 * 插件仓库配置
 */
const PLUGIN_REPO_OWNER = 'Mai-with-u'
const PLUGIN_REPO_NAME = 'plugin-repo'
const PLUGIN_REPO_BRANCH = 'main'
const PLUGIN_DETAILS_FILE = 'plugin_details.json'

/**
 * 插件列表 API 响应类型（只包含我们需要的字段）
 */
interface PluginApiResponse {
  id: string
  manifest: {
    manifest_version: number
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
    keywords: string[]
    categories?: string[]
    default_locale: string
    locales_path?: string
  }
  // 可能还有其他字段,但我们不关心
  [key: string]: unknown
}

/**
 * 从远程获取插件列表(通过后端代理避免 CORS)
 */
export async function fetchPluginList(): Promise<ApiResponse<PluginInfo[]>> {
  const response = await fetchWithAuth('/api/webui/plugins/fetch-raw', {
    method: 'POST',
    body: JSON.stringify({
      owner: PLUGIN_REPO_OWNER,
      repo: PLUGIN_REPO_NAME,
      branch: PLUGIN_REPO_BRANCH,
      file_path: PLUGIN_DETAILS_FILE
    })
  })
  
  const apiResult = await parseResponse<{ success: boolean; data: string; error?: string }>(response)
  
  if (!apiResult.success) {
    return apiResult
  }
  
  const result = apiResult.data
  if (!result.success || !result.data) {
    return {
      success: false,
      error: result.error || '获取插件列表失败'
    }
  }
  
  const data: PluginApiResponse[] = JSON.parse(result.data)
  
  const pluginList = data
    .filter(item => {
      if (!item?.id || !item?.manifest) {
        console.warn('跳过无效插件数据:', item)
        return false
      }
      if (!item.manifest.name || !item.manifest.version) {
        console.warn('跳过缺少必需字段的插件:', item.id)
        return false
      }
      return true
    })
    .map((item) => ({
      id: item.id,
      manifest: {
        manifest_version: item.manifest.manifest_version || 1,
        name: item.manifest.name,
        version: item.manifest.version,
        description: item.manifest.description || '',
        author: item.manifest.author || { name: 'Unknown' },
        license: item.manifest.license || 'Unknown',
        host_application: item.manifest.host_application || { min_version: '0.0.0' },
        homepage_url: item.manifest.homepage_url,
        repository_url: item.manifest.repository_url,
        keywords: item.manifest.keywords || [],
        categories: item.manifest.categories || [],
        default_locale: item.manifest.default_locale || 'zh-CN',
        locales_path: item.manifest.locales_path,
      },
      downloads: 0,
      rating: 0,
      review_count: 0,
      installed: false,
      published_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }))
  
  return {
    success: true,
    data: pluginList
  }
}

/**
 * 检查本机 Git 安装状态
 */
export async function checkGitStatus(): Promise<ApiResponse<GitStatus>> {
  const response = await fetchWithAuth('/api/webui/plugins/git-status')
  
  const apiResult = await parseResponse<GitStatus>(response)
  
  if (!apiResult.success) {
    return {
      success: true,
      data: {
        installed: false,
        error: '无法检测 Git 安装状态'
      }
    }
  }
  
  return apiResult
}

/**
 * 获取麦麦版本信息
 */
export async function getMaimaiVersion(): Promise<ApiResponse<MaimaiVersion>> {
  const response = await fetchWithAuth('/api/webui/plugins/version')
  
  const apiResult = await parseResponse<MaimaiVersion>(response)
  
  if (!apiResult.success) {
    return {
      success: true,
      data: {
        version: '0.0.0',
        version_major: 0,
        version_minor: 0,
        version_patch: 0
      }
    }
  }
  
  return apiResult
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
  // 解析插件最小版本
  const minParts = pluginMinVersion.split('.').map(p => parseInt(p) || 0)
  const minMajor = minParts[0] || 0
  const minMinor = minParts[1] || 0
  const minPatch = minParts[2] || 0
  
  // 检查最小版本
  if (maimaiVersion.version_major < minMajor) return false
  if (maimaiVersion.version_major === minMajor && maimaiVersion.version_minor < minMinor) return false
  if (maimaiVersion.version_major === minMajor && 
      maimaiVersion.version_minor === minMinor && 
      maimaiVersion.version_patch < minPatch) return false
  
  // 检查最大版本(如果有)
  if (pluginMaxVersion) {
    const maxParts = pluginMaxVersion.split('.').map(p => parseInt(p) || 0)
    const maxMajor = maxParts[0] || 0
    const maxMinor = maxParts[1] || 0
    const maxPatch = maxParts[2] || 0
    
    if (maimaiVersion.version_major > maxMajor) return false
    if (maimaiVersion.version_major === maxMajor && maimaiVersion.version_minor > maxMinor) return false
    if (maimaiVersion.version_major === maxMajor && 
        maimaiVersion.version_minor === maxMinor && 
        maimaiVersion.version_patch > maxPatch) return false
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
  onError?: (error: Event) => void
): Promise<WebSocket | null> {
  const wsBase = await getWsBaseUrl()
  const wsUrl = `${wsBase}/api/webui/ws/plugin-progress`
  
  // 使用 ws-utils 创建 WebSocket
  const { createReconnectingWebSocket } = await import('@/lib/ws-utils')
  const wsControl = createReconnectingWebSocket(wsUrl, {
    onMessage: (data: string) => {
      try {
        const progressData = JSON.parse(data) as import('./types').PluginLoadProgress
        onProgress(progressData)
      } catch (error) {
        console.error('Failed to parse progress data:', error)
      }
    },
    onOpen: () => {
      console.log('Plugin progress WebSocket connected')
    },
    onClose: () => {
      console.log('Plugin progress WebSocket disconnected')
    },
    onError: (error) => {
      console.error('Plugin progress WebSocket error:', error)
      onError?.(error)
    },
    heartbeatInterval: 30000,
    maxRetries: 10,
    backoffBase: 1000,
    maxBackoff: 30000,
  })
  
  // 启动连接
  await wsControl.connect()
  
  // 返回 WebSocket 实例(用于外部检查连接状态)
  return wsControl.getWebSocket()
}
