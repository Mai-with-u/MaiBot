import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Clock, FileText, Loader2, Search, SlidersHorizontal, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ShortcutKbd } from '@/components/ui/kbd'
import { StreamlineIcon } from '@/components/ui/streamline-icon'
import { createStreamlineIcon } from '@/components/ui/streamline-menu-icon'
import { useMenuSections } from '@/components/layout/use-menu-sections'
import type { MenuIcon } from '@/components/layout/types'
import { registeredRoutePaths } from '@/router'
import { searchWithAI } from '@/lib/ai-search-api'
import { getBotConfigSchema, getModelConfigSchema } from '@/lib/config-api'
import { getAllLocalizedText, resolveFieldLabel } from '@/lib/config-label'
import { buildSearchNavigationPath } from '@/lib/config-search-navigation'
import { cn } from '@/lib/utils'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

const ConfigFileIcon = createStreamlineIcon('file-bookmark-solid', FileText)
const ModelConfigIcon = createStreamlineIcon('horizontal-slider-2-solid', SlidersHorizontal)
const RecentIcon: MenuIcon = Clock
const RECENT_SEARCH_ROUTES_KEY = 'maibot-search-recent-routes'
const MAX_RECENT_SEARCH_ROUTES = 8

interface SearchDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

interface SearchItem {
  id: string
  icon: MenuIcon
  title: string
  description: string
  path: string
  category: string
  keywords: string
  fieldPath?: string
}

interface AISearchItem {
  item: SearchItem
  reason: string
  score: number
}

function loadRecentSearchRoutes(): string[] {
  if (typeof window === 'undefined') {
    return []
  }

  const stored = localStorage.getItem(RECENT_SEARCH_ROUTES_KEY)
  if (!stored) {
    return []
  }

  try {
    const parsed = JSON.parse(stored)
    return Array.isArray(parsed)
      ? parsed
          .filter((path): path is string => typeof path === 'string')
          .slice(0, MAX_RECENT_SEARCH_ROUTES)
      : []
  } catch {
    return []
  }
}

function saveRecentSearchRoutes(paths: string[]): void {
  localStorage.setItem(
    RECENT_SEARCH_ROUTES_KEY,
    JSON.stringify(paths.slice(0, MAX_RECENT_SEARCH_ROUTES))
  )
}

function getSearchRank(item: SearchItem, query: string): number {
  if (!query) {
    return 0
  }

  const title = item.title.toLowerCase()
  const category = item.category.toLowerCase()
  const path = item.path.toLowerCase()
  if (title === query || path === query) return 0
  if (title.startsWith(query)) return 1
  if (category.includes(query)) return 2
  if (path.includes(query)) return 3
  return 4
}

function resolveSchemaTitle(schema: ConfigSchema, fallback: string) {
  return schema.uiLabel || schema.classDoc || schema.className || fallback
}

function unwrapConfigSchema(payload: unknown): ConfigSchema | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  if ('fields' in payload) {
    return payload as ConfigSchema
  }

  if ('schema' in payload) {
    const schema = (payload as { schema?: unknown }).schema
    if (schema && typeof schema === 'object' && 'fields' in schema) {
      return schema as ConfigSchema
    }
  }

  return null
}

function getModelConfigPath() {
  return '/config/model'
}

function buildFieldSearchText(
  field: FieldSchema,
  fieldPath: string,
  sectionTitle: string,
  language?: string
) {
  const options = field.options?.join(' ') ?? ''
  const optionDescriptions = field['x-option-descriptions']
    ? Object.entries(field['x-option-descriptions'])
        .map(([key, value]) => `${key} ${value}`)
        .join(' ')
    : ''

  return [
    resolveFieldLabel(field, language),
    ...getAllLocalizedText(field.label),
    field.name,
    fieldPath,
    field.description,
    sectionTitle,
    field.type,
    options,
    optionDescriptions,
  ].join(' ')
}

