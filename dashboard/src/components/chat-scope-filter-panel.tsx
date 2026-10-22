import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

import { StreamlineIcon } from '@/components/ui/streamline-icon'
import { Button } from '@/components/ui/button'
import { SelectionListItem } from '@/components/ui/selection-list-item'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { cn } from '@/lib/utils'

export interface ChatScopeMode<T extends string> {
  label: string
  value: T
}

export interface ChatScopeItem {
  id: string | number
  label: ReactNode
  title?: string
  description?: ReactNode
  descriptionTitle?: string
  disabled?: boolean
  meta?: ReactNode
}

interface ChatScopeFilterPanelProps<TMode extends string> {
  modes?: ChatScopeMode<TMode>[]
  activeMode?: TMode
  onModeChange?: (mode: TMode) => void
  items: ChatScopeItem[]
  selectedItemId?: string | number | null
  onItemSelect?: (id: string | number) => void
  title?: string
  titleMeta?: ReactNode
  density?: 'default' | 'compact'
  emptyContent?: ReactNode
  footer?: ReactNode
  modeColumns?: number
  collapsed?: boolean
  collapseLabel?: string
  expandLabel?: string
  onCollapsedChange?: (collapsed: boolean) => void
  className?: string
  listClassName?: string
}

export function ChatScopeFilterPanel<TMode extends string>({
  modes,
  activeMode,
  onModeChange,
  items,
  selectedItemId,
  onItemSelect,
  title,
  titleMeta,
  density = 'default',
  emptyContent,
  footer,
  modeColumns,
  collapsed = false,
  collapseLabel = '折叠列表',
  expandLabel = '展开列表',
  onCollapsedChange,
  className,
  listClassName,
}: ChatScopeFilterPanelProps<TMode>) {
  const modeItems = modes ?? []
  const hasModes = modeItems.length > 0

  return (
    <PageFrame asChild>
      <aside
        data-chat-scope-panel="true"
        data-collapsed={collapsed ? 'true' : 'false'}
        className={cn(
          'flex min-h-0 flex-col lg:h-full lg:self-stretch lg:overflow-hidden',
          className
        )}
      >
        <div
          data-chat-scope-panel-header="true"
          className={cn('space-y-2 border-b-2 py-2', collapsed ? 'px-2' : 'px-3')}
        >
          {(title || titleMeta || onCollapsedChange) && (
            <div className="flex min-w-0 items-center gap-2">
              {!collapsed && (
                <>
                  <h2 className="min-w-0 flex-1 truncate text-xs font-medium">{title}</h2>
                  {titleMeta && (
                    <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                      {titleMeta}
                    </span>
                  )}
                </>
              )}
              {onCollapsedChange && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => onCollapsedChange(!collapsed)}
                  aria-label={collapsed ? expandLabel : collapseLabel}
                  aria-expanded={!collapsed}
                  title={collapsed ? expandLabel : collapseLabel}
                >
                  <StreamlineIcon
                    name="line-arrow-right-1-remix"
                    fallback={ChevronRight}
                    className={cn('h-3 w-3', !collapsed && 'rotate-180')}
                  />
                </Button>
              )}
            </div>
          )}
          {!collapsed && hasModes && (
            <div
              data-chat-scope-panel-modes="true"
              className="grid gap-1"
              style={{
                gridTemplateColumns: `repeat(${modeColumns ?? modeItems.length}, minmax(0, 1fr))`,
              }}
            >
              {modeItems.map((mode) => (
                <Button
                  key={mode.value}
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => onModeChange?.(mode.value)}
                  data-chat-scope-panel-mode="true"
                  data-active={activeMode === mode.value ? 'true' : 'false'}
                  aria-pressed={activeMode === mode.value}
                  className="min-w-0 px-1.5"
                >
                  <span className="block truncate">{mode.label}</span>
                </Button>
              ))}
            </div>
          )}
        </div>

        {!collapsed && (
          <>
            <DataPanel
              padding="list"
              inset
              data-chat-scope-panel-list="true"
              className={cn('min-h-0 flex-1 space-y-1 overflow-y-auto', listClassName)}
            >
              {items.length > 0
                ? items.map((item) => {
                    const active = selectedItemId === item.id
                    return (
                      <SelectionListItem
                        key={item.id}
                        type="button"
                        onClick={() => onItemSelect?.(item.id)}
                        data-chat-scope-panel-item="true"
                        data-active={active ? 'true' : 'false'}
                        selected={active}
                        density={density}
                        disabled={item.disabled}
                        title={item.title}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span className="min-w-0 flex-1 truncate font-medium">{item.label}</span>
                          {item.meta != null && (
                            <span className="shrink-0 text-xs tabular-nums opacity-75">
                              {item.meta}
                            </span>
                          )}
                        </span>
                        {item.description && (
                          <span
                            className={cn(
                              'block truncate text-xs',
                              active ? 'text-current opacity-75' : 'text-muted-foreground'
                            )}
                            title={item.descriptionTitle}
                          >
                            {item.description}
                          </span>
                        )}
                      </SelectionListItem>
                    )
                  })
                : emptyContent}
            </DataPanel>
            {footer && (
              <div data-chat-scope-panel-footer="true" className="border-t-2 px-3 py-2">
                {footer}
              </div>
            )}
          </>
        )}
      </aside>
    </PageFrame>
  )
}
