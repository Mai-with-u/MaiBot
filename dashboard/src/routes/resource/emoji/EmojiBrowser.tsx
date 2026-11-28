import { useEffect } from 'react'
import type { LearningBrowserTabs } from '../learning'
import type { ComponentProps, ReactNode } from 'react'
import { ArrowUpDown, MoreHorizontal } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DataBrowserHeader,
  DataBrowserLayout,
  DataToolbar,
  MetricStrip,
  StatusIndicator,
} from '@/components/ui/data-browser'
import { DataPagination } from '@/components/ui/data-pagination'
import { PillNav } from '@/components/ui/pill-nav'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { SelectionListItem } from '@/components/ui/selection-list-item'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { EmojiStats, EmojiStatus } from '@/types/emoji'
import { emojiPageSizes } from './useEmojiPageSize'
import { EmojiList } from './EmojiList'

export interface EmojiFilters {
  status: EmojiStatus | 'locked' | 'all'
  format: string
  sortBy: string
  sortOrder: 'desc' | 'asc'
}

interface EmojiBrowserProps extends Omit<ComponentProps<typeof EmojiList>, 'columns' | 'rows'> {
  browserTabs?: LearningBrowserTabs
  stats: EmojiStats | null
  filters: EmojiFilters
  onFilterChange: <K extends keyof EmojiFilters>(key: K, value: EmojiFilters[K]) => void
  search: string
  onSearchChange: (value: string) => void
  onSelectPage: () => void
  onSelectAll: () => void
  selectionBusy?: boolean
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  onOpenCache: () => void
  quickSettings?: ReactNode
  batchActions?: ReactNode
  error?: string
  onRetry: () => void
}

