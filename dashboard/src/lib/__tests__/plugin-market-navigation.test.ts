import { describe, expect, it } from 'vitest'

import { PLUGIN_MARKET_VIEW_STATE_KEY } from '../plugin-market-navigation'

describe('plugin-market-navigation', () => {
  it('导出稳定的插件市场视图状态存储键', () => {
    expect(PLUGIN_MARKET_VIEW_STATE_KEY).toBe('plugins-market-view-state')
  })
})
