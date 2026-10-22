import { Fragment, type HTMLAttributes, type ReactNode } from 'react'
import { Search } from 'lucide-react'

import { Input } from './input'
import { DataPanel } from './surface'
import { Separator } from './separator'
import { cn } from '@/lib/utils'
import { StatusLight, type LightStatus } from './status-light'

/** 标题、页签和摘要分开布局，窄屏允许摘要换行。 */
export function DataBrowserHeader({
  title,
  navigation,
  summary,
}: {
  title: string
  navigation?: ReactNode
  summary?: ReactNode
}) {
  return (
    <header data-browser-header="true">
      <div className="flex min-w-0 flex-wrap items-center gap-4">
        <h1 className="text-xl font-normal">{title}</h1>
        {navigation}
      </div>
      {summary && <div data-browser-summary="true">{summary}</div>}
    </header>
  )
}

export function MetricStrip({
  items,
}: {
  items: { label: string; value: ReactNode; positive?: boolean }[]
}) {
  return (
    <div data-metric-strip="true">
      <DataPanel padding="none">
        {items.map((item, index) => (
          <Fragment key={item.label}>
            {index > 0 && <Separator orientation="vertical" />}
            <div className="px-3 py-1">
              <div className="text-muted-foreground text-[10px] leading-tight">{item.label}</div>
              <div
                className={cn(
                  'text-base leading-tight tabular-nums',
                  item.positive && 'text-green-700 dark:text-green-400'
                )}
              >
                {item.value}
              </div>
            </div>
          </Fragment>
        ))}
      </DataPanel>
    </div>
  )
}

export function StatusIndicator({
  status,
  children,
  title,
  variant = 'panel',
}: {
  status: LightStatus
  children?: ReactNode
  title?: string
  variant?: 'panel' | 'inline'
}) {
  return (
    <span data-status-indicator="true" data-status={status} data-status-variant={variant} title={title}>
      <StatusLight status={status} decorative={Boolean(children) || Boolean(title)} />
      {children}
    </span>
  )
}

/** 工具栏只负责搜索和操作区，始终放在页面窗口内部。 */
export function DataToolbar({
  search,
  onSearchChange,
  placeholder,
  actions,
  children,
}: {
  search: string
  onSearchChange: (value: string) => void
  placeholder: string
  actions?: ReactNode
  children?: ReactNode
}) {
  return (
    <div data-browser-toolbar="true">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div className="relative min-w-40 flex-1">
          <Search
            aria-hidden="true"
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 z-10 h-4 w-4 -translate-y-1/2"
          />
          <Input
            controlSize="sm"
            aria-label="搜索"
            placeholder={placeholder}
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      </div>
      {children}
    </div>
  )
}

export function DataBrowserLayout({
  sidebar,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & { sidebar: ReactNode }) {
  return (
    <div data-browser-layout="true" className={cn('min-h-0 min-w-0 flex-1', className)} {...props}>
      {sidebar}
      {children}
    </div>
  )
}
