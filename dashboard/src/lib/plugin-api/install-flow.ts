/**
 * 插件安装 / 卸载 / 更新流程 API
 *
 * 请求样板（认证、解析、错误格式化）由 @/lib/http 的请求客户端承担；
 * 本文件声明 endpoint 与业务错误文案，并展示安装或更新后的依赖提醒。
 */
import { backendApi } from '@/lib/http'
import { toast } from '@/hooks/use-toast'

type InstallPluginResult = { success: boolean; message: string; warnings?: string[] }

type UpdatePluginResult = {
  success: boolean
  message: string
  old_version: string
  new_version: string
  update_mode?: 'git_pull' | 'reinstall_from_backup' | 'release'
  backup_path?: string
  warnings?: string[]
}

function showDependencyWarnings(result: { warnings?: string[] }) {
  if (result.warnings?.length) {
    toast({
      title: '插件依赖提醒',
      description: result.warnings.join('；'),
    })
  }
}

/**
 * 安装插件
 */
export async function installPlugin(
  pluginId: string,
  repositoryUrl: string,
  branch: string = 'main',
  release?: { version: string } | null
): Promise<InstallPluginResult> {
  const result = await backendApi.post<InstallPluginResult>('/api/webui/plugins/install', {
    body: {
      plugin_id: pluginId,
      repository_url: repositoryUrl,
      branch: branch,
      ...(release === null ? {} : release || (branch === 'main' ? { version: 'latest' } : {})),
    },
    errorMessage: '安装插件失败',
  })
  showDependencyWarnings(result)
  return result
}

/**
 * 卸载插件
 */
export async function uninstallPlugin(
  pluginId: string
): Promise<{ success: boolean; message: string }> {
  return backendApi.post<{ success: boolean; message: string }>('/api/webui/plugins/uninstall', {
    body: {
      plugin_id: pluginId,
    },
    errorMessage: '卸载插件失败',
  })
}

/**
 * 更新插件
 */
export async function updatePlugin(
  pluginId: string,
  repositoryUrl: string,
  branch: string = 'main',
  release?: { version: string } | null,
  signal?: AbortSignal
): Promise<UpdatePluginResult> {
  const result = await backendApi.post<UpdatePluginResult>('/api/webui/plugins/update', {
    body: {
      plugin_id: pluginId,
      repository_url: repositoryUrl,
      branch: branch,
      ...(release === null ? {} : release || (branch === 'main' ? { version: 'latest' } : {})),
    },
    errorMessage: '更新插件失败',
    signal,
  })
  showDependencyWarnings(result)
  return result
}
