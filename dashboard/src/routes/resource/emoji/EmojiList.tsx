import type { ReactNode } from 'react'
import { Ban, CheckCircle2, Edit, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { EmojiThumbnail } from '@/components/emoji-thumbnail'
import { getEmojiThumbnailUrl } from '@/lib/emoji-api'
import type { Emoji } from '@/types/emoji'

const statusLabel: Record<Emoji['status'], string> = {
  adopted: '已注册',
  known: '待审核 · 已识别',
  unknown: '待审核 · 未识别',
  discarded: '已禁用',
}

interface EmojiListProps {
  emojiList: Emoji[]
  loading: boolean
  columns: number
  selectedIds: Set<number>
  onToggleSelect: (id: number) => void
  onEdit: (emoji: Emoji) => void
  onViewDetail: (emoji: Emoji) => void
  onRegister: (emoji: Emoji) => void
  onBan: (emoji: Emoji) => void
  onDelete: (emoji: Emoji) => void
  /** 预览可传入本地示例图片；正式页面使用真实缩略图。 */
  renderThumbnail?: (emoji: Emoji) => ReactNode
}

/** 图片、角标与轻量操作放在同一屏幕中，描述留在外面；分页由浏览窗口统一管理。 */
export function EmojiList({
  emojiList,
  loading,
  columns,
  selectedIds,
  onToggleSelect,
  onEdit,
  onViewDetail,
  onRegister,
  onBan,
  onDelete,
  renderThumbnail,
}: EmojiListProps) {
  if (loading)
    return (
      <div role="status" className="text-muted-foreground py-12 text-center text-sm">
        正在加载表情包…
      </div>
    )
  if (emojiList.length === 0)
    return <div className="text-muted-foreground py-12 text-center text-sm">暂无数据</div>

  return (
    <div
      className="grid items-start gap-x-3 gap-y-4"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {emojiList.map((emoji) => (
        <article key={emoji.id} className="group min-w-0">
          <Card variant="flat" className="relative aspect-square">
            <button
              type="button"
              className="focus-visible:outline-ring absolute inset-0 flex cursor-pointer items-center justify-center overflow-hidden p-1 focus-visible:outline-2 focus-visible:outline-offset-2"
              onClick={() => onViewDetail(emoji)}
              aria-label={`查看表情包：${emoji.description || emoji.emoji_hash}`}
              title="详情"
            >
              {renderThumbnail ? (
                renderThumbnail(emoji)
              ) : (
                <EmojiThumbnail
                  src={getEmojiThumbnailUrl(emoji.id)}
                  alt={emoji.description || '表情包'}
                />
              )}
            </button>
            <Badge
              variant="outline"
              className="bg-background/90 pointer-events-none absolute top-1 left-1 px-1 py-0 text-[10px]"
            >
              {emoji.format.toUpperCase()}
            </Badge>
            <div className="absolute top-1 right-1 flex items-center gap-1">
              <Checkbox
                size="sm"
                checked={selectedIds.has(emoji.id)}
                onCheckedChange={() => onToggleSelect(emoji.id)}
                aria-label={`选择表情包：${emoji.description || emoji.emoji_hash}`}
                className={
                  selectedIds.has(emoji.id)
                    ? ''
                    : 'opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 focus-visible:opacity-100'
                }
              />
              <span
                role="img"
                aria-label={statusLabel[emoji.status]}
                title={statusLabel[emoji.status]}
                className={`h-1.5 w-1.5 ${emoji.status === 'adopted' ? 'bg-green-500' : emoji.status === 'discarded' ? 'bg-muted-foreground' : 'bg-orange-400'}`}
              />
            </div>
            {/* 操作始终可见，按钮独立于图片和多选框，不会误触详情或选择。 */}
            <div className="absolute inset-x-1 bottom-1 flex items-end justify-between gap-1">
              <div
                role="toolbar"
                aria-label="表情包操作"
                className="bg-background/85 text-muted-foreground/60 flex min-w-0 flex-nowrap"
              >
                <Button
                  variant="ghost"
                  size="icon-xs"
                  title="编辑"
                  aria-label="编辑"
                  onClick={() => onEdit(emoji)}
                >
                  <Edit />
                </Button>
                {emoji.status !== 'adopted' && (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    title="注册"
                    aria-label="注册"
                    onClick={() => onRegister(emoji)}
                  >
                    <CheckCircle2 />
                  </Button>
                )}
                {emoji.status !== 'discarded' && (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    title="封禁"
                    aria-label="封禁"
                    onClick={() => onBan(emoji)}
                  >
                    <Ban />
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="icon-xs"
                  title="删除"
                  aria-label="删除"
                  onClick={() => onDelete(emoji)}
                >
                  <Trash2 />
                </Button>
              </div>
              <span
                title={`使用 ${emoji.usage_count} 次`}
                className="bg-foreground text-background pointer-events-none shrink-0 px-1 text-[10px] leading-4 tabular-nums"
              >
                ×{emoji.usage_count}
              </span>
            </div>
          </Card>
          <p
            className="text-muted-foreground mt-1.5 line-clamp-2 min-h-8 text-xs leading-4 break-all"
            title={emoji.description?.trim() || '暂无描述'}
          >
            {emoji.description?.trim() || '暂无描述'}
          </p>
        </article>
      ))}
    </div>
  )
}
