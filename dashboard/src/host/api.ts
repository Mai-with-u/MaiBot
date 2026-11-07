import { createApiClient } from '@/lib/http/client'
import { getControlApiBaseUrl } from '@/lib/api-base'

import { getHostRuntime, setHostRuntime, updateHostBots } from './state'
import type { HostMode } from './state'

export interface HostBot {
  bot_id: string
  name: string
  directory: string
  created_at: string
  /** false 表示目录不归宿主所有（原地登记的旧实例），宿主永不删除或移动它。 */
  owned: boolean
  initialization: 'initializing' | 'initialized' | 'failed'
  initialization_error: string | null
  desired_state: 'running' | 'stopped'
  restart_policy: 'on-failure' | 'never'
  status: 'starting' | 'migrating' | 'running' | 'stopping' | 'stopped' | 'exited' | 'failed' | 'crash_loop'
  /** Worker 上报的阶段说明，例如数据迁移进度。 */
  detail: string
  pid: number | null
  started_at: string | null
  exited_at: string | null
  exit_code: number | null
  error: string | null
  /** 崩溃后计划自动重启的时间（Unix 秒）。 */
  retry_at: number | null
  log_path: string
}

export interface HostBotList {
  bots: HostBot[]
  workspace: string
}

export type BotAction = 'start' | 'stop' | 'restart'

const hostApi = createApiClient({
  resolveBaseUrl: getControlApiBaseUrl,
  auth: 'cookie',
  onUnauthorized: () => {
    window.location.assign('/auth')
  },
})
const mutationHeaders = { 'X-MaiBot-Host': '1' }

export interface CommonSettings {
  restart_required: boolean
  retention: Record<string, Record<string, number | boolean>>
  revision: string
  webui: Record<string, string | number | boolean | string[]>
}
export const saveWebUIStyle = (webui_style: number) => hostApi.patch<CommonSettings>('/api/host/settings/appearance', { body: { webui_style }, headers: mutationHeaders })
export const getCommonSettings = () => hostApi.get<CommonSettings>('/api/host/settings', { cache: 'no-store' })
export const saveCommonSettings = (body: CommonSettings) => hostApi.put<CommonSettings>('/api/host/settings', { body, headers: mutationHeaders })

export const renameHostBot = (id: string, name: string) => hostApi.patch(`/api/host/bots/${id}`, { body: { name }, headers: mutationHeaders })
export const cloneHostBot = (id: string, name: string) => hostApi.post(`/api/host/bots/${id}/clone`, { body: { name }, headers: mutationHeaders, signal: AbortSignal.timeout(1_800_000) })
export const archiveHostBot = (id: string) => hostApi.delete<{ directory: string }>(`/api/host/bots/${id}`, { headers: mutationHeaders, signal: AbortSignal.timeout(180_000) })
export interface HostUpdateCheck {
  local: { version: string; commit?: string; blocker?: string }
  latest_commit?: string
  latest_version?: string
  can_apply: boolean
  blocker_messages: string[]
}
export interface HostUpdateStatus {
  busy: boolean
  local: { version: string }
  last_result: { success: boolean; error: string; rollback_error: string; finished_at: number; to_version: string } | null
}
export const getHostUpdate = () => hostApi.get<HostUpdateStatus>('/api/host/maintenance/update', { cache: 'no-store', signal: AbortSignal.timeout(10_000) })
export const HOST_UPDATE_CHECK_EVENT = 'maibot-host-update-check'
export async function checkHostUpdate(): Promise<HostUpdateCheck> {
  const result = await hostApi.post<HostUpdateCheck>('/api/host/maintenance/update/check', { headers: mutationHeaders, signal: AbortSignal.timeout(120_000) })
  window.dispatchEvent(new CustomEvent(HOST_UPDATE_CHECK_EVENT, { detail: result }))
  return result
}
export const applyHostUpdate = (target: string) => hostApi.post('/api/host/maintenance/update', { body: { target }, headers: mutationHeaders, signal: AbortSignal.timeout(600_000) })

let selectionStorageKey = 'maibot-selected-bot'
let listRequest: Promise<HostBotList> | null = null

