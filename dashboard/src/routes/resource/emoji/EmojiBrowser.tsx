import type { ComponentProps, ReactNode } from 'react'
import { ArrowUpDown, MoreHorizontal, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DataBrowserHeader,
  DataBrowserLayout,
  DataToolbar,
  MetricStrip,
  StatusIndicator,
} from '@/components/ui/data-browser'
import { DataPagination } from '@/components/ui/data-pagination'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { SectionPanel } from '@/components/ui/management'
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
import { EmojiList } from './EmojiList'

export interface EmojiFilters {
  status: EmojiStatus | 'all'
  format: string
  sortBy: string
  sortOrder: 'desc' | 'asc'
}

interface EmojiBrowserProps extends ComponentProps<typeof EmojiList> {
  browserHeader?: ReactNode
  stats: EmojiStats | null
  filters: EmojiFilters
  onFilterChange: <K extends keyof EmojiFilters>(key: K, value: EmojiFilters[K]) => void
  search: string
  onSearchChange: (value: string) => void
  cardSize: 'small' | 'medium' | 'large'
  onCardSizeChange: (size: 'small' | 'medium' | 'large') => void
  total: number
  page: number
  pageSize: number
  pageSizeOptions: number[]
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  onUpload: () => void
  onOpenCache: () => void
  batchActions?: ReactNode
  error?: string
  onRetry: () => void
}

/** 表情包浏览框架，学习页负责注入导航页签。 */
export function EmojiBrowser({
  browserHeader,
  stats,
  filters,
  onFilterChange,
  search,
  onSearchChange,
  cardSize,
  onCardSizeChange,
  total,
  page,
  pageSize,
  pageSizeOptions,
  onPageChange,
  onPageSizeChange,
  onUpload,
  onOpenCache,
  batchActions,
  error,
  onRetry,
  ...listProps
}: EmojiBrowserProps) {
  const states = [
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
  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <DataBrowserHeader
        title="学习"
        navigation={browserHeader}
        summary={
          <>
            <MetricStrip
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
                status={stats.collecting_enabled ? 'on' : 'off'}
                title="是否自动收集聊天中的表情包"
              >
                {stats.collecting_enabled ? '收集中' : '收集已关闭'}
              </StatusIndicator>
            )}
            {stats && (
              <StatusIndicator
                status={stats.adopted > 0 ? 'on' : 'off'}
                title="已注册表情可供麦麦选用"
              >
                {stats.adopted > 0 ? '有可用表情' : '暂无可用表情'}
              </StatusIndicator>
            )}
          </>
        }
      />
      <DataBrowserLayout
        sidebar={
          <SectionPanel
            title="筛选"
            className="min-h-0 overflow-y-auto"
            bodyClassName="space-y-5 p-3"
            headerClassName="p-3"
          >
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
              <div className="flex flex-wrap gap-1.5">
                {['all', ...Object.keys(stats?.formats ?? {})].map((format) => (
                  <Button
                    key={format}
                    variant="outline"
                    size="xs"
                    aria-pressed={filters.format === format}
                    onClick={() => onFilterChange('format', format)}
                  >
                    {format === 'all' ? '全部' : format.toUpperCase()}
                  </Button>
                ))}
              </div>
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
          </SectionPanel>
        }
      >
        <PageFrame className="flex min-h-0 flex-col overflow-hidden">
          <DataToolbar
            search={search}
            onSearchChange={onSearchChange}
            placeholder="搜索标签、描述或哈希..."
            actions={
              <>
                <div className="flex gap-1" role="group" aria-label="卡片大小">
                  {(['small', 'medium', 'large'] as const).map((size, index) => (
                    <Button
                      key={size}
                      variant="outline"
                      size="icon-sm"
                      aria-pressed={cardSize === size}
                      aria-label={`${['小', '中', '大'][index]}卡片`}
                      onClick={() => onCardSizeChange(size)}
                    >
                      <span
                        aria-hidden="true"
                        className={
                          ['h-2 w-2 bg-current', 'h-3 w-3 bg-current', 'h-4 w-4 bg-current'][index]
                        }
                      />
                    </Button>
                  ))}
                </div>
                <Button size="sm" onClick={onUpload}>
                  <Plus />
                  上传
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
          <div className="min-h-0 flex-1 overflow-y-auto p-3" aria-busy={listProps.loading}>
            {error ? (
              <div className="space-y-3 py-12 text-center">
                <p className="text-destructive text-sm">{error}</p>
                <Button variant="outline" size="sm" onClick={onRetry}>
                  重试
                </Button>
              </div>
            ) : (
              <EmojiList {...listProps} />
            )}
          </div>
          <DataPagination
            total={total}
            page={page}
            pageSize={pageSize}
            pageSizeOptions={pageSizeOptions}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
            disabled={listProps.loading || !!error}
          />
        </PageFrame>
      </DataBrowserLayout>
    </div>
  )
}
