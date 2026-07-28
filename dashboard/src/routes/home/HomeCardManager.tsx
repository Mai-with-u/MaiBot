import type { CSSProperties, ReactNode } from 'react'
import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  rectSortingStrategy,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ExternalLink, GripVertical, Maximize2, Pencil, Plus, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import type { PluginHomeCard, PluginHomeCardContent, PluginHomeCardWidth } from '@/lib/plugin-api'
import { cn } from '@/lib/utils'

const HOME_CARD_LAYOUT_STORAGE_KEY = 'maibot-home-card-layout-v1'
const LEGACY_HITOKOTO_STYLE_STORAGE_KEY = 'maibot-home-hitokoto-style'
const HOME_CARD_LOW_ROW_HEIGHT = 192
const HOME_CARD_HIGH_ROW_HEIGHT = 360
const HOME_CARD_GRID_GAP = 16
const HOME_CARD_SEPARATOR_ROW_HEIGHT = 34
const LEGACY_BUILTIN_CARD_ORDERS = [
  [
    'builtin:version',
    'builtin:bot-status',
    'builtin:quick-actions',
    'builtin:stats-overview',
    'builtin:storage',
  ],
  ['builtin:bot-status', 'builtin:quick-actions', 'builtin:stats-overview', 'builtin:storage'],
]
const DEFAULT_BUILTIN_CARD_ORDER = [
  'builtin:bot-status',
  'builtin:quick-actions',
  'builtin:storage',
  'builtin:stats-overview',
]

type HomeCardSource = 'builtin' | 'plugin'
type HomeCardRowMode = 'low' | 'high'
type HomeCardCategory = 'status' | 'statistics' | 'analysis' | 'plugin'
type HomeCardStyle = 'default' | 'orange' | 'borderless'

export interface HomeCardDefinition {
  id: string
  title: string
  description?: string
  width?: PluginHomeCardWidth
  allowedWidths?: PluginHomeCardWidth[]
  preferredHeight?: HomeCardRowMode
  defaultHidden?: boolean
  category?: HomeCardCategory
  editLabel?: string
  onEdit?: () => void
  variant?: 'card' | 'separator'
  source: HomeCardSource
  render: () => ReactNode
}

interface HomeCardLayout {
  order: string[]
  hidden: string[]
  rowModes: Record<string, HomeCardRowMode>
  styles: Record<string, HomeCardStyle>
  widths: Record<string, PluginHomeCardWidth>
}

interface HomeCardManagerProps {
  cards: HomeCardDefinition[]
  pluginCards: PluginHomeCard[]
  controlsPortalId?: string
}

function migrateHomeCardOrder(order: string[]): string[] {
  const builtinOrder = order.filter((id) => id.startsWith('builtin:'))
  const isLegacyDefault = LEGACY_BUILTIN_CARD_ORDERS.some((legacyOrder) =>
    stringArraysEqual(builtinOrder, legacyOrder)
  )
  if (!isLegacyDefault) return order

  return [...DEFAULT_BUILTIN_CARD_ORDER, ...order.filter((id) => !id.startsWith('builtin:'))]
}

function loadHomeCardLayout(): HomeCardLayout {
  if (typeof window === 'undefined') {
    return { order: [], hidden: [], rowModes: {}, styles: {}, widths: {} }
  }

  try {
    const parsed = JSON.parse(localStorage.getItem(HOME_CARD_LAYOUT_STORAGE_KEY) || '{}')
    const order = Array.isArray(parsed.order)
      ? parsed.order.filter((item: unknown): item is string => typeof item === 'string')
      : []
    const styles = normalizeCardStyles(parsed.styles)
    if (
      styles['builtin:hitokoto'] === undefined &&
      localStorage.getItem(LEGACY_HITOKOTO_STYLE_STORAGE_KEY) === 'orange'
    ) {
      styles['builtin:hitokoto'] = 'orange'
    }
    return {
      order: migrateHomeCardOrder(order),
      hidden: Array.isArray(parsed.hidden)
        ? parsed.hidden.filter((item: unknown): item is string => typeof item === 'string')
        : [],
      rowModes: normalizeRowModes(parsed.rowModes),
      styles,
      widths: normalizeCardWidths(parsed.widths),
    }
  } catch {
    return { order: [], hidden: [], rowModes: {}, styles: {}, widths: {} }
  }
}