/** 表情包浏览框架，学习页负责注入导航页签。 */
export function EmojiBrowser({
  browserTabs,
  stats,
  filters,
  onFilterChange,
  search,
  onSearchChange,
  onSelectPage,
  onSelectAll,
  selectionBusy,
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  onOpenCache,
  quickSettings,
  batchActions,
  error,
  onRetry,
  ...listProps
}: EmojiBrowserProps) {
  const rows = pageSize === 8 ? 2 : 3
  const columns = pageSize / rows
  const pages = Math.ceil(total / pageSize)
  useEffect(() => {
    function turnPage(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
      if (document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [data-radix-popper-content-wrapper]')) return
      if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable="true"], [role="tablist"], [role="combobox"], [role="slider"]')) return
      const direction = { ArrowLeft: -1, PageUp: -1, ArrowRight: 1, PageDown: 1 }[event.key]
      if (!direction || listProps.loading || error) return
      event.preventDefault()
      const next = page + direction
      if (next >= 1 && next <= pages) onPageChange(next)
    }
    window.addEventListener('keydown', turnPage)
    return () => window.removeEventListener('keydown', turnPage)
  }, [page, pages, listProps.loading, error, onPageChange])

  const states = [
    { value: 'locked' as const, label: '已锁定', count: stats?.locked, color: 'bg-blue-500' },
    { value: 'adopted' as const, label: '已注册', count: stats?.adopted, color: 'bg-green-500' },
    {
      value: 'known' as const,
      label: '待审核 · 已识别',
      count: stats?.known,
      color: 'bg-orange-400',
    },
    {
      value: 'unknown' as const,
      label: '待审核 · 未识别',
      count: stats?.unknown,
      color: 'bg-amber-400',
    },
    {
      value: 'discarded' as const,
      label: '已禁用',
      count: stats?.discarded,
      color: 'bg-muted-foreground',
    },
    { value: 'all' as const, label: '全部', count: stats?.total, color: 'bg-muted-foreground' },
  ]
  const summary = (
    <>
      <MetricStrip
        responsiveCompact
        items={[
          { label: '总数量', value: stats?.total.toLocaleString() ?? '—' },
          {
            label: '近 7 天新增',
            value:
              stats?.recent_count == null ? '—' : `+${stats.recent_count.toLocaleString()}`,
            positive: true,
          },
        ]}
      />
      {stats?.collecting_enabled != null && (
        <StatusIndicator
          variant="inline"
          status={stats.collecting_enabled ? 'on' : 'off'}
          title="是否自动将候选表情注册为可发送的表情。关闭后，聊天表情仍会为识别而缓存。"
        >
          <span className="whitespace-nowrap text-xs">
            {stats.collecting_enabled ? '自动注册开' : '自动注册关'}
          </span>
        </StatusIndicator>
      )}
    </>
  )
  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {!browserTabs && <DataBrowserHeader title="学习" summary={summary} />}
      <DataBrowserLayout
        tabs={browserTabs?.(summary)}
        sidebar={
          <PageFrame role="region" aria-label="表情包筛选" className="min-h-0 space-y-5 overflow-y-auto p-3">
            <section className="space-y-2" aria-label="表情包状态">
              <h3 className="text-muted-foreground text-xs">状态</h3>
              <DataPanel padding="list">
                {states.map((item) => (
                  <SelectionListItem
                    key={item.value}
                    density="compact"
                    selected={filters.status === item.value}
                    onClick={() => onFilterChange('status', item.value)}
                  >
                    <span className="flex items-center gap-2 py-1">
                      <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 ${item.color}`} />
                      <span>{item.label}</span>
                      <span className="ml-auto text-xs tabular-nums">
                        {item.count?.toLocaleString() ?? '—'}
                      </span>
                    </span>
                  </SelectionListItem>
                ))}
              </DataPanel>
            </section>
            <section className="space-y-2" aria-label="格式">
              <h3 className="text-muted-foreground text-xs">格式</h3>
              <PillNav
                variant="pill"
                size="xs"
                scrollable
                ariaLabel="格式"
                value={filters.format}
                onChange={(format) => onFilterChange('format', format)}
                options={['all', ...Object.keys(stats?.formats ?? {})].map((format) => ({
                  value: format,
                  label: format === 'all' ? '全部' : format.toUpperCase(),
                }))}
              />
            </section>
            <section className="space-y-2">
              <h3 className="text-muted-foreground text-xs">排序</h3>
              <div className="flex items-center gap-2">
                <Select
                  value={filters.sortBy}
                  onValueChange={(value) => onFilterChange('sortBy', value)}
                >
                  <SelectTrigger controlSize="sm" aria-label="排序字段" className="min-w-0 flex-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="register_time">注册时间</SelectItem>
                    <SelectItem value="record_time">记录时间</SelectItem>
                    <SelectItem value="usage_count">使用次数</SelectItem>
                    <SelectItem value="last_used_time">最后使用</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  variant="outline"
                  size="icon-sm"
                  aria-label={filters.sortOrder === 'desc' ? '切换为正序' : '切换为倒序'}
                  onClick={() =>
                    onFilterChange('sortOrder', filters.sortOrder === 'desc' ? 'asc' : 'desc')
                  }
                >
                  <ArrowUpDown />
                </Button>
              </div>
            </section>
            {quickSettings}
          </PageFrame>
        }
      >
        <PageFrame className="flex min-h-0 flex-col overflow-hidden">
          <DataToolbar
            search={search}
            onSearchChange={onSearchChange}
            placeholder="搜索标签、描述或哈希..."
            actions={
              <>
                <Button size="sm" variant="outline" disabled={!listProps.emojiList.length || selectionBusy} onClick={onSelectPage}>
                  {listProps.emojiList.length > 0 && listProps.emojiList.every((item) => listProps.selectedIds.has(item.id)) ? '取消本页选择' : '选择本页'}
                </Button>
                <Button size="sm" variant="outline" title="选择当前搜索和筛选下的所有表情" disabled={!total || selectionBusy} onClick={onSelectAll}>
                  选择全部
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="icon-sm" aria-label="更多操作">
                      <MoreHorizontal />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={onOpenCache}>缓存维护</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </>
            }
          >
            {batchActions}
          </DataToolbar>
          <div className="min-h-0 flex-1 overflow-hidden p-3" aria-busy={listProps.loading}>
            {error ? (
              <div className="space-y-3 py-12 text-center">
                <p className="text-destructive text-sm">{error}</p>
                <Button variant="outline" size="sm" onClick={onRetry}>
                  重试
                </Button>
              </div>
            ) : (
              <EmojiList {...listProps} columns={columns} rows={rows} />
            )}
          </div>
          <DataPagination
            total={total}
            page={page}
            pageSize={pageSize}
            pageSizeOptions={emojiPageSizes.desktop}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
            disabled={listProps.loading || !!error}
          />
        </PageFrame>
      </DataBrowserLayout>
    </div>
  )
}
