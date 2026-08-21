import { useEffect, useMemo, useState } from 'react'
import { BarChart3, Box, Database, FileText, Gauge, Puzzle, Settings, Store, Wrench } from 'lucide-react'

import { BOT_CONFIG_UPDATED_EVENT, getBotConfigCached } from '@/lib/config-api'
import { fetchPluginPages } from '@/lib/plugin-api/pages'
import { PLUGIN_PAGES_UPDATED_EVENT } from '@/lib/plugin-api/plugin-pages-events'
import type { PluginPageSummary } from '@/lib/plugin-api/types'

import { menuSections } from './constants'
import type { MenuIcon, MenuSection } from './types'

const PLUGIN_PAGE_ICON_MAP: Record<string, MenuIcon> = {
  'bar-chart-3': BarChart3,
  box: Box,
  database: Database,
  'file-text': FileText,
  gauge: Gauge,
  puzzle: Puzzle,
  settings: Settings,
  store: Store,
  wrench: Wrench,
}

/** 将 Manifest 中的安全 icon 名称解析为 Host 已内置的 Lucide 图标。 */
export function resolvePluginPageIcon(iconName: string | null): MenuIcon {
  return PLUGIN_PAGE_ICON_MAP[String(iconName ?? '').trim().toLowerCase()] ?? Puzzle
}

interface MenuFeatureFlags {
  behaviorLearning: boolean
  replyEffects: boolean
}

function resolveMenuFeatureFlags(config: Record<string, unknown> | null): MenuFeatureFlags {
  const experimental = config?.experimental
  const behaviorLearning =
    experimental && typeof experimental === 'object' && 'enable_behavior_learning' in experimental
      ? Boolean((experimental as Record<string, unknown>).enable_behavior_learning)
      : true
  const debug = config?.debug
  const replyEffects =
    debug && typeof debug === 'object'
      ? (debug as Record<string, unknown>).enable_reply_effect_tracking === true
      : false

  return {
    behaviorLearning,
    replyEffects,
  }
}

function filterMenuSections(flags: MenuFeatureFlags | null): MenuSection[] {
  return menuSections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => {
        if (item.featureFlag === 'behaviorLearning') return flags?.behaviorLearning === true
        if (item.featureFlag === 'replyEffects') return flags?.replyEffects === true
        return true
      }),
    }))
    .filter((section) => section.items.length > 0)
}

/** 将 Host 页面清单追加到固定的扩展与集成分组，且不修改静态菜单源数据。 */
export function appendPluginPages(
  sections: MenuSection[],
  pages: PluginPageSummary[]
): MenuSection[] {
  if (pages.length === 0) {
    return sections
  }

  const pluginItems = [...pages]
    .sort((left, right) => {
      if (left.order !== right.order) return left.order - right.order
      if (left.plugin_id !== right.plugin_id) return left.plugin_id.localeCompare(right.plugin_id)
      return left.page_id.localeCompare(right.page_id)
    })
    .map((page) => ({
      icon: resolvePluginPageIcon(page.icon),
      label: page.title,
      labelMode: 'text' as const,
      path: page.route,
    }))

  return sections.map((section) =>
    section.title === 'sidebar.groups.extensionsMonitor'
      ? { ...section, items: [...section.items, ...pluginItems] }
      : section
  )
}

export function useMenuSections(): MenuSection[] {
  const [featureFlags, setFeatureFlags] = useState<MenuFeatureFlags | null>(null)
  const [pluginPages, setPluginPages] = useState<PluginPageSummary[]>([])

  useEffect(() => {
    let cancelled = false
    let pluginPagesController: AbortController | null = null

    const refreshFeatureFlags = () => {
      getBotConfigCached()
        .then((result) => {
          if (!cancelled) {
            setFeatureFlags(resolveMenuFeatureFlags(result ?? null))
          }
        })
        .catch(() => {
          if (!cancelled) {
            setFeatureFlags({ behaviorLearning: true, replyEffects: false })
          }
        })
    }

    const refreshPluginPages = () => {
      pluginPagesController?.abort()
      pluginPagesController = new AbortController()
      fetchPluginPages(pluginPagesController.signal)
        .then((response) => {
          if (!cancelled) {
            setPluginPages(response.pages)
          }
        })
        .catch((error: unknown) => {
          if (cancelled || (error instanceof Error && error.name === 'AbortError')) {
            return
          }
          // 页面清单失败不能影响静态插件管理、市场和 MCP 入口。
          console.error('加载插件 WebUI 页面失败:', error)
          setPluginPages([])
        })
    }

    refreshFeatureFlags()
    refreshPluginPages()
    window.addEventListener(BOT_CONFIG_UPDATED_EVENT, refreshFeatureFlags)
    window.addEventListener(PLUGIN_PAGES_UPDATED_EVENT, refreshPluginPages)

    return () => {
      cancelled = true
      pluginPagesController?.abort()
      window.removeEventListener(BOT_CONFIG_UPDATED_EVENT, refreshFeatureFlags)
      window.removeEventListener(PLUGIN_PAGES_UPDATED_EVENT, refreshPluginPages)
    }
  }, [])

  return useMemo(
    () => appendPluginPages(filterMenuSections(featureFlags), pluginPages),
    [featureFlags, pluginPages]
  )
}
