import { Ban, CheckCircle2, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Edit, Info, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { EmojiThumbnail } from '@/components/emoji-thumbnail'

import { getEmojiThumbnailUrl } from '@/lib/emoji-api'
import type { Emoji } from '@/types/emoji'

interface EmojiListProps {
  emojiList: Emoji[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  selectedIds: Set<number>
  cardSize: 'small' | 'medium' | 'large'
  jumpToPage: string
  onPageChange: (page: number) => void
  onJumpToPage: () => void
  onJumpToPageChange: (value: string) => void
  onToggleSelect: (id: number) => void
  onEdit: (emoji: Emoji) => void
  onViewDetail: (emoji: Emoji) => void
  onRegister: (emoji: Emoji) => void
  onBan: (emoji: Emoji) => void
  onDelete: (emoji: Emoji) => void
}

export function EmojiList({
  emojiList,
  // loading,
  total,
  page,
  pageSize,
  selectedIds,
  cardSize,
  jumpToPage,
  onPageChange,
  onJumpToPage,
  onJumpToPageChange,
  onToggleSelect,
  onEdit,
  onViewDetail,
  onRegister,
  onBan,
  onDelete,
}: EmojiListProps) {
  // 空状态
  if (emojiList.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        暂无数据
      </div>
    )
  }

  // 卡片网格视图
  return (
    <>
      <div
        className={`grid gap-3 ${
          cardSize === 'small'
            ? 'grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10'
            : cardSize === 'medium'
              ? 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8'
              : 'grid-cols-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5'
        }`}
      >
        {emojiList.map((emoji) => (
          <div
            key={emoji.id}
            className={`group relative rounded-lg border bg-card overflow-hidden hover:ring-2 hover:ring-primary transition-all cursor-pointer ${
              selectedIds.has(emoji.id)
                ? 'ring-2 ring-primary bg-primary/5'
                : ''
            }`}
            role="button"
            tabIndex={0}
            onClick={() => onToggleSelect(emoji.id)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggleSelect(emoji.id) } }}
          >
            {/* 选中指示器 */}
            <div
              className={`absolute top-1 left-1 z-10 transition-opacity ${
                selectedIds.has(emoji.id)
                  ? 'opacity-100'
                  : 'opacity-0 group-hover:opacity-100'
              }`}
            >
              <div
                className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                  selectedIds.has(emoji.id)
                    ? 'bg-primary border-primary text-primary-foreground'
                    : 'bg-background/80 border-muted-foreground/50'
                }`}
              >
                {selectedIds.has(emoji.id) && (
                  <CheckCircle2 className="h-3 w-3" />
                )}
              </div>
            </div>

            {/* 状态标签 */}
            <div className="absolute top-1 right-1 z-10 flex flex-col gap-0.5">
              {emoji.is_registered && (
                <Badge
                  variant="default"
                  className="bg-green-600 text-[10px] px-1 py-0"
                >
                  已注册
                </Badge>
              )}
              {emoji.is_banned && (
                <Badge variant="destructive" className="text-[10px] px-1 py-0">
                  已封禁
                </Badge>
              )}
            </div>

            {/* 图片 */}
            <div
              className={`aspect-square bg-muted flex items-center justify-center overflow-hidden ${
                cardSize === 'small'
                  ? 'p-1'
                  : cardSize === 'medium'
                    ? 'p-2'
                    : 'p-3'
              }`}
            >
              <EmojiThumbnail
                src={getEmojiThumbnailUrl(emoji.id)}
                alt="表情包"
              />
            </div>

            {/* 底部信息和操作 */}
            <div
              className={`border-t bg-card ${cardSize === 'small' ? 'p-1' : 'p-2'}`}
            >
              {/* 使用次数和格式 */}
              <div className="flex items-center justify-between gap-1 text-xs text-muted-foreground mb-1">
                <Badge variant="outline" className="text-[10px] px-1 py-0">
                  {emoji.format.toUpperCase()}
                </Badge>
                <span className="font-mono">{emoji.usage_count}次</span>
              </div>

              {/* 操作按钮 - 悬停时显示 */}
              <div
                className={`flex gap-1 justify-center opacity-0 group-hover:opacity-100 transition-opacity ${
                  cardSize === 'small' ? 'flex-wrap' : ''
                }`}
              >
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={(e) => {
                    e.stopPropagation()
                    onEdit(emoji)
                  }}
                  title="编辑"
                >
                  <Edit className="h-3 w-3" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={(e) => {
                    e.stopPropagation()
                    onViewDetail(emoji)
                  }}
                  title="详情"
                >
                  <Info className="h-3 w-3" />
                </Button>
                {!emoji.is_registered && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-green-600 hover:text-green-700"
                    onClick={(e) => {
                      e.stopPropagation()
                      onRegister(emoji)
                    }}
                    title="注册"
                  >
                    <CheckCircle2 className="h-3 w-3" />
                  </Button>
                )}
                {!emoji.is_banned && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-orange-600 hover:text-orange-700"
                    onClick={(e) => {
                      e.stopPropagation()
                      onBan(emoji)
                    }}
                    title="封禁"
                  >
                    <Ban className="h-3 w-3" />
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-red-600 hover:text-red-700"
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(emoji)
                  }}
                  title="删除"
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 分页 - 增强版 */}
      {total > 0 && (
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mt-4">
          <div className="text-sm text-muted-foreground">
            显示 {(page - 1) * pageSize + 1} 到{' '}
            {Math.min(page * pageSize, total)} 条，共 {total} 条
          </div>
          <div className="flex items-center gap-2">
            {/* 首页 */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(1)}
              disabled={page === 1}
              className="hidden sm:flex"
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>

            {/* 上一页 */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(Math.max(1, page - 1))}
              disabled={page === 1}
            >
              <ChevronLeft className="h-4 w-4 sm:mr-1" />
              <span className="hidden sm:inline">上一页</span>
            </Button>

            {/* 页码跳转 */}
            <div className="flex items-center gap-2">
              <Input
                type="number"
                value={jumpToPage}
                onChange={(e) => onJumpToPageChange(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onJumpToPage()}
                placeholder={page.toString()}
                className="w-16 h-8 text-center"
                min={1}
                max={Math.ceil(total / pageSize)}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={onJumpToPage}
                disabled={!jumpToPage}
                className="h-8"
              >
                跳转
              </Button>
            </div>

            {/* 下一页 */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(page + 1)}
              disabled={page >= Math.ceil(total / pageSize)}
            >
              <span className="hidden sm:inline">下一页</span>
              <ChevronRight className="h-4 w-4 sm:ml-1" />
            </Button>

            {/* 末页 */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(Math.ceil(total / pageSize))}
              disabled={page >= Math.ceil(total / pageSize)}
              className="hidden sm:flex"
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </>
  )
}
