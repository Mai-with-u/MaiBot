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
import { ExternalLink, GripVertical, Plus, RotateCcw, X } from 'lucide-react'
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
const HOME_CARD_LOW_ROW_HEIGHT = 236
const HOME_CARD_HIGH_ROW_HEIGHT = 360
const HOME_CARD_GRID_GAP = 16

type HomeCardSource = 'builtin' | 'plugin'
type HomeCardRowMode = 'low' | 'high'

export interface HomeCardDefinition {
  id: string
  title: string
  description?: string
  width?: PluginHomeCardWidth
  source: HomeCardSource
  render: () => ReactNode
}

interface HomeCardLayout {
  order: string[]
  hidden: string[]
  rowModes: Record<string, HomeCardRowMode>
}

interface HomeCardManagerProps {
  cards: HomeCardDefinition[]
  pluginCards: PluginHomeCard[]
  controlsPortalId?: string
}

function loadHomeCardLayout(): HomeCardLayout {
  if (typeof window === 'undefined') {
    return { order: [], hidden: [], rowModes: {} }
  }

  try {
    const parsed = JSON.parse(localStorage.getItem(HOME_CARD_LAYOUT_STORAGE_KEY) || '{}')
    return {
      order: Array.isArray(parsed.order) ? parsed.order.filter((item: unknown): item is string => typeof item === 'string') : [],
      hidden: Array.isArray(parsed.hidden) ? parsed.hidden.filter((item: unknown): item is string => typeof item === 'string') : [],
      rowModes: normalizeRowModes(parsed.rowModes),
    }
  } catch {
    return { order: [], hidden: [], rowModes: {} }
  }
}

function saveHomeCardLayout(layout: HomeCardLayout): void {
  localStorage.setItem(HOME_CARD_LAYOUT_STORAGE_KEY, JSON.stringify(layout))
}

function sanitizeUrl(url: unknown): string {
  const value = String(url || '').trim()
  if (!value || value.startsWith('//')) return ''
  const lower = value.toLowerCase()
  if (value.startsWith('/') || lower.startsWith('http://') || lower.startsWith('https://') || lower.startsWith('mailto:')) {
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

function rowModesEqual(left: Record<string, HomeCardRowMode>, right: Record<string, HomeCardRowMode>): boolean {
  const leftKeys = Object.keys(left)
  const rightKeys = Object.keys(right)
  return leftKeys.length === rightKeys.length && leftKeys.every((key) => left[key] === right[key])
}

function defaultRowMode(rowIndex: number): HomeCardRowMode {
  return rowIndex === 0 ? 'low' : 'high'
}

function rowHeight(mode: HomeCardRowMode): number {
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

function shrinkCardWidthOneStep(width: PluginHomeCardWidth | undefined): PluginHomeCardWidth | undefined {
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

function buildAdaptiveCardWidths(cards: HomeCardDefinition[]): Map<string, PluginHomeCardWidth | undefined> {
  const widths = new Map<string, PluginHomeCardWidth | undefined>()
  let currentRowColumns = 0

  for (const card of cards) {
    const preferredWidth = card.width
    const preferredColumns = cardWidthColumns(preferredWidth)
    const remainingColumns = 10 - currentRowColumns
    let renderedWidth = preferredWidth
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
            <a className="text-primary hover:underline" href={safeHref} target="_blank" rel="noopener noreferrer" {...props}>
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
          return <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">{children}</code>
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
      <div key={index} className="rounded-md border bg-muted/20 px-3 py-2">
        <div className="text-xs text-muted-foreground">{getBlockText(block, ['label', 'title'])}</div>
        <div className="mt-1 text-xl font-bold">{getBlockText(block, ['value', 'content'])}</div>
        {getBlockText(block, ['detail', 'description']) && (
          <div className="mt-1 text-xs text-muted-foreground">{getBlockText(block, ['detail', 'description'])}</div>
        )}
      </div>
    )
  }
  if (type === 'key_value') {
    const entries = block.entries && typeof block.entries === 'object' && !Array.isArray(block.entries)
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
              <a href={href} target={href.startsWith('/') ? undefined : '_blank'} rel={href.startsWith('/') ? undefined : 'noopener noreferrer'}>
                {getBlockText(action, ['label', 'title']) || href}
              </a>
            </Button>
          )
        })}
      </div>
    )
  }
  return <p key={index} className="text-sm leading-relaxed">{getBlockText(block, ['content', 'text', 'value'])}</p>
}

