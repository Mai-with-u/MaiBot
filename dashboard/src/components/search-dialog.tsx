import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { FileText, Search, SlidersHorizontal } from 'lucide-react'
import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import type { LucideProps } from 'lucide-react'

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ShortcutKbd } from '@/components/ui/kbd'
import { useMenuSections } from '@/components/layout/use-menu-sections'
import { registeredRoutePaths } from '@/router'
import { getBotConfigSchema, getModelConfigSchema } from '@/lib/config-api'
import { getAllLocalizedText, resolveFieldLabel } from '@/lib/config-label'
import { cn } from '@/lib/utils'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

interface SearchDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

interface SearchItem {
  id: string
  icon: React.ComponentType<LucideProps>
  title: string
  description: string
  path: string
  category: string
  keywords: string
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

function getModelConfigPath(_fieldPath: string) {
  return '/config/model'
}

function buildFieldSearchText(field: FieldSchema, fieldPath: string, sectionTitle: string, language?: string) {
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
  language?: string,
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
        icon: sourceLabel === '模型配置' ? SlidersHorizontal : FileText,
        title: fieldTitle,
        description: `${sourceLabel} / ${nextTrail.join(' / ')} / ${fullPath} · ${description}`,
        path: route,
        category: '配置项',
        keywords: buildFieldSearchText(field, fullPath, nextTrail.join(' / '), language),
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
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const { i18n, t } = useTranslation()
  const menuSections = useMenuSections()

  useEffect(() => {
    setConfigSearchItems([])
  }, [i18n.language])

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
          nextItems.push(...collectConfigFields(
            botSchema,
            'Bot 配置',
            '',
            () => '/config/bot',
            i18n.language,
          ))
        }
      }
      if (modelSchemaResult.status === 'fulfilled') {
        const modelSchema = unwrapConfigSchema(modelSchemaResult.value)
        if (modelSchema) {
          nextItems.push(...collectConfigFields(
            modelSchema,
            '模型配置',
            '',
            getModelConfigPath,
            i18n.language,
          ))
        }
      }

      setConfigSearchItems(nextItems)
    }

    loadConfigSearchItems().catch(() => {
      if (!cancelled) {
        setConfigSearchItems([])
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

  // 过滤搜索结果
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const filteredItems = (normalizedQuery ? [...searchItems, ...configSearchItems] : searchItems)
    .filter((item) => item.keywords.toLowerCase().includes(normalizedQuery))
    .slice(0, 80)

  // 导航到页面
  const handleNavigate = useCallback((path: string) => {
    navigate({ to: path })
    onOpenChange(false)
    // 在导航后重置状态
    setSearchQuery('')
    setSelectedIndex(0)
  }, [navigate, onOpenChange])

  // 键盘导航
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        if (filteredItems.length === 0) return
        setSelectedIndex((prev) => (prev + 1) % filteredItems.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        if (filteredItems.length === 0) return
        setSelectedIndex((prev) => (prev - 1 + filteredItems.length) % filteredItems.length)
      } else if (e.key === 'Enter' && filteredItems[selectedIndex]) {
        e.preventDefault()
        handleNavigate(filteredItems[selectedIndex].path)
      }
    },
    [filteredItems, selectedIndex, handleNavigate]
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl p-0 gap-0" confirmOnEnter>
        <DialogHeader className="px-4 pt-4 pb-0">
          <DialogTitle className="sr-only">{t('search.title')}</DialogTitle>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
            <Input
              ref={inputRef}
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value)
                setSelectedIndex(0)
              }}
              onKeyDown={handleKeyDown}
              placeholder={t('search.placeholder')}
              className="h-12 pl-11 text-base border-0 focus-visible:ring-0 shadow-none"
            />
          </div>
        </DialogHeader>

        <div className="border-t">
          <DialogBody className="h-100" viewportClassName="px-0">
            {filteredItems.length > 0 ? (
              <div className="space-y-1.5 p-2">
                {filteredItems.map((item, index) => {
                  const Icon = item.icon
                  return (
                    <button
                      key={item.id}
                      onClick={() => handleNavigate(item.path)}
                      onMouseEnter={() => setSelectedIndex(index)}
                      className={cn(
                        'w-full flex items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors',
                        index === selectedIndex
                          ? 'bg-accent text-accent-foreground'
                          : 'hover:bg-accent/50'
                      )}
                    >
                      <Icon className="h-5 w-5 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{item.title}</div>
                        <div className="truncate text-xs text-muted-foreground">
                          {item.description}
                        </div>
                      </div>
                      <div className="max-w-28 shrink-0 truncate rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
                        {item.category}
                      </div>
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <Search className="h-12 w-12 text-muted-foreground/50 mb-4" />
                <p className="text-sm text-muted-foreground">
                  {searchQuery ? t('search.noResults') : t('search.startSearch')}
                </p>
              </div>
            )}
          </DialogBody>
        </div>

        <div className="border-t px-4 py-3 flex items-center justify-between text-xs text-muted-foreground">
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
