import { describe, expect, it } from 'vitest'

import { clearMarketInstall, publicMarketUrl, readMarketInstall, rememberMarketInstall, PLUGIN_MARKET_VIEW_STATE_KEY } from '../plugin-market-navigation'

describe('plugin-market-navigation', () => {
  it('导出稳定的插件市场视图状态存储键', () => {
    expect(PLUGIN_MARKET_VIEW_STATE_KEY).toBe('plugins-market-view-state')
  })
  it('安装意图跨登录保留，并只传递校验后的插件 ID 和版本', () => {
    clearMarketInstall()
    rememberMarketInstall({ installPluginId: '../command', repository: 'https://evil.test' })
    expect(readMarketInstall()).toBeNull()
    rememberMarketInstall({ installPluginId: 'owner.plugin', installVersion: '1.2.3', token: 'secret' })
    expect(readMarketInstall()).toEqual({ pluginId: 'owner.plugin', version: '1.2.3' })
    rememberMarketInstall({ installPluginId: '久远.chat_summary_plugin' })
    expect(readMarketInstall()).toEqual({ pluginId: '久远.chat_summary_plugin' })
    clearMarketInstall()
    expect(readMarketInstall()).toBeNull()
  })
  it('前往站点仅通过 fragment 传递 WebUI origin', () => {
    const url = new URL(publicMarketUrl('http://127.0.0.1:8001/plugins?token=secret'))
    expect(url.origin).toBe('https://plugin.maibot.chat')
    expect(url.search).toBe('')
    expect(new URLSearchParams(url.hash.slice(1)).get('maibot')).toBe('http://127.0.0.1:8001')
    expect(url.href).not.toContain('secret')
  })
})
