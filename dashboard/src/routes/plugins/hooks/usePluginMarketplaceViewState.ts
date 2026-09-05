/**
 * usePluginMarketplaceViewState —— 插件市场页视图状态领域 hook（页面逻辑下沉）。
 *
 * 收编工具栏视图态：
 * - sessionStorage 读写 search / type / sort / showInstalledPlugins；
 * - 滚动位置保存与加载完成后的 restore。
 *
 * MarketplaceTab 内部的过滤、排序、surprise seed 不进入本 hook。
 */
import { useEffect, useRef, useState } from 'react'

import { PLUGIN_MARKET_VIEW_STATE_KEY } from '@/lib/plugin-market-navigation'

import { PLUGIN_TYPE_OPTIONS, type MarketplaceSortKey } from '../types'

const PLUGIN_MARKET_SCROLL_TOP_KEY = 'plugins-market-scroll-top'
const MARKETPLACE_SORT_KEYS: MarketplaceSortKey[] = ['default', 'latest', 'downloads', 'likes', 'rating']

interface PluginMarketplaceViewState {
  searchQuery: string
  pluginTypeFilter: string
  marketplaceSortBy: MarketplaceSortKey
  showInstalledPlugins: boolean
}

const DEFAULT_PLUGIN_MARKET_VIEW_STATE: PluginMarketplaceViewState = {
  searchQuery: '',
  pluginTypeFilter: 'all',
  marketplaceSortBy: 'default',
  showInstalledPlugins: false,
}

const readPluginMarketplaceViewState = (): PluginMarketplaceViewState => {
  const savedState = sessionStorage.getItem(PLUGIN_MARKET_VIEW_STATE_KEY)
  if (!savedState) {
    return DEFAULT_PLUGIN_MARKET_VIEW_STATE
  }

  const parsed = JSON.parse(savedState) as Partial<PluginMarketplaceViewState>
  const pluginTypeFilter = typeof parsed.pluginTypeFilter === 'string'
    && (parsed.pluginTypeFilter === 'all' || PLUGIN_TYPE_OPTIONS.some(option => option.value === parsed.pluginTypeFilter))
    ? parsed.pluginTypeFilter
    : DEFAULT_PLUGIN_MARKET_VIEW_STATE.pluginTypeFilter
  const marketplaceSortBy = parsed.marketplaceSortBy
    && MARKETPLACE_SORT_KEYS.includes(parsed.marketplaceSortBy)
    ? parsed.marketplaceSortBy
    : DEFAULT_PLUGIN_MARKET_VIEW_STATE.marketplaceSortBy

  return {
    searchQuery: typeof parsed.searchQuery === 'string'
      ? parsed.searchQuery
      : DEFAULT_PLUGIN_MARKET_VIEW_STATE.searchQuery,
    pluginTypeFilter,
    marketplaceSortBy,
    showInstalledPlugins: typeof parsed.showInstalledPlugins === 'boolean'
      ? parsed.showInstalledPlugins
      : DEFAULT_PLUGIN_MARKET_VIEW_STATE.showInstalledPlugins,
  }
}

export interface UsePluginMarketplaceViewStateOptions {
  loading: boolean
  pluginCount: number
}

export function usePluginMarketplaceViewState({
  loading,
  pluginCount,
}: UsePluginMarketplaceViewStateOptions) {
  const scrollViewportRef = useRef<HTMLDivElement | null>(null)
  const scrollRestoredRef = useRef(false)
  const [initialViewState] = useState(readPluginMarketplaceViewState)
  const [searchQuery, setSearchQuery] = useState(initialViewState.searchQuery)
  const [pluginTypeFilter, setPluginTypeFilter] = useState(initialViewState.pluginTypeFilter)
  const [marketplaceSortBy, setMarketplaceSortBy] = useState<MarketplaceSortKey>(
    initialViewState.marketplaceSortBy
  )
  const [showInstalledPlugins, setShowInstalledPlugins] = useState(initialViewState.showInstalledPlugins)

  useEffect(() => {
    sessionStorage.setItem(
      PLUGIN_MARKET_VIEW_STATE_KEY,
      JSON.stringify({
        searchQuery,
        pluginTypeFilter,
        marketplaceSortBy,
        showInstalledPlugins,
      } satisfies PluginMarketplaceViewState)
    )
  }, [marketplaceSortBy, pluginTypeFilter, searchQuery, showInstalledPlugins])

  useEffect(() => {
    const viewport = scrollViewportRef.current
    if (!viewport) {
      return
    }

    const handleScroll = () => {
      sessionStorage.setItem(PLUGIN_MARKET_SCROLL_TOP_KEY, String(viewport.scrollTop))
    }

    viewport.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      viewport.removeEventListener('scroll', handleScroll)
    }
  }, [])

  useEffect(() => {
    if (scrollRestoredRef.current || loading) {
      return
    }

    const viewport = scrollViewportRef.current
    if (!viewport) {
      return
    }

    const savedScrollTop = Number(sessionStorage.getItem(PLUGIN_MARKET_SCROLL_TOP_KEY) ?? 0)
    if (!Number.isFinite(savedScrollTop) || savedScrollTop <= 0) {
      scrollRestoredRef.current = true
      return
    }

    const frameId = requestAnimationFrame(() => {
      viewport.scrollTop = savedScrollTop
      scrollRestoredRef.current = true
    })

    return () => {
      cancelAnimationFrame(frameId)
    }
  }, [loading, pluginCount])

  return {
    scrollViewportRef,
    searchQuery,
    setSearchQuery,
    pluginTypeFilter,
    setPluginTypeFilter,
    marketplaceSortBy,
    setMarketplaceSortBy,
    showInstalledPlugins,
    setShowInstalledPlugins,
  }
}
