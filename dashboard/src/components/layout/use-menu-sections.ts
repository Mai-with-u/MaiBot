import { useEffect, useMemo, useState } from 'react'
import { Puzzle } from 'lucide-react'

import { BOT_CONFIG_UPDATED_EVENT, getBotConfigCached } from '@/lib/config-api'
import { fetchPluginPages } from '@/lib/plugin-api/pages'
import type { PluginPageSummary } from '@/lib/plugin-api/types'

import { menuSections } from './constants'
import type { MenuIcon, MenuSection } from './types'

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

  const pluginPageIcon: MenuIcon = Puzzle
  const pluginItems = [...pages]
    .sort((left, right) => {
      if (left.order !== right.order) return left.order - right.order
      if (left.plugin_id !== right.plugin_id) return left.plugin_id.localeCompare(right.plugin_id)
      return left.page_id.localeCompare(right.page_id)
    })
    .map((page) => ({
      icon: pluginPageIcon,
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
    const abortController = new AbortController()

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

    refreshFeatureFlags()
    fetchPluginPages(abortController.signal)
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
    window.addEventListener(BOT_CONFIG_UPDATED_EVENT, refreshFeatureFlags)

    return () => {
      cancelled = true
      abortController.abort()
      window.removeEventListener(BOT_CONFIG_UPDATED_EVENT, refreshFeatureFlags)
    }
  }, [])

  return useMemo(
    () => appendPluginPages(filterMenuSections(featureFlags), pluginPages),
    [featureFlags, pluginPages]
  )
}