export async function bootstrapHostRuntime(): Promise<void> {
  const origin = await getControlApiBaseUrl()
  // Electron 尚未选择后端时交给现有连接向导，不能请求 file:// 下的相对 API。
  if (window.location.protocol === 'file:' && !origin) return
  // 一次性票据先换会话再读取 mode，才能取得登录后的默认 Bot；凭据不留在地址栏。
  const ticket = new URLSearchParams(window.location.hash.slice(1)).get('ticket')
  if (ticket) {
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    await hostApi.post('/api/host/session', {
      body: { ticket },
      headers: mutationHeaders,
      signal: AbortSignal.timeout(15_000),
    })
  }
  selectionStorageKey = `maibot-selected-bot:${origin || window.location.origin}`
  const pinned = sessionStorage.getItem(selectionStorageKey)
  const query = pinned ? `?bot_id=${encodeURIComponent(pinned)}` : ''
  const response = await fetch(`${origin}/api/host/mode${query}`, {
    credentials: 'include',
    cache: 'no-store',
    signal: AbortSignal.timeout(15_000),
  })
  if (response.status === 404 || response.status === 405) return
  if (!response.ok) throw new Error(`读取 WebUI 运行模式失败（HTTP ${response.status}）`)
  const mode = (await response.json()) as HostMode
  if (mode.host !== true) throw new Error('服务器返回了无效的 WebUI 运行模式')
  const selectedId = pinned === null ? mode.selected_bot_id : pinned || null
  // 第一次进入也锁定默认选择；随后刷新页面不跟随另一个标签页改变的 Cookie。
  if (mode.authenticated) sessionStorage.setItem(selectionStorageKey, selectedId ?? '')
  setHostRuntime({ ...mode, selected_bot_id: selectedId, selected_bot_running: false, bots: [] })

  if (getHostRuntime().authenticated) await listHostBots()
}

export function listHostBots(): Promise<HostBotList> {
  listRequest ??= hostApi
    .get<HostBotList>('/api/host/bots', {
      cache: 'no-store',
      signal: AbortSignal.timeout(15_000),
    })
    .then((result) => {
      updateHostBots(result.bots)
      return result
    })
    .finally(() => {
      listRequest = null
    })
  return listRequest
}

export async function selectHostBot(botId: string | null): Promise<void> {
  await hostApi.post('/api/host/selection', {
    body: { bot_id: botId },
    headers: mutationHeaders,
    signal: AbortSignal.timeout(15_000),
  })
  sessionStorage.setItem(selectionStorageKey, botId ?? '')
  // 完整卸载页面、配置缓存与 WebSocket，避免旧 Bot 的请求/草稿留在新上下文。
  window.location.reload()
}

export function createHostBot(name: string): Promise<unknown> {
  return hostApi.post('/api/host/bots', {
    body: { name },
    headers: mutationHeaders,
    signal: AbortSignal.timeout(100_000),
  })
}

export function controlHostBot(botId: string, action: BotAction): Promise<HostBot> {
  return hostApi.post(`/api/host/bots/${encodeURIComponent(botId)}/${action}`, {
    headers: mutationHeaders,
    signal: AbortSignal.timeout(action === 'stop' ? 100_000 : 1_800_000),
  })
}

export interface PluginRequirement {
  requirement: string
  package: string
  plugin_ids: string[]
}

export interface PluginDependencyReport {
  error?: string
  bots: { bot_id: string; name: string; requirements: string[] }[]
  requirements: PluginRequirement[]
  busy?: boolean
  installed?: boolean
  conflicts?: string[]
  all_requirements?: PluginRequirement[]
}

export function getPluginDependencies(): Promise<PluginDependencyReport> {
  return hostApi.get('/api/host/maintenance/plugin-dependencies', {
    cache: 'no-store',
    signal: AbortSignal.timeout(150_000),
  })
}

/** 宿主会先停止全部 Bot，安装完成后按期望状态恢复；下载安装可能持续较久。 */
export function installPluginDependencies(): Promise<PluginDependencyReport> {
  return hostApi.post('/api/host/maintenance/plugin-dependencies', {
    headers: mutationHeaders,
    signal: AbortSignal.timeout(1_800_000),
  })
}