function collectConfigFields(
  schema: ConfigSchema,
  sourceLabel: string,
  basePath: string,
  routePath: (fieldPath: string) => string,
  language?: string
): SearchItem[] {
  const items: SearchItem[] = []

  const walk = (currentSchema: ConfigSchema, pathPrefix: string, sectionTrail: string[]) => {
    const sectionTitle = resolveSchemaTitle(currentSchema, sourceLabel)
    const nextTrail = [...sectionTrail, sectionTitle].filter(Boolean)

    for (const field of currentSchema.fields) {
      const fieldPath = pathPrefix ? `${pathPrefix}.${field.name}` : field.name
      const nestedSchema = currentSchema.nested?.[field.name]
      const fieldTitle = resolveFieldLabel(field, language)
      const description = field.description || nextTrail.join(' / ') || fieldPath
      const fullPath = basePath ? `${basePath}.${fieldPath}` : fieldPath
      const route = routePath(fullPath)

      items.push({
        id: `config:${sourceLabel}:${fullPath}`,
        icon: sourceLabel === '模型配置' ? ModelConfigIcon : ConfigFileIcon,
        title: fieldTitle,
        description: `${sourceLabel} / ${nextTrail.join(' / ')} / ${fullPath} · ${description}`,
        path: route,
        category: '配置项',
        keywords: buildFieldSearchText(field, fullPath, nextTrail.join(' / '), language),
        fieldPath: fullPath,
      })

      if (nestedSchema) {
        walk(nestedSchema, fieldPath, nextTrail)
      }
    }
  }

  walk(schema, '', [])
  return items
}

