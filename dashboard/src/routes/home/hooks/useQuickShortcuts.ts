/**
 * useQuickShortcuts —— 首页快捷菜单领域 hook（页面逻辑下沉）。
 *
 * 收编 index.tsx 的快捷菜单状态机：
 * - quickShortcutIds（localStorage 持久化）、dialog 开关、搜索关键字
 * - 插件快捷入口（pluginShortcuts / isPluginShortcutsLoading + 加载 effect）
 * - quickShortcutOptions / map / selected / filtered 派生态，toggle / reset 操作
 *
 * 设计判断：
 * - 快捷菜单内置项依赖外部交互态（重启、审核器、未审核数量），故由调用方注入
 *   handleRestart / isRestarting / uncheckedCount / onOpenReviewer，hook 负责编排其余全部逻辑。
 * - 插件快捷入口的纯函数 helper（id 编解码、schema 解析等）随 hook 一并下沉。
 */
import {
  BarChart3,
  ClipboardCheck,
  FileText,
  HardDrive,
  MessageSquare,
  Puzzle,
  RotateCcw,
  Settings,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import {
  getInstalledPlugins,
  getPluginConfigSchema,
  type InstalledPlugin,
  type PluginConfigSchema,
} from '@/lib/plugin-api'

import type { QuickShortcutDefinition } from '../types'

const QUICK_SHORTCUT_STORAGE_KEY = 'maibot-home-quick-shortcuts'
const DEFAULT_QUICK_SHORTCUT_IDS = [
  'action:restart',
  'action:expression-review',
  'route:logs',
  'route:plugin-market',
  'route:settings',
  'external:statistics',
]

function loadQuickShortcutIds(): string[] {
  const fallback = [...DEFAULT_QUICK_SHORTCUT_IDS]
  if (typeof window === 'undefined') {
    return fallback
  }

  const stored = localStorage.getItem(QUICK_SHORTCUT_STORAGE_KEY)
  if (!stored) {
    return fallback
  }

  try {
    const parsed = JSON.parse(stored)
    if (Array.isArray(parsed)) {
      const ids = parsed.filter((item): item is string => typeof item === 'string' && item.length > 0)
      return ids.length > 0 ? Array.from(new Set(ids)) : fallback
    }
  } catch {
    return fallback
  }

  return fallback
}

function saveQuickShortcutIds(ids: string[]): void {
  localStorage.setItem(QUICK_SHORTCUT_STORAGE_KEY, JSON.stringify(Array.from(new Set(ids))))
}

function getPluginShortcutId(pluginId: string, tabId?: string): string {
  const encodedPluginId = encodeURIComponent(pluginId)
  if (!tabId) {
    return `plugin-config:${encodedPluginId}`
  }
  return `plugin-config:${encodedPluginId}:tab:${encodeURIComponent(tabId)}`
}

function parsePluginShortcutId(id: string): { pluginId: string; tabId?: string } | null {
  if (!id.startsWith('plugin-config:')) {
    return null
  }

  const [, encodedPluginId, marker, encodedTabId] = id.split(':')
  if (!encodedPluginId) {
    return null
  }

  return {
    pluginId: decodeURIComponent(encodedPluginId),
    tabId: marker === 'tab' && encodedTabId ? decodeURIComponent(encodedTabId) : undefined,
  }
}

function getPluginConfigHref(pluginId: string, tabId?: string): string {
  const params = new URLSearchParams({ plugin: pluginId })
  if (tabId) {
    params.set('tab', tabId)
  }
  return `/plugin-config?${params.toString()}`
}

function buildBasePluginShortcut(plugin: InstalledPlugin, t: TFunction): QuickShortcutDefinition {
  const pluginName = plugin.manifest.name || plugin.id
  return {
    id: getPluginShortcutId(plugin.id),
    category: 'plugin',
    label: t('home.pluginShortcuts.baseLabel', { plugin: pluginName }),
    description: t('home.pluginShortcuts.baseDescription', { plugin: pluginName }),
    icon: Puzzle,
    href: getPluginConfigHref(plugin.id),
  }
}

async function loadPluginTabShortcuts(
  plugin: InstalledPlugin,
  t: TFunction,
  selectedTabIds?: Set<string>
): Promise<QuickShortcutDefinition[]> {
  // 单个插件 schema 拉取失败不应中断整体快捷入口加载，按空处理
  let schema: PluginConfigSchema
  try {
    schema = await getPluginConfigSchema(plugin.id)
  } catch {
    return []
  }

  const pluginName = plugin.manifest.name || plugin.id
  const schemaTabs = schema.layout.type === 'tabs' ? schema.layout.tabs : []
  const tabs = selectedTabIds ? schemaTabs.filter((tab) => selectedTabIds.has(tab.id)) : schemaTabs
  return tabs.map((tab) => ({
    id: getPluginShortcutId(plugin.id, tab.id),
    category: 'plugin' as const,
    label: `${pluginName} / ${tab.title || tab.id}`,
    description: t('home.pluginShortcuts.tabDescription', {
      plugin: pluginName,
      tab: tab.title || tab.id,
    }),
    icon: Puzzle,
    href: getPluginConfigHref(plugin.id, tab.id),
  }))
}

function getSelectedPluginTabIds(ids: string[]): Map<string, Set<string>> {
  const selectedTabs = new Map<string, Set<string>>()
  for (const id of ids) {
    const parsed = parsePluginShortcutId(id)
    if (!parsed?.tabId) {
      continue
    }

    const pluginTabs = selectedTabs.get(parsed.pluginId) ?? new Set<string>()
    pluginTabs.add(parsed.tabId)
    selectedTabs.set(parsed.pluginId, pluginTabs)
  }
  return selectedTabs
}

function getSelectedPluginIds(ids: string[]): Set<string> {
  const selectedPluginIds = new Set<string>()
  for (const id of ids) {
    const parsed = parsePluginShortcutId(id)
    if (parsed) {
      selectedPluginIds.add(parsed.pluginId)
    }
  }
  return selectedPluginIds
}

function getFallbackPluginShortcut(id: string, t: TFunction): QuickShortcutDefinition | null {
  const parsed = parsePluginShortcutId(id)
  if (!parsed) {
    return null
  }

  return {
    id,
    category: 'plugin',
    label: parsed.tabId
      ? t('home.pluginShortcuts.fallbackTabLabel', { plugin: parsed.pluginId, tab: parsed.tabId })
      : t('home.pluginShortcuts.fallbackLabel', { plugin: parsed.pluginId }),
    description: parsed.tabId
      ? t('home.pluginShortcuts.fallbackTabDescription')
      : t('home.pluginShortcuts.fallbackDescription'),
    icon: Puzzle,
    href: getPluginConfigHref(parsed.pluginId, parsed.tabId),
  }
}

interface UseQuickShortcutsParams {
  isRestarting: boolean
  handleRestart: () => void | Promise<void>
  uncheckedCount: number
  onOpenReviewer: () => void
}

export function useQuickShortcuts({
  isRestarting,
  handleRestart,
  uncheckedCount,
  onOpenReviewer,
}: UseQuickShortcutsParams) {
  const { t } = useTranslation()
  const [quickShortcutIds, setQuickShortcutIds] = useState<string[]>(loadQuickShortcutIds)
  const [quickShortcutDialogOpen, setQuickShortcutDialogOpen] = useState(false)
  const [quickShortcutSearch, setQuickShortcutSearch] = useState('')
  const [pluginShortcuts, setPluginShortcuts] = useState<QuickShortcutDefinition[]>([])
  const [isPluginShortcutsLoading, setIsPluginShortcutsLoading] = useState(false)

  useEffect(() => {
    let cancelled = false

    const loadPluginShortcuts = async () => {
      const selectedPluginIds = getSelectedPluginIds(quickShortcutIds)
      const selectedPluginTabIds = getSelectedPluginTabIds(quickShortcutIds)
      if (!quickShortcutDialogOpen && selectedPluginIds.size === 0) {
        setPluginShortcuts([])
        setIsPluginShortcutsLoading(false)
        return
      }

      setIsPluginShortcutsLoading(true)
      try {
        const installed = await getInstalledPlugins()
        if (cancelled) {
          return
        }

        const enabledPlugins = installed
          .filter((plugin) => plugin.disabled !== true && plugin.enabled !== false)
          .filter((plugin, index, all) => index === all.findIndex((item) => item.id === plugin.id))

        const visiblePlugins = quickShortcutDialogOpen
          ? enabledPlugins
          : enabledPlugins.filter((plugin) => selectedPluginIds.has(plugin.id))
        const baseShortcuts = visiblePlugins.map((plugin) => buildBasePluginShortcut(plugin, t))
        if (!cancelled) {
          setPluginShortcuts(baseShortcuts)
        }

        if (selectedPluginTabIds.size === 0) {
          return
        }

        const enabledPluginMap = new Map(enabledPlugins.map((plugin) => [plugin.id, plugin]))
        const tabShortcuts = (
          await Promise.all(
            Array.from(selectedPluginTabIds.entries()).map(async ([pluginId, selectedTabIds]) => {
              const plugin = enabledPluginMap.get(pluginId)
              if (!plugin) {
                return []
              }

              try {
                return await loadPluginTabShortcuts(plugin, t, selectedTabIds)
              } catch (error) {
                console.warn(`加载插件 ${plugin.id} 已选配置页签快捷入口失败:`, error)
                return []
              }
            })
          )
        ).flat()

        if (!cancelled) {
          setPluginShortcuts([...baseShortcuts, ...tabShortcuts])
        }
      } catch (error) {
        console.error('加载插件快捷入口失败:', error)
      } finally {
        if (!cancelled) {
          setIsPluginShortcutsLoading(false)
        }
      }
    }

    void loadPluginShortcuts()

    return () => {
      cancelled = true
    }
  }, [quickShortcutDialogOpen, quickShortcutIds, t])

  const quickShortcutOptions = useMemo<QuickShortcutDefinition[]>(
    () => [
      {
        id: 'action:restart',
        category: 'system',
        label: isRestarting ? t('home.quickActions.restarting') : t('home.quickActions.restart'),
        description: t('home.quickActions.descriptions.restart'),
        icon: RotateCcw,
        action: handleRestart,
        disabled: isRestarting,
      },
      {
        id: 'action:expression-review',
        category: 'resource',
        label: t('home.quickActions.expressionReview'),
        description: t('home.quickActions.descriptions.expressionReview'),
        icon: ClipboardCheck,
        action: onOpenReviewer,
        badge: uncheckedCount > 0 ? (uncheckedCount > 99 ? '99+' : String(uncheckedCount)) : undefined,
      },
      {
        id: 'route:logs',
        category: 'monitor',
        label: t('home.quickActions.viewLogs'),
        description: t('home.quickActions.descriptions.viewLogs'),
        icon: FileText,
        href: '/logs',
      },
      {
        id: 'route:plugin-market',
        category: 'plugin',
        label: t('home.quickActions.pluginManage'),
        description: t('home.quickActions.descriptions.pluginManage'),
        icon: Puzzle,
        href: '/plugins',
      },
      {
        id: 'route:plugin-config',
        category: 'plugin',
        label: t('home.quickActions.pluginConfig'),
        description: t('home.quickActions.descriptions.pluginConfig'),
        icon: Settings,
        href: '/plugin-config',
      },
      {
        id: 'route:settings',
        category: 'system',
        label: t('home.quickActions.systemSettings'),
        description: t('home.quickActions.descriptions.systemSettings'),
        icon: Settings,
        href: '/settings',
      },
      {
        id: 'route:settings-appearance',
        category: 'system',
        label: t('home.quickActions.appearanceSettings'),
        description: t('home.quickActions.descriptions.appearanceSettings'),
        icon: Settings,
        href: '/settings?tab=appearance',
      },
      {
        id: 'route:settings-local-cache',
        category: 'system',
        label: t('home.quickActions.localCache'),
        description: t('home.quickActions.descriptions.localCache'),
        icon: HardDrive,
        href: '/settings?tab=local-cache',
      },
      {
        id: 'route:model-providers',
        category: 'config',
        label: t('home.quickActions.modelProviders'),
        description: t('home.quickActions.descriptions.modelProviders'),
        icon: Settings,
        href: '/config/model?tab=providers',
      },
      {
        id: 'route:model-list',
        category: 'config',
        label: t('home.quickActions.modelList'),
        description: t('home.quickActions.descriptions.modelList'),
        icon: Settings,
        href: '/config/model?tab=models',
      },
      {
        id: 'route:model-tasks',
        category: 'config',
        label: t('home.quickActions.modelTasks'),
        description: t('home.quickActions.descriptions.modelTasks'),
        icon: Settings,
        href: '/config/model?tab=tasks',
      },
      {
        id: 'route:bot-config',
        category: 'config',
        label: t('home.quickActions.botConfig'),
        description: t('home.quickActions.descriptions.botConfig'),
        icon: Settings,
        href: '/config/bot',
      },
      {
        id: 'route:emoji',
        category: 'resource',
        label: t('home.quickActions.emojiManagement'),
        description: t('home.quickActions.descriptions.emojiManagement'),
        icon: MessageSquare,
        href: '/resource/emoji',
      },
      {
        id: 'route:expression',
        category: 'resource',
        label: t('home.quickActions.expressionManagement'),
        description: t('home.quickActions.descriptions.expressionManagement'),
        icon: MessageSquare,
        href: '/resource/expression',
      },
      {
        id: 'external:statistics',
        category: 'external',
        label: t('home.quickActions.statistics'),
        description: t('home.quickActions.descriptions.statistics'),
        icon: BarChart3,
        href: '/maibot_statistics.html',
        external: true,
      },
      ...pluginShortcuts,
    ],
    [handleRestart, isRestarting, onOpenReviewer, pluginShortcuts, t, uncheckedCount]
  )

  const quickShortcutMap = useMemo(
    () => new Map(quickShortcutOptions.map((shortcut) => [shortcut.id, shortcut])),
    [quickShortcutOptions]
  )

  const selectedQuickShortcuts = useMemo(
    () =>
      quickShortcutIds
        .map((id) => quickShortcutMap.get(id) ?? getFallbackPluginShortcut(id, t))
        .filter((shortcut): shortcut is QuickShortcutDefinition => Boolean(shortcut)),
    [quickShortcutIds, quickShortcutMap, t]
  )

  const filteredQuickShortcutOptions = useMemo(() => {
    const query = quickShortcutSearch.trim().toLowerCase()
    if (!query) {
      return quickShortcutOptions
    }

    return quickShortcutOptions.filter((shortcut) =>
      `${shortcut.label} ${shortcut.description}`.toLowerCase().includes(query)
    )
  }, [quickShortcutOptions, quickShortcutSearch])

  const updateQuickShortcutIds = useCallback((nextIds: string[]) => {
    const normalizedIds = Array.from(new Set(nextIds))
    setQuickShortcutIds(normalizedIds)
    saveQuickShortcutIds(normalizedIds)
  }, [])

  const toggleQuickShortcut = useCallback(
    (id: string, checked: boolean) => {
      updateQuickShortcutIds(
        checked ? [...quickShortcutIds, id] : quickShortcutIds.filter((shortcutId) => shortcutId !== id)
      )
    },
    [quickShortcutIds, updateQuickShortcutIds]
  )

  const resetQuickShortcuts = useCallback(() => {
    updateQuickShortcutIds([...DEFAULT_QUICK_SHORTCUT_IDS])
  }, [updateQuickShortcutIds])

  return {
    quickShortcutIds,
    quickShortcutDialogOpen,
    setQuickShortcutDialogOpen,
    quickShortcutSearch,
    setQuickShortcutSearch,
    isPluginShortcutsLoading,
    selectedQuickShortcuts,
    filteredQuickShortcutOptions,
    toggleQuickShortcut,
    resetQuickShortcuts,
  }
}
