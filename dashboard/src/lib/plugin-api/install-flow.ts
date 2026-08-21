/**
 * 插件安装 / 卸载 / 更新流程 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件只声明 endpoint 与业务错误文案。
 */
import { backendApi } from '@/lib/http'

import { notifyPluginPagesUpdated } from './plugin-pages-events'

interface PluginLifecycleResult {
  success: boolean
  message: string
  runtime_loaded?: boolean
  runtime_warning?: string
}

type UpdatePluginResult = {
  success: boolean
  message: string
  old_version: string
  new_version: string
  update_mode?: 'git_pull' | 'reinstall_from_backup'
  backup_path?: string
  runtime_loaded?: boolean
  runtime_warning?: string
}

/**
 * 安装插件
 */
export async function installPlugin(
  pluginId: string,
  repositoryUrl: string,
  branch: string = 'main'
): Promise<PluginLifecycleResult> {
  const response = await backendApi.post<PluginLifecycleResult>('/api/webui/plugins/install', {
    body: {
      plugin_id: pluginId,
      repository_url: repositoryUrl,
      branch: branch,
    },
    errorMessage: '安装插件失败',
  })
  if (response.success) {
    notifyPluginPagesUpdated()
  }
  return response
}

/**
 * 卸载插件
 */
export async function uninstallPlugin(
  pluginId: string
): Promise<PluginLifecycleResult> {
  const response = await backendApi.post<PluginLifecycleResult>('/api/webui/plugins/uninstall', {
    body: {
      plugin_id: pluginId,
    },
    errorMessage: '卸载插件失败',
  })
  if (response.success) {
    notifyPluginPagesUpdated()
  }
  return response
}

/**
 * 更新插件
 */
export async function updatePlugin(
  pluginId: string,
  repositoryUrl: string,
  branch: string = 'main'
): Promise<UpdatePluginResult> {
  const response = await backendApi.post<UpdatePluginResult>('/api/webui/plugins/update', {
    body: {
      plugin_id: pluginId,
      repository_url: repositoryUrl,
      branch: branch,
    },
    errorMessage: '更新插件失败',
  })
  if (response.success) {
    notifyPluginPagesUpdated()
  }
  return response
}
