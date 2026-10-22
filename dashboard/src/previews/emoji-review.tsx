import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { useMediaQuery } from '@/hooks/use-media-query'
import { EmojiBrowser, type EmojiFilters } from '@/routes/resource/emoji/EmojiBrowser'
import type { Emoji } from '@/types/emoji'

const descriptions = [
  '呆萌，无辜，困惑，可爱，惊讶',
  '自泡，惊讶，呆萌，疑惑，可爱',
  '无奈，重试，技术故障，系统提示，无语',
  '委屈，哭泣，难过，伤心，大哭',
  '惊讶，呆萌，震惊，可爱，无语',
  '咸鱼，躺平，摆烂，慵懒，咸鱼翻身',
  '得意，傲娇，自信，嘚瑟，小得意',
  '开心，兴奋，期待，可爱，热情',
  '大哭，崩溃，伤心',
  '发呆，无语，盯',
  '害羞，可爱，猫耳',
  '睡觉，困，晚安',
]
const colors = ['#2c70ad', '#bd4562', '#9e7108', '#29805b', '#b3572c', '#7156b5']
const samples: Emoji[] = Array.from({ length: 276 }, (_, i) => ({
  id: i + 1,
  full_path: '',
  format: ['jpeg', 'jpeg', 'png', 'gif'][i % 4],
  emoji_hash: `sample-${i + 1}`,
  description: descriptions[i % descriptions.length],
  query_count: i % 13,
  usage_count: i % 13,
  status: i < 240 ? 'adopted' : i < 252 ? 'known' : i < 264 ? 'unknown' : 'discarded',
  is_registered: i < 240,
  is_banned: i >= 264,
  emotion: null,
  record_time: 0,
  register_time: null,
  last_used_time: null,
}))

/** 使用正式浏览组件，数据和按钮操作仅作用于这个示例。 */
export function EmojiReview() {
  const [filters, setFilters] = useState<EmojiFilters>({
    status: 'adopted',
    format: 'all',
    sortBy: 'register_time',
    sortOrder: 'desc',
  })
  const [search, setSearch] = useState('')
  const [size, setSize] = useState<'small' | 'medium' | 'large'>('medium')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(24)
  const [selected, setSelected] = useState(new Set<number>())
  const [notice, setNotice] = useState('示例数据；操作仅在预览页生效')
  const wide = useMediaQuery('(min-width: 1280px)')
  const mobile = useMediaQuery('(max-width: 1023px)')
  const columns = mobile
    ? 3
    : { small: wide ? 8 : 6, medium: wide ? 6 : 4, large: wide ? 4 : 3 }[size]
  const items = samples.filter(
    (item) =>
      (filters.status === 'all' || item.status === filters.status) &&
      (filters.format === 'all' || item.format === filters.format) &&
      `${item.description} ${item.emoji_hash}`.includes(search)
  )
  const ordered = [...items].sort(
    (a, b) =>
      (filters.sortBy === 'usage_count' ? a.usage_count - b.usage_count : a.id - b.id) *
      (filters.sortOrder === 'desc' ? -1 : 1)
  )
  return (
    <div className="learning-review-screen">
      <EmojiBrowser
        stats={{
          total: 276,
          registered: 240,
          adopted: 240,
          banned: 12,
          discarded: 12,
          known: 12,
          unknown: 12,
          unregistered: 36,
          recent_count: 23,
          collecting_enabled: true,
          formats: { gif: 69, png: 69, jpeg: 138 },
          top_used: [],
        }}
        filters={filters}
        onFilterChange={(key, value) => {
          setFilters((current) => ({ ...current, [key]: value }))
          setPage(1)
        }}
        search={search}
        onSearchChange={(value) => {
          setSearch(value)
          setPage(1)
        }}
        cardSize={size}
        onCardSizeChange={setSize}
        columns={columns}
        emojiList={ordered.slice((page - 1) * pageSize, page * pageSize)}
        loading={false}
        total={items.length}
        page={page}
        pageSize={pageSize}
        pageSizeOptions={[24, 48, 72, 96]}
        onPageChange={setPage}
        onPageSizeChange={(value) => {
          setPageSize(value)
          setPage(1)
        }}
        selectedIds={selected}
        onToggleSelect={(id) =>
          setSelected((current) => {
            const next = new Set(current)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
          })
        }
        onUpload={() => setNotice('正式页面会打开上传窗口')}
        onOpenCache={() => setNotice('正式页面会打开缓存维护窗口')}
        onViewDetail={(item) => setNotice(`查看详情：${item.description}`)}
        onEdit={() => setNotice('正式页面会打开编辑窗口')}
        onRegister={() => setNotice('正式页面可注册表情')}
        onBan={() => setNotice('正式页面可禁用表情')}
        onDelete={() => setNotice('正式页面会先确认删除')}
        onRetry={() => {}}
        batchActions={
          selected.size > 0 && (
            <div className="mt-2 flex items-center gap-3 text-xs">
              已选择 {selected.size} 个表情包
              <Button variant="outline" size="xs" onClick={() => setSelected(new Set())}>
                取消选择
              </Button>
            </div>
          )
        }
        renderThumbnail={(item) => (
          <svg viewBox="0 0 160 160" className="h-full w-full" aria-label="示例表情">
            <rect width="160" height="160" fill={colors[(item.id - 1) % 6]} opacity=".12" />
            <g fill="none" stroke={colors[(item.id - 1) % 6]} strokeWidth="4">
              <circle cx="80" cy="75" r="23" />
              <path d="M68 68h6m12 0h6M68 80q12 15 24 0" />
            </g>
          </svg>
        )}
      />
      <p className="sample-note" role="status">
        {notice}
      </p>
    </div>
  )
}