function saveHomeCardLayout(layout: HomeCardLayout): void {
  localStorage.setItem(HOME_CARD_LAYOUT_STORAGE_KEY, JSON.stringify(layout))
}

function sanitizeUrl(url: unknown): string {
  const value = String(url || '').trim()
  if (!value || value.startsWith('//')) return ''
  const lower = value.toLowerCase()
  if (
    value.startsWith('/') ||
    lower.startsWith('http://') ||
    lower.startsWith('https://') ||
    lower.startsWith('mailto:')
  ) {
    return value
  }
  return ''
}

function cardWidthClass(width: PluginHomeCardWidth | undefined): string {
  switch (width) {
    case 'small':
      return 'lg:col-span-2'
    case 'medium':
      return 'lg:col-span-3'
    case 'large':
      return 'lg:col-span-5'
    case 'wide':
      return 'lg:col-span-7'
    case 'full':
      return 'lg:col-span-10'
    default:
      return 'lg:col-span-3'
  }
}

function normalizeRowModes(value: unknown): Record<string, HomeCardRowMode> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.entries(value).reduce<Record<string, HomeCardRowMode>>((result, [key, mode]) => {
    if (/^\d+$/.test(key) && (mode === 'low' || mode === 'high')) {
      result[key] = mode
    }
    return result
  }, {})
}

function normalizeCardWidths(value: unknown): Record<string, PluginHomeCardWidth> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const validWidths = new Set<PluginHomeCardWidth>(['small', 'medium', 'large', 'wide', 'full'])
  return Object.entries(value).reduce<Record<string, PluginHomeCardWidth>>(
    (result, [key, width]) => {
      if (typeof width === 'string' && validWidths.has(width as PluginHomeCardWidth)) {
        result[key] = width as PluginHomeCardWidth
      }
      return result
    },
    {}
  )
}

function normalizeCardStyles(value: unknown): Record<string, HomeCardStyle> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const validStyles = new Set<HomeCardStyle>(['default', 'orange', 'borderless'])
  return Object.entries(value).reduce<Record<string, HomeCardStyle>>((result, [key, style]) => {
    if (typeof style === 'string' && validStyles.has(style as HomeCardStyle)) {
      result[key] = style as HomeCardStyle
    }
    return result
  }, {})
}

function rowModesEqual(
  left: Record<string, HomeCardRowMode>,
  right: Record<string, HomeCardRowMode>
): boolean {
  const leftKeys = Object.keys(left)
  const rightKeys = Object.keys(right)
  return leftKeys.length === rightKeys.length && leftKeys.every((key) => left[key] === right[key])
}

function defaultRowMode(rowIndex: number, cards: HomeCardDefinition[] = []): HomeCardRowMode {
  if (cards.some((card) => card.preferredHeight === 'high')) return 'high'
  if (cards.length > 0 && cards.every((card) => card.preferredHeight === 'low')) return 'low'
  return rowIndex === 0 ? 'low' : 'high'
}

function isSeparatorRow(cards: HomeCardDefinition[]): boolean {
  return cards.length === 1 && cards[0].variant === 'separator'
}

function rowHeight(mode: HomeCardRowMode, cards: HomeCardDefinition[]): number {
  if (isSeparatorRow(cards)) return HOME_CARD_SEPARATOR_ROW_HEIGHT
  return mode === 'high' ? HOME_CARD_HIGH_ROW_HEIGHT : HOME_CARD_LOW_ROW_HEIGHT
}

function cardWidthColumns(width: PluginHomeCardWidth | undefined): number {
  switch (width) {
    case 'small':
      return 2
    case 'medium':
      return 3
    case 'large':
      return 5
    case 'wide':
      return 7
    case 'full':
      return 10
    default:
      return 3
  }
}

function shrinkCardWidthOneStep(
  width: PluginHomeCardWidth | undefined
): PluginHomeCardWidth | undefined {
  switch (width) {
    case 'full':
      return 'wide'
    case 'wide':
      return 'large'
    case 'large':
      return 'medium'
    case 'medium':
      return 'small'
    case 'small':
    default:
      return width
  }
}

