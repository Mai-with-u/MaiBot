export const PLUGIN_MARKET_VIEW_STATE_KEY = 'plugins-market-view-state'

const PENDING_INSTALL_KEY = 'plugins-market-pending-install'
const PLUGIN_ID = /^[\p{L}\p{N}_.-]{1,256}$/u

export interface MarketInstallIntent { pluginId: string; version?: string }

/** 只保存目录 ID，不接受外部仓库地址、命令或认证信息。 */
export function rememberMarketInstall(search: Record<string, unknown>): void {
  if (typeof search.installPluginId !== 'string' || !PLUGIN_ID.test(search.installPluginId)) return
  const intent: MarketInstallIntent = { pluginId: search.installPluginId }
  if (typeof search.installVersion === 'string' && /^[\w.+-]{1,80}$/.test(search.installVersion)) {
    intent.version = search.installVersion
  }
  sessionStorage.setItem(PENDING_INSTALL_KEY, JSON.stringify(intent))
}

export function readMarketInstall(): MarketInstallIntent | null {
  const raw = sessionStorage.getItem(PENDING_INSTALL_KEY)
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<MarketInstallIntent>
    if (typeof value.pluginId === 'string' && PLUGIN_ID.test(value.pluginId)) return { pluginId: value.pluginId, version: value.version }
  } catch { /* 损坏的浏览器状态不应阻止市场打开。 */ }
  sessionStorage.removeItem(PENDING_INSTALL_KEY)
  return null
}

export function clearMarketInstall(): void {
  sessionStorage.removeItem(PENDING_INSTALL_KEY)
}

export function publicMarketUrl(origin: string): string {
  const site = new URL('https://plugin.maibot.chat/')
  // Electron 的 file: 页面没有 HTTP origin；由网页版设置实例地址。
  if (origin === 'null') return site.href
  const webui = new URL(origin)
  if (webui.protocol === 'http:' || webui.protocol === 'https:') {
    // Fragment 不会发送给市场服务器，也不携带 MaiBot 认证令牌。
    site.hash = new URLSearchParams({ maibot: webui.origin }).toString()
  }
  return site.href
}