export function SearchDialog({ open, onOpenChange }: SearchDialogProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [configSearchItems, setConfigSearchItems] = useState<SearchItem[]>([])
  const [configIndexLoading, setConfigIndexLoading] = useState(false)
  const [recentSearchRoutes, setRecentSearchRoutes] = useState<string[]>(loadRecentSearchRoutes)
  const [aiSearchItems, setAISearchItems] = useState<AISearchItem[]>([])
  const [aiExpandedTerms, setAIExpandedTerms] = useState<string[]>([])
  const [aiSearchLoading, setAISearchLoading] = useState(false)
  const [aiSearchError, setAISearchError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const aiSearchAbortRef = useRef<AbortController | null>(null)
  const navigate = useNavigate()
  const { i18n, t } = useTranslation()
  const menuSections = useMenuSections()

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      setConfigSearchItems([])
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [i18n.language])

  useEffect(() => {
    return () => aiSearchAbortRef.current?.abort()
  }, [])

  useEffect(() => {
    if (!open) {
      return
    }

    const frameId = window.requestAnimationFrame(() => {
      inputRef.current?.focus()
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [open])

  useEffect(() => {
    if (!open || configSearchItems.length > 0) {
      return
    }

    let cancelled = false

    const loadConfigSearchItems = async () => {
      setConfigIndexLoading(true)
      // 用 allSettled：任一 schema 失败仍以另一 schema 建立搜索索引（保留原 best-effort 行为）
      const [botSchemaResult, modelSchemaResult] = await Promise.allSettled([
        getBotConfigSchema(),
        getModelConfigSchema(),
      ])

      if (cancelled) {
        return
      }

      const nextItems: SearchItem[] = []
      if (botSchemaResult.status === 'fulfilled') {
        const botSchema = unwrapConfigSchema(botSchemaResult.value)
        if (botSchema) {
          nextItems.push(
            ...collectConfigFields(botSchema, 'Bot 配置', '', () => '/config/bot', i18n.language)
          )
        }
      }
      if (modelSchemaResult.status === 'fulfilled') {
        const modelSchema = unwrapConfigSchema(modelSchemaResult.value)
        if (modelSchema) {
          nextItems.push(
            ...collectConfigFields(modelSchema, '模型配置', '', getModelConfigPath, i18n.language)
          )
        }
      }

      setConfigSearchItems(nextItems)
      setConfigIndexLoading(false)
    }

    loadConfigSearchItems().catch(() => {
      if (!cancelled) {
        setConfigSearchItems([])
        setConfigIndexLoading(false)
      }
    })

    return () => {
      cancelled = true
    }
  }, [configSearchItems.length, i18n.language, open])

  const searchItems: SearchItem[] = useMemo(
    () =>
      menuSections.flatMap((section) =>
        section.items
          .filter((item) => registeredRoutePaths.has(item.path))
          .map((item) => ({
            id: `route:${item.path}`,
            icon: item.icon,
            title: t(item.label),
            description: item.searchDescription ? t(item.searchDescription) : item.path,
            path: item.path,
            category: t(section.title),
            keywords: [
              t(item.label),
              item.path,
              item.searchDescription ? t(item.searchDescription) : '',
              t(section.title),
            ].join(' '),
          }))
      ),
    [menuSections, t]
  )

  const searchItemMap = useMemo(
    () => new Map(searchItems.map((item) => [item.path, item])),
    [searchItems]
  )

  const recentSearchItems = useMemo<SearchItem[]>(
    () =>
      recentSearchRoutes
        .map<SearchItem | null>((path) => {
          const item = searchItemMap.get(path)
          if (!item) {
            return null
          }

          return {
            ...item,
            id: `recent:${item.path}`,
            icon: RecentIcon,
            category: t('search.recent'),
          }
        })
        .filter((item): item is SearchItem => item !== null),
    [recentSearchRoutes, searchItemMap, t]
  )

  // 过滤搜索结果
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const candidateItems = normalizedQuery
    ? [...searchItems, ...configSearchItems]
    : [...recentSearchItems, ...searchItems].filter(
        (item, index, all) => all.findIndex((candidate) => candidate.path === item.path) === index
      )
  const filteredItems = candidateItems
    .filter((item) => item.keywords.toLowerCase().includes(normalizedQuery))
    .sort((left, right) => {
      if (!normalizedQuery) {
        return 0
      }

      const rankDiff = getSearchRank(left, normalizedQuery) - getSearchRank(right, normalizedQuery)
      return rankDiff === 0 ? left.title.localeCompare(right.title) : rankDiff
    })
    .slice(0, 80)

  const aiMatchedIds = new Set(aiSearchItems.map(({ item }) => item.id))
  const visibleItems: SearchItem[] = [
    ...aiSearchItems.map(({ item, reason }) => ({
      ...item,
      description: reason || item.description,
      category: t('search.aiRecommendation'),
    })),
    ...filteredItems.filter((item) => !aiMatchedIds.has(item.id)),
  ].slice(0, 80)

  const runAISearch = useCallback(async () => {
    const query = searchQuery.trim()
    if (!query || configIndexLoading) {
      return
    }

    aiSearchAbortRef.current?.abort()
    const abortController = new AbortController()
    aiSearchAbortRef.current = abortController
    setAISearchLoading(true)
    setAISearchError('')
    setAISearchItems([])
    setAIExpandedTerms([])

    const aiCandidates = [...searchItems, ...configSearchItems]
      .slice(0, 600)
      .map((item, index) => ({
        id: `c${index}`,
        item,
      }))
    const itemMap = new Map(aiCandidates.map(({ id, item }) => [id, item]))

    try {
      const response = await searchWithAI(
        {
          query,
          language: i18n.language,
          candidates: aiCandidates.map(({ id, item }) => ({
            id,
            title: item.title.slice(0, 120),
            description: item.description.slice(0, 240),
            category: item.category.slice(0, 80),
          })),
        },
        abortController.signal
      )

      if (abortController.signal.aborted) {
        return
      }

      const nextItems = response.results
        .map<AISearchItem | null>((result) => {
          const item = itemMap.get(result.id)
          return item
            ? {
                item,
                reason: result.reason,
                score: result.score,
              }
            : null
        })
        .filter((item): item is AISearchItem => item !== null)

      setAISearchItems(nextItems)
      setAIExpandedTerms(response.expanded_terms)
      if (nextItems.length === 0) {
        setAISearchError(t('search.aiNoResults'))
      }
      setSelectedIndex(0)
    } catch (error) {
      if (!abortController.signal.aborted) {
        setAISearchError(error instanceof Error ? error.message : t('search.aiFailed'))
      }
    } finally {
      if (!abortController.signal.aborted) {
        setAISearchLoading(false)
      }
    }
  }, [configIndexLoading, configSearchItems, i18n.language, searchItems, searchQuery, t])

  // 导航到页面
  const handleNavigate = useCallback(
    (item: SearchItem) => {
      const nextRoutes = [
        item.path,
        ...recentSearchRoutes.filter((recentPath) => recentPath !== item.path),
      ].slice(0, MAX_RECENT_SEARCH_ROUTES)
      setRecentSearchRoutes(nextRoutes)
      saveRecentSearchRoutes(nextRoutes)
      navigate({ to: buildSearchNavigationPath(item.path, item.fieldPath) })
      onOpenChange(false)
      aiSearchAbortRef.current?.abort()
      // 在导航后重置状态
      setSearchQuery('')
      setSelectedIndex(0)
      setAISearchItems([])
      setAIExpandedTerms([])
      setAISearchError('')
    },
    [navigate, onOpenChange, recentSearchRoutes]
  )

  // 键盘导航
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        void runAISearch()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        if (visibleItems.length === 0) return
        setSelectedIndex((prev) => (prev + 1) % visibleItems.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        if (visibleItems.length === 0) return
        setSelectedIndex((prev) => (prev - 1 + visibleItems.length) % visibleItems.length)
      } else if (e.key === 'Home') {
        e.preventDefault()
        setSelectedIndex(0)
      } else if (e.key === 'End') {
        e.preventDefault()
        setSelectedIndex(Math.max(0, visibleItems.length - 1))
      } else if (e.key === 'Enter' && visibleItems[selectedIndex]) {
        e.preventDefault()
        handleNavigate(visibleItems[selectedIndex])
      }
    },
    [handleNavigate, runAISearch, selectedIndex, visibleItems]
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl gap-0 p-0" confirmOnEnter>
        <DialogHeader className="px-4 pt-4 pb-0">
          <DialogTitle className="sr-only">{t('search.title')}</DialogTitle>
          <div className="relative">
            <StreamlineIcon
              name="search-bar-solid"
              fallback={Search}
              className="text-muted-foreground absolute top-1/2 left-3 h-5 w-5 -translate-y-1/2"
            />
            <Input
              ref={inputRef}
              value={searchQuery}
              onChange={(e) => {
                aiSearchAbortRef.current?.abort()
                setSearchQuery(e.target.value)
                setSelectedIndex(0)
                setAISearchItems([])
                setAIExpandedTerms([])
                setAISearchError('')
                setAISearchLoading(false)
              }}
              onKeyDown={handleKeyDown}
              placeholder={t('search.placeholder')}
              className="h-12 border-0 pl-11 text-base shadow-none focus-visible:ring-0"
            />
          </div>
          {normalizedQuery && (
            <div className="flex min-h-10 flex-wrap items-center gap-2 border-t px-2 py-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 shrink-0 gap-1.5"
                disabled={aiSearchLoading || configIndexLoading}
                onClick={() => void runAISearch()}
              >
                {aiSearchLoading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                {aiSearchLoading ? t('search.aiSearching') : t('search.aiSearch')}
              </Button>
              <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
                {configIndexLoading
                  ? t('search.indexLoading')
                  : aiSearchError ||
                    (aiExpandedTerms.length > 0
                      ? t('search.aiUnderstood', { terms: aiExpandedTerms.join('、') })
                      : t('search.aiHint'))}
              </span>
              <span className="text-muted-foreground hidden text-xs sm:inline">
                {t('search.aiShortcut')}
              </span>
            </div>
          )}
        </DialogHeader>

        <div className="border-t">
          <DialogBody className="h-100" viewportClassName="px-0">
            {visibleItems.length > 0 ? (
              <div className="space-y-1.5 p-2">
                {visibleItems.map((item, index) => {
                  const Icon = item.icon
                  return (
                    <button
                      key={item.id}
                      onClick={() => handleNavigate(item)}
                      onMouseEnter={() => setSelectedIndex(index)}
                      title={`${item.title} · ${item.description} · ${item.path}`}
                      className={cn(
                        'flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors',
                        index === selectedIndex
                          ? 'bg-accent text-accent-foreground'
                          : 'hover:bg-accent/50'
                      )}
                    >
                      <Icon className="h-5 w-5 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{item.title}</div>
                        <div className="text-muted-foreground truncate text-xs">
                          {item.description}
                        </div>
                      </div>
                      <div className="bg-muted text-muted-foreground max-w-28 shrink-0 truncate rounded px-2 py-1 text-xs">
                        {item.category}
                      </div>
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <StreamlineIcon
                  name="search-bar-solid"
                  fallback={Search}
                  className="text-muted-foreground/50 mb-4 h-12 w-12"
                />
                <p className="text-muted-foreground text-sm">
                  {searchQuery ? t('search.noResults') : t('search.startSearch')}
                </p>
              </div>
            )}
          </DialogBody>
        </div>

        <div className="text-muted-foreground flex items-center justify-between border-t px-4 py-3 text-xs">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1">
              <ShortcutKbd size="sm" keys={['up']} />
              <ShortcutKbd size="sm" keys={['down']} />
              {t('search.navigate')}
            </span>
            <span className="flex items-center gap-1">
              <ShortcutKbd size="sm" keys={['enter']} />
              {t('search.select')}
            </span>
            <span className="flex items-center gap-1">
              <ShortcutKbd size="sm" keys={['esc']} />
              {t('search.close')}
            </span>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