function buildAdaptiveCardWidths(
  cards: HomeCardDefinition[],
  widthOverrides: Record<string, PluginHomeCardWidth>
): Map<string, PluginHomeCardWidth | undefined> {
  const widths = new Map<string, PluginHomeCardWidth | undefined>()
  let currentRowColumns = 0

  for (const card of cards) {
    const preferredWidth = widthOverrides[card.id] ?? card.width
    const preferredColumns = cardWidthColumns(preferredWidth)
    const remainingColumns = 10 - currentRowColumns
    let renderedWidth: PluginHomeCardWidth | undefined = preferredWidth
    let renderedColumns = preferredColumns

    if (currentRowColumns > 0 && preferredColumns > remainingColumns) {
      const shrunkWidth = shrinkCardWidthOneStep(preferredWidth)
      const shrunkColumns = cardWidthColumns(shrunkWidth)
      if (shrunkColumns <= remainingColumns) {
        renderedWidth = shrunkWidth
        renderedColumns = shrunkColumns
      } else {
        currentRowColumns = 0
      }
    }

    widths.set(card.id, renderedWidth)
    currentRowColumns += renderedColumns
    if (currentRowColumns >= 10) {
      currentRowColumns = 0
    }
  }

  return widths
}

function buildCardRows(
  cards: HomeCardDefinition[],
  widths: Map<string, PluginHomeCardWidth | undefined>
): HomeCardDefinition[][] {
  const rows: HomeCardDefinition[][] = []
  let currentRow: HomeCardDefinition[] = []
  let currentRowColumns = 0

  for (const card of cards) {
    const columns = cardWidthColumns(widths.get(card.id) ?? card.width)
    if (currentRow.length > 0 && currentRowColumns + columns > 10) {
      rows.push(currentRow)
      currentRow = []
      currentRowColumns = 0
    }

    currentRow.push(card)
    currentRowColumns += columns
    if (currentRowColumns >= 10) {
      rows.push(currentRow)
      currentRow = []
      currentRowColumns = 0
    }
  }

  if (currentRow.length > 0) {
    rows.push(currentRow)
  }
  return rows
}

function HomeMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => sanitizeUrl(url)}
      components={{
        a({ children, href, ...props }) {
          const safeHref = sanitizeUrl(href)
          if (!safeHref) return <span>{children}</span>
          return (
            <a
              className="text-primary hover:underline"
              href={safeHref}
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            >
              {children}
            </a>
          )
        },
        p({ children }) {
          return <p className="my-1.5 leading-relaxed">{children}</p>
        },
        ul({ children }) {
          return <ul className="my-2 list-inside list-disc space-y-1">{children}</ul>
        },
        ol({ children }) {
          return <ol className="my-2 list-inside list-decimal space-y-1">{children}</ol>
        },
        code({ children }) {
          return <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">{children}</code>
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}
function getBlockText(block: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = block[key]
    if (typeof value === 'string' && value.trim()) {
      return value
    }
  }
  return ''
}

function renderContentBlock(block: Record<string, unknown>, index: number): ReactNode {
  const type = String(block.type || 'text')
  if (type === 'markdown') {
    return <HomeMarkdown key={index} content={getBlockText(block, ['content', 'text', 'value'])} />
  }
  if (type === 'stat') {
    return (
      <div key={index} className="bg-muted/20 rounded-md border px-3 py-2">
        <div className="text-muted-foreground text-xs">
          {getBlockText(block, ['label', 'title'])}
        </div>
        <div className="mt-1 text-xl font-bold">{getBlockText(block, ['value', 'content'])}</div>
        {getBlockText(block, ['detail', 'description']) && (
          <div className="text-muted-foreground mt-1 text-xs">
            {getBlockText(block, ['detail', 'description'])}
          </div>
        )}
      </div>
    )
  }
  if (type === 'key_value') {
    const entries =
      block.entries && typeof block.entries === 'object' && !Array.isArray(block.entries)
        ? Object.entries(block.entries as Record<string, unknown>)
        : []
    return (
      <div key={index} className="space-y-1.5">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-center justify-between gap-3 text-sm">
            <span className="text-muted-foreground">{key}</span>
            <span className="min-w-0 truncate font-medium">{String(value || '')}</span>
          </div>
        ))}
      </div>
    )
  }
  if (type === 'list' && Array.isArray(block.items)) {
    return (
      <ul key={index} className="list-inside list-disc space-y-1 text-sm">
        {block.items.map((item, itemIndex) => (
          <li key={itemIndex}>{String(item || '')}</li>
        ))}
      </ul>
    )
  }
  if (type === 'actions' && Array.isArray(block.actions)) {
    return (
      <div key={index} className="flex flex-wrap gap-2">
        {block.actions.map((item, itemIndex) => {
          if (!item || typeof item !== 'object') return null
          const action = item as Record<string, unknown>
          const href = sanitizeUrl(action.url || action.href)
          if (!href) return null
          return (
            <Button key={itemIndex} variant="outline" size="sm" asChild>
              <a
                href={href}
                target={href.startsWith('/') ? undefined : '_blank'}
                rel={href.startsWith('/') ? undefined : 'noopener noreferrer'}
              >
                {getBlockText(action, ['label', 'title']) || href}
              </a>
            </Button>
          )
        })}
      </div>
    )
  }
  return (
    <p key={index} className="text-sm leading-relaxed">
      {getBlockText(block, ['content', 'text', 'value'])}
    </p>
  )
}

