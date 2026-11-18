import { useEffect, useMemo, useState } from 'react'
import { FileText, SlidersHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useMenuSections } from '@/components/layout/use-menu-sections'
import type { MenuIcon } from '@/components/layout/types'
import { createStreamlineIcon } from '@/components/ui/streamline-menu-icon'
import { getBotConfigSchema, getModelConfigSchema } from '@/lib/config-api'
import { getAllLocalizedText, resolveFieldLabel } from '@/lib/config-label'
import { registeredRoutePaths } from '@/router'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

const ConfigFileIcon = createStreamlineIcon('file-bookmark-solid', FileText)
const ModelConfigIcon = createStreamlineIcon('horizontal-slider-2-solid', SlidersHorizontal)

const configSources = [
  { load: getBotConfigSchema, label: 'Bot 配置', path: '/config/bot', icon: ConfigFileIcon },
  { load: getModelConfigSchema, label: '模型配置', path: '/config/model', icon: ModelConfigIcon },
]

export interface SearchItem {
  id: string
  icon: MenuIcon
  title: string
  description: string
  path: string
  category: string
  keywords: string
  fieldPath?: string
  schemaDefault?: boolean | number | string
}

function getSearchRank(item: SearchItem, query: string): number {
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
  source: (typeof configSources)[number],
  language: string
): SearchItem[] {
  const items: SearchItem[] = []
  const { icon, label, path } = source

  const walk = (currentSchema: ConfigSchema, pathPrefix: string, sectionTrail: string[]) => {
    const nextTrail = [...sectionTrail, resolveSchemaTitle(currentSchema, label)]

    for (const field of currentSchema.fields) {
      const fieldPath = pathPrefix ? `${pathPrefix}.${field.name}` : field.name
      const nestedSchema = currentSchema.nested?.[field.name]
      const fieldTitle = resolveFieldLabel(field, language)
      const description = field.description || nextTrail.join(' / ') || fieldPath
      const value = field.default
      // 只把公开的标量默认值作为答疑证据，省略字符串密钥、对象和列表。
      const schemaDefault =
        typeof value === 'boolean' ||
        typeof value === 'number' ||
        (typeof value === 'string' && field.type === 'select' && field.options?.includes(value))
          ? value
          : undefined

      items.push({
        id: `config:${label}:${fieldPath}`,
        icon,
        title: fieldTitle,
        description: `${label} / ${nextTrail.join(' / ')} / ${fieldPath} · ${description}`,
        path,
        category: '配置项',
        keywords: buildFieldSearchText(field, fieldPath, nextTrail.join(' / '), language),
        fieldPath,
        schemaDefault,
      })

      if (nestedSchema) {
        walk(nestedSchema, fieldPath, nextTrail)
      }
    }
  }

  walk(schema, '', [])
  return items
}

export function matchSearchItems(items: SearchItem[], query: string, limit: number): SearchItem[] {
  if (!query) return items.slice(0, limit)

  return items
    .filter((item) => item.keywords.toLowerCase().includes(query))
    .map((item) => ({ item, rank: getSearchRank(item, query) }))
    .sort(
      (left, right) => left.rank - right.rank || left.item.title.localeCompare(right.item.title)
    )
    .slice(0, limit)
    .map(({ item }) => item)
}

const emptyIndex: SearchItem[] = []

export function useSearchIndex(open: boolean) {
  const {
    i18n: { language },
    t,
  } = useTranslation()
  const menuSections = useMenuSections()
  const [configIndex, setConfigIndex] = useState<{ language: string; items: SearchItem[] }>()
  const [configIndexLoading, setConfigIndexLoading] = useState(false)
  const configIndexReady = configIndex?.language === language
  const configSearchItems = configIndexReady ? configIndex.items : emptyIndex
  const configItemCount = configSearchItems.length

  useEffect(() => {
    if (!open || configItemCount > 0) {
      return
    }

    let cancelled = false

    const loadConfigSearchItems = async () => {
      setConfigIndexLoading(true)
      // 两个 schema 独立加载，一个失败仍可索引另一个的字段。
      const results = await Promise.allSettled(
        configSources.map(async (source) => {
          const schema = unwrapConfigSchema(await source.load())
          return schema ? collectConfigFields(schema, source, language) : []
        })
      )
      if (cancelled) return

      const items = results.flatMap((result) => (result.status === 'fulfilled' ? result.value : []))
      setConfigIndex({ language, items })
      setConfigIndexLoading(false)
    }

    void loadConfigSearchItems()

    return () => {
      cancelled = true
    }
  }, [configItemCount, language, open])

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

  return { searchItems, configSearchItems, configIndexLoading, configIndexReady }
}