function PluginHomeCardView({ card }: { card: PluginHomeCard }) {
  const href = sanitizeUrl(card.link_url)
  const content = renderPluginContent(card.content)

  return (
    <Card className="h-full">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <CardTitle className="truncate text-sm font-medium">{card.title}</CardTitle>
            {card.description && <CardDescription className="line-clamp-2">{card.description}</CardDescription>}
          </div>
          <Badge variant="outline" className="shrink-0 text-[10px]">插件</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {content}
        {href && (
          <Button variant="outline" size="sm" asChild className="w-full justify-start gap-2">
            <a href={href} target={href.startsWith('/') ? undefined : '_blank'} rel={href.startsWith('/') ? undefined : 'noopener noreferrer'}>
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
    return content.trim() ? <HomeMarkdown content={content} /> : <p className="text-sm text-muted-foreground">暂无内容</p>
  }
  if (Array.isArray(content)) {
    return <div className="space-y-3">{content.map(renderContentBlock)}</div>
  }
  if (content && typeof content === 'object') {
    return renderContentBlock(content, 0)
  }
  return <p className="text-sm text-muted-foreground">暂无内容</p>
}

function stringArraysEqual(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

function SortableHomeCard({
  card,
  displayWidth,
  editing,
  onHide,
}: {
  card: HomeCardDefinition
  displayWidth?: PluginHomeCardWidth
  editing: boolean
  onHide: (id: string) => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: card.id,
    disabled: !editing,
  })
  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn('relative h-full min-w-0', cardWidthClass(displayWidth ?? card.width), isDragging && 'z-20 opacity-80')}
    >
      {editing && (
        <div
          data-home-card-edit-overlay="true"
          aria-hidden="true"
          className="absolute inset-0 z-10 rounded-lg border border-primary/25 bg-white/20 shadow-[inset_0_1px_0_rgba(255,255,255,0.38),inset_0_0_0_1px_rgba(255,255,255,0.12)] backdrop-blur-md backdrop-saturate-150 dark:bg-black/20"
          style={{
            WebkitBackdropFilter: 'blur(10px) saturate(140%)',
            backdropFilter: 'blur(10px) saturate(140%)',
          }}
        />
      )}
      {editing && (
        <div className="absolute right-2 top-2 z-20 flex items-center gap-1 rounded-md border bg-background/95 p-1 shadow-sm backdrop-blur">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" className="h-7 w-7 cursor-grab" {...attributes} {...listeners}>
                <GripVertical className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>拖拽排序</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
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
          editing && 'pointer-events-none select-none blur-[2.5px] opacity-75'
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
        source: 'plugin' as const,
        render: () => <PluginHomeCardView card={card} />,
      })),
    [pluginCards]
  )

  const allCards = useMemo(
    () => [...cards, ...pluginDefinitions],
    [cards, pluginDefinitions]
  )
  const cardMap = useMemo(() => new Map(allCards.map((card) => [card.id, card])), [allCards])
  const allCardIds = useMemo(() => allCards.map((card) => card.id), [allCards])

  const updateLayout = useCallback((updater: (current: HomeCardLayout) => HomeCardLayout) => {
    setLayout((current) => {
      const next = updater(current)
      saveHomeCardLayout(next)
      return next
    })
  }, [])

  useEffect(() => {
    updateLayout((current) => {
      const knownIds = new Set(allCardIds)
      const order = [...current.order.filter((id) => knownIds.has(id)), ...allCardIds.filter((id) => !current.order.includes(id))]
      const hidden = current.hidden.filter((id) => knownIds.has(id))
      const rowModes = normalizeRowModes(current.rowModes)
      if (
        stringArraysEqual(order, current.order)
        && stringArraysEqual(hidden, current.hidden)
        && rowModesEqual(rowModes, current.rowModes)
      ) {
        return current
      }
      return { ...current, order, hidden, rowModes }
    })
  }, [allCardIds, updateLayout])

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
        .filter((card): card is HomeCardDefinition => card !== undefined && !layout.hidden.includes(card.id)),
    [cardMap, layout.hidden, layout.order]
  )
  const hiddenCards = useMemo(
    () =>
      layout.hidden
        .map((id) => cardMap.get(id))
        .filter((card): card is HomeCardDefinition => card !== undefined),
    [cardMap, layout.hidden]
  )
  const adaptiveCardWidths = useMemo(() => buildAdaptiveCardWidths(visibleCards), [visibleCards])
  const cardRows = useMemo(() => buildCardRows(visibleCards, adaptiveCardWidths), [adaptiveCardWidths, visibleCards])
  const rowModes = useMemo(
    () => cardRows.map((_, index) => layout.rowModes[String(index)] ?? defaultRowMode(index)),
    [cardRows, layout.rowModes]
  )
  const rowControls = useMemo(() => {
    let rowTop = 0
    return rowModes.map((mode, index) => {
      const top = rowTop
      rowTop += rowHeight(mode) + HOME_CARD_GRID_GAP
      return { index, mode, top }
    })
  }, [rowModes])
  const gridStyle = cardRows.length > 0
    ? ({
      '--home-card-grid-rows': rowModes.map((mode) => `${rowHeight(mode)}px`).join(' '),
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

  const hideCard = useCallback((id: string) => {
    updateLayout((current) => ({ ...current, hidden: Array.from(new Set([...current.hidden, id])) }))
  }, [updateLayout])

  const restoreCard = useCallback((id: string) => {
    updateLayout((current) => ({ ...current, hidden: current.hidden.filter((item) => item !== id) }))
  }, [updateLayout])

  const toggleRowMode = useCallback((rowIndex: number) => {
    updateLayout((current) => {
      const key = String(rowIndex)
      const currentMode = current.rowModes[key] ?? defaultRowMode(rowIndex)
      return {
        ...current,
        rowModes: {
          ...current.rowModes,
          [key]: currentMode === 'high' ? 'low' : 'high',
        },
      }
    })
  }, [updateLayout])

  const resetLayout = useCallback(() => {
    updateLayout(() => ({
      order: allCardIds,
      hidden: [],
      rowModes: {},
    }))
  }, [allCardIds, updateLayout])

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
      <Button variant={editing ? 'default' : 'outline'} size="sm" onClick={() => setEditing((value) => !value)} className="gap-2">
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
                  className="pointer-events-auto absolute left-2 h-7 bg-background/95 px-2 text-xs shadow-sm backdrop-blur"
                  style={{ top: row.top + 8 }}
                  onClick={() => toggleRowMode(row.index)}
                >
                  {row.mode === 'high' ? t('home.cards.row.high') : t('home.cards.row.low')}
                </Button>
              ))}
            </div>
          )}

          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={visibleCards.map((card) => card.id)} strategy={rectSortingStrategy}>
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
                    editing={editing}
                    onHide={hideCard}
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
                    <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                      {t('home.cards.dialog.noHiddenCards')}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {hiddenCards.map((card) => (
                        <div key={card.id} className="flex items-center justify-between gap-3 rounded-md border p-3">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-medium">{card.title}</div>
                            {card.description && <div className="truncate text-xs text-muted-foreground">{card.description}</div>}
                          </div>
                          <Button variant="outline" size="sm" onClick={() => restoreCard(card.id)}>
                            {t('home.cards.dialog.restore')}
                          </Button>
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
      </div>
    </TooltipProvider>
  )
}