function PluginHomeCardView({ card }: { card: PluginHomeCard }) {
  const href = sanitizeUrl(card.link_url)
  const content = renderPluginContent(card.content)
  const showTitle = card.show_title !== false

  return (
    <Card className="h-full">
      {showTitle && (
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <CardTitle className="truncate text-sm font-medium">{card.title}</CardTitle>
              {card.description && (
                <CardDescription className="line-clamp-2">{card.description}</CardDescription>
              )}
            </div>
            <Badge variant="outline" className="shrink-0 text-[10px]">
              插件
            </Badge>
          </div>
        </CardHeader>
      )}
      <CardContent className={cn('space-y-3 text-sm', !showTitle && 'pt-6')}>
        {content}
        {href && (
          <Button variant="outline" size="sm" asChild className="w-full justify-start gap-2">
            <a
              href={href}
              target={href.startsWith('/') ? undefined : '_blank'}
              rel={href.startsWith('/') ? undefined : 'noopener noreferrer'}
            >
              {card.link_label || '打开'}
              {!href.startsWith('/') && <ExternalLink className="h-3.5 w-3.5" />}
            </a>
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

function renderPluginContent(content: PluginHomeCardContent): ReactNode {
  if (typeof content === 'string') {
    return content.trim() ? (
      <HomeMarkdown content={content} />
    ) : (
      <p className="text-muted-foreground text-sm">暂无内容</p>
    )
  }
  if (Array.isArray(content)) {
    return <div className="space-y-3">{content.map(renderContentBlock)}</div>
  }
  if (content && typeof content === 'object') {
    return renderContentBlock(content, 0)
  }
  return <p className="text-muted-foreground text-sm">暂无内容</p>
}

function stringArraysEqual(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

function mergeCardOrder(currentOrder: string[], defaultOrder: string[]): string[] {
  const knownIds = new Set(defaultOrder)
  const order = currentOrder.filter((id) => knownIds.has(id))

  for (const id of defaultOrder) {
    if (order.includes(id)) continue
    const defaultIndex = defaultOrder.indexOf(id)
    const previousId = defaultOrder
      .slice(0, defaultIndex)
      .reverse()
      .find((candidate) => order.includes(candidate))
    if (previousId) {
      order.splice(order.indexOf(previousId) + 1, 0, id)
      continue
    }
    const nextId = defaultOrder
      .slice(defaultIndex + 1)
      .find((candidate) => order.includes(candidate))
    if (nextId) {
      order.splice(order.indexOf(nextId), 0, id)
    } else {
      order.push(id)
    }
  }
  return order
}

function SortableHomeCard({
  card,
  displayWidth,
  preferredWidth,
  cardStyle,
  editing,
  onEdit,
  onHide,
  onResize,
}: {
  card: HomeCardDefinition
  displayWidth?: PluginHomeCardWidth
  preferredWidth?: PluginHomeCardWidth
  cardStyle: HomeCardStyle
  editing: boolean
  onEdit: (id: string) => void
  onHide: (id: string) => void
  onResize: (id: string) => void
}) {
  const { t } = useTranslation()
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: card.id,
    disabled: !editing,
  })
  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
  }
  const editLabel = t('home.cards.editCard', { title: card.title })

  return (
    <div
      ref={setNodeRef}
      style={style}
      data-home-card-id={card.id}
      data-home-card-style={cardStyle}
      className={cn(
        'relative h-full min-w-0',
        card.variant === 'separator'
          ? 'lg:col-span-10'
          : cardWidthClass(displayWidth ?? card.width),
        isDragging && 'z-20 opacity-80'
      )}
      data-home-card-variant={card.variant ?? 'card'}
    >
      {editing && (
        <div
          data-home-card-edit-overlay="true"
          aria-hidden="true"
          className="border-primary/25 absolute inset-0 z-10 rounded-lg border bg-white/20 shadow-[inset_0_1px_0_rgba(255,255,255,0.38),inset_0_0_0_1px_rgba(255,255,255,0.12)] backdrop-blur-md backdrop-saturate-150 dark:bg-black/20"
          style={{
            WebkitBackdropFilter: 'blur(10px) saturate(140%)',
            backdropFilter: 'blur(10px) saturate(140%)',
          }}
        />
      )}
      {editing && (
        <div className="bg-background/95 absolute top-2 right-2 z-20 flex items-center gap-1 rounded-md border p-1 shadow-sm backdrop-blur">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 cursor-grab"
                aria-label={`拖拽排序：${card.title}`}
                {...attributes}
                {...listeners}
              >
                <GripVertical className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>拖拽排序</TooltipContent>
          </Tooltip>
          {card.allowedWidths && card.allowedWidths.length > 1 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  aria-label={`调整尺寸：${card.title}`}
                  onClick={() => onResize(card.id)}
                >
                  <Maximize2 className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {preferredWidth ? `调整尺寸（当前 ${preferredWidth}）` : '调整尺寸'}
              </TooltipContent>
            </Tooltip>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label={t('home.cards.editCard', { title: card.title })}
                onClick={() => onEdit(card.id)}
              >
                <Pencil className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{editLabel}</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                aria-label={`从首页隐藏：${card.title}`}
                onClick={() => onHide(card.id)}
              >
                <X className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>从首页隐藏</TooltipContent>
          </Tooltip>
        </div>
      )}
      <div
        aria-hidden={editing}
        className={cn(
          'h-full overflow-hidden transition-[filter,opacity] duration-150',
          editing && 'pointer-events-none opacity-75 blur-[2.5px] select-none'
        )}
        inert={editing}
      >
        {card.render()}
      </div>
    </div>
  )
}

export function HomeCardManager({ cards, pluginCards, controlsPortalId }: HomeCardManagerProps) {
  const { t } = useTranslation()
  const [layout, setLayout] = useState<HomeCardLayout>(loadHomeCardLayout)
  const [editing, setEditing] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingCardId, setEditingCardId] = useState<string | null>(null)
  const [controlsContainer, setControlsContainer] = useState<HTMLElement | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  const pluginDefinitions = useMemo<HomeCardDefinition[]>(
    () =>
      pluginCards.map((card) => ({
        id: card.id,
        title: card.title,
        description: card.description,
        width: card.width,
        category: 'plugin',
        source: 'plugin' as const,
        render: () => <PluginHomeCardView card={card} />,
      })),
    [pluginCards]
  )

  const allCards = useMemo(() => [...cards, ...pluginDefinitions], [cards, pluginDefinitions])
  const cardMap = useMemo(() => new Map(allCards.map((card) => [card.id, card])), [allCards])
  const allCardIds = useMemo(() => allCards.map((card) => card.id), [allCards])

  const updateLayout = useCallback((updater: (current: HomeCardLayout) => HomeCardLayout) => {
    setLayout((current) => {
      const next = updater(current)
      if (next === current) {
        return current
      }
      saveHomeCardLayout(next)
      return next
    })
  }, [])

  useEffect(() => {
    updateLayout((current) => {
      const knownIds = new Set(allCardIds)
      const newIds = allCardIds.filter((id) => !current.order.includes(id))
      const order = mergeCardOrder(current.order, allCardIds)
      const defaultHiddenIds = newIds.filter((id) => cardMap.get(id)?.defaultHidden)
      const hidden = Array.from(
        new Set([...current.hidden.filter((id) => knownIds.has(id)), ...defaultHiddenIds])
      )
      const rowModes = normalizeRowModes(current.rowModes)
      const styles = Object.fromEntries(
        Object.entries(normalizeCardStyles(current.styles)).filter(([id]) => knownIds.has(id))
      )
      const widths = Object.fromEntries(
        Object.entries(normalizeCardWidths(current.widths)).filter(([id]) => knownIds.has(id))
      )
      if (
        stringArraysEqual(order, current.order) &&
        stringArraysEqual(hidden, current.hidden) &&
        rowModesEqual(rowModes, current.rowModes) &&
        JSON.stringify(styles) === JSON.stringify(current.styles) &&
        JSON.stringify(widths) === JSON.stringify(current.widths)
      ) {
        return current
      }
      return { ...current, order, hidden, rowModes, styles, widths }
    })
  }, [allCardIds, cardMap, updateLayout])

  useEffect(() => {
    if (!controlsPortalId || typeof document === 'undefined') {
      setControlsContainer(null)
      return
    }
    setControlsContainer(document.getElementById(controlsPortalId))
  }, [controlsPortalId])

  const visibleCards = useMemo(
    () =>
      layout.order
        .map((id) => cardMap.get(id))
        .filter(
          (card): card is HomeCardDefinition =>
            card !== undefined && !layout.hidden.includes(card.id)
        ),
    [cardMap, layout.hidden, layout.order]
  )
  const hiddenCards = useMemo(
    () =>
      layout.hidden
        .map((id) => cardMap.get(id))
        .filter((card): card is HomeCardDefinition => card !== undefined),
    [cardMap, layout.hidden]
  )
  const hiddenCardsByCategory = useMemo(() => {
    const groups = new Map<HomeCardCategory, HomeCardDefinition[]>()
    for (const card of hiddenCards) {
      const category = card.category ?? (card.source === 'plugin' ? 'plugin' : 'status')
      groups.set(category, [...(groups.get(category) ?? []), card])
    }
    return Array.from(groups.entries())
  }, [hiddenCards])
  const editingCard = editingCardId ? cardMap.get(editingCardId) : undefined
  const adaptiveCardWidths = useMemo(
    () => buildAdaptiveCardWidths(visibleCards, layout.widths),
    [layout.widths, visibleCards]
  )
  const cardRows = useMemo(
    () => buildCardRows(visibleCards, adaptiveCardWidths),
    [adaptiveCardWidths, visibleCards]
  )
  const rowModes = useMemo(
    () =>
      cardRows.map((row, index) => layout.rowModes[String(index)] ?? defaultRowMode(index, row)),
    [cardRows, layout.rowModes]
  )
  const rowControls = useMemo(
    () =>
      rowModes
        .map((mode, index) => {
          const top = rowModes
            .slice(0, index)
            .reduce(
              (offset, previousMode, previousIndex) =>
                offset + rowHeight(previousMode, cardRows[previousIndex]) + HOME_CARD_GRID_GAP,
              0
            )
          return { index, mode, top }
        })
        .filter((row) => !isSeparatorRow(cardRows[row.index])),
    [cardRows, rowModes]
  )
  const gridStyle =
    cardRows.length > 0
      ? ({
          '--home-card-grid-rows': rowModes
            .map((mode, index) => `${rowHeight(mode, cardRows[index])}px`)
            .join(' '),
        } as CSSProperties)
      : undefined

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event
      if (!over || active.id === over.id) return
      updateLayout((current) => {
        const visibleIds = visibleCards.map((card) => card.id)
        const oldIndex = visibleIds.indexOf(String(active.id))
        const newIndex = visibleIds.indexOf(String(over.id))
        if (oldIndex < 0 || newIndex < 0) return current
        const reorderedVisibleIds = arrayMove(visibleIds, oldIndex, newIndex)
        const remainingIds = current.order.filter((id) => !visibleIds.includes(id))
        return { ...current, order: [...reorderedVisibleIds, ...remainingIds] }
      })
    },
    [updateLayout, visibleCards]
  )

  const hideCard = useCallback(
    (id: string) => {
      updateLayout((current) => ({
        ...current,
        hidden: Array.from(new Set([...current.hidden, id])),
      }))
    },
    [updateLayout]
  )

  const restoreCard = useCallback(
    (id: string) => {
      updateLayout((current) => ({
        ...current,
        hidden: current.hidden.filter((item) => item !== id),
      }))
    },
    [updateLayout]
  )

  const toggleRowMode = useCallback(
    (rowIndex: number) => {
      updateLayout((current) => {
        const key = String(rowIndex)
        const currentMode = current.rowModes[key] ?? defaultRowMode(rowIndex, cardRows[rowIndex])
        return {
          ...current,
          rowModes: {
            ...current.rowModes,
            [key]: currentMode === 'high' ? 'low' : 'high',
          },
        }
      })
    },
    [cardRows, updateLayout]
  )

  const resizeCard = useCallback(
    (id: string) => {
      const card = cardMap.get(id)
      const allowedWidths = card?.allowedWidths
      if (!card || !allowedWidths || allowedWidths.length < 2) return

      updateLayout((current) => {
        const currentWidth = current.widths[id] ?? card.width ?? allowedWidths[0]
        const currentIndex = allowedWidths.indexOf(currentWidth)
        const nextWidth = allowedWidths[(currentIndex + 1) % allowedWidths.length]
        return {
          ...current,
          widths: {
            ...current.widths,
            [id]: nextWidth,
          },
        }
      })
    },
    [cardMap, updateLayout]
  )

  const updateCardStyle = useCallback(
    (id: string, style: HomeCardStyle) => {
      updateLayout((current) => ({
        ...current,
        styles: {
          ...current.styles,
          [id]: style,
        },
      }))
    },
    [updateLayout]
  )

  const editCardContent = useCallback(() => {
    if (!editingCard?.onEdit) return
    setEditingCardId(null)
    editingCard.onEdit()
  }, [editingCard])

  const resetLayout = useCallback(() => {
    updateLayout(() => ({
      order: allCardIds,
      hidden: allCards.filter((card) => card.defaultHidden).map((card) => card.id),
      rowModes: {},
      styles: {},
      widths: {},
    }))
  }, [allCardIds, allCards, updateLayout])

  const controls = (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Button variant="outline" size="sm" onClick={resetLayout} className="gap-2">
        <RotateCcw className="h-4 w-4" />
        {t('home.cards.reset')}
      </Button>
      <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)} className="gap-2">
        <Plus className="h-4 w-4" />
        {t('home.cards.add')}
      </Button>
      <Button
        variant={editing ? 'default' : 'outline'}
        size="sm"
        onClick={() => setEditing((value) => !value)}
        className="gap-2"
      >
        <GripVertical className="h-4 w-4" />
        {editing ? t('home.cards.done') : t('home.cards.edit')}
      </Button>
    </div>
  )

  return (
    <TooltipProvider>
      <div className="space-y-3">
        {controlsPortalId && controlsContainer ? createPortal(controls, controlsContainer) : null}
        {!controlsPortalId && controls}

        <div className="relative">
          {editing && rowControls.length > 0 && (
            <div className="pointer-events-none absolute inset-x-0 top-0 z-30 hidden lg:block">
              {rowControls.map((row) => (
                <Button
                  key={row.index}
                  type="button"
                  variant={row.mode === 'high' ? 'default' : 'outline'}
                  size="sm"
                  className="bg-background/95 pointer-events-auto absolute left-2 h-7 px-2 text-xs shadow-sm backdrop-blur"
                  style={{ top: row.top + 8 }}
                  onClick={() => toggleRowMode(row.index)}
                >
                  {row.mode === 'high' ? t('home.cards.row.high') : t('home.cards.row.low')}
                </Button>
              ))}
            </div>
          )}

          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={visibleCards.map((card) => card.id)}
              strategy={rectSortingStrategy}
            >
              <div
                data-home-summary-cards="true"
                data-home-row-sizing="custom"
                className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-10"
                style={gridStyle}
              >
                {visibleCards.map((card) => (
                  <SortableHomeCard
                    key={card.id}
                    card={card}
                    displayWidth={adaptiveCardWidths.get(card.id)}
                    preferredWidth={layout.widths[card.id] ?? card.width}
                    cardStyle={layout.styles[card.id] ?? 'default'}
                    editing={editing}
                    onEdit={setEditingCardId}
                    onHide={hideCard}
                    onResize={resizeCard}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        </div>

        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('home.cards.dialog.title')}</DialogTitle>
              <DialogDescription>{t('home.cards.dialog.description')}</DialogDescription>
            </DialogHeader>
            <DialogBody viewportClassName="max-h-[62vh]">
              <div className="space-y-5 pr-1">
                <div className="space-y-2">
                  <div className="text-sm font-medium">{t('home.cards.dialog.hiddenCards')}</div>
                  {hiddenCards.length === 0 ? (
                    <div className="text-muted-foreground rounded-md border border-dashed p-3 text-sm">
                      {t('home.cards.dialog.noHiddenCards')}
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {hiddenCardsByCategory.map(([category, categoryCards]) => (
                        <div key={category} className="space-y-2">
                          <div className="text-muted-foreground text-xs font-medium">
                            {t(`home.cards.categories.${category}`)}
                          </div>
                          {categoryCards.map((card) => (
                            <div
                              key={card.id}
                              className="flex items-center justify-between gap-3 rounded-md border p-3"
                            >
                              <div className="min-w-0">
                                <div className="truncate text-sm font-medium">{card.title}</div>
                                {card.description && (
                                  <div className="text-muted-foreground truncate text-xs">
                                    {card.description}
                                  </div>
                                )}
                              </div>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => restoreCard(card.id)}
                              >
                                {t('home.cards.dialog.restore')}
                              </Button>
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={resetLayout} className="mr-auto gap-2">
                <RotateCcw className="h-4 w-4" />
                {t('home.cards.dialog.reset')}
              </Button>
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                {t('home.cards.dialog.cancel')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog
          open={editingCard !== undefined}
          onOpenChange={(open) => {
            if (!open) setEditingCardId(null)
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {t('home.cards.styleDialog.title', { title: editingCard?.title ?? '' })}
              </DialogTitle>
              <DialogDescription>{t('home.cards.styleDialog.description')}</DialogDescription>
            </DialogHeader>
            <DialogBody>
              {editingCard && (
                <div className="grid gap-3 sm:grid-cols-3">
                  {(['default', 'orange', 'borderless'] as const).map((style) => {
                    const selected = (layout.styles[editingCard.id] ?? 'default') === style
                    return (
                      <button
                        key={style}
                        type="button"
                        data-selected={selected ? 'true' : 'false'}
                        className={cn(
                          'min-h-28 rounded-lg border-2 p-3 text-left transition-colors',
                          selected ? 'border-primary' : 'border-border hover:border-primary/50',
                          style === 'orange' && 'bg-primary text-background',
                          style === 'borderless' && 'border-dashed bg-transparent shadow-none'
                        )}
                        onClick={() => updateCardStyle(editingCard.id, style)}
                      >
                        <span className="font-medium">{t(`home.cards.styles.${style}.title`)}</span>
                        <span
                          className={cn(
                            'mt-2 block text-xs',
                            style === 'orange' ? 'text-background/80' : 'text-muted-foreground'
                          )}
                        >
                          {t(`home.cards.styles.${style}.description`)}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )}
            </DialogBody>
            <DialogFooter>
              {editingCard?.onEdit && (
                <Button variant="outline" onClick={editCardContent} className="mr-auto">
                  {editingCard.editLabel ?? t('home.cards.editContent')}
                </Button>
              )}
              <Button onClick={() => setEditingCardId(null)}>
                {t('home.cards.styleDialog.done')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  )
}
