import { Check, ChevronLeft, ChevronRight } from 'lucide-react'
import { useRef } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

import { EmojiThumbnail } from '@/components/emoji-thumbnail'
import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { getEmojiThumbnailUrl } from '@/lib/emoji-api'
import { cn } from '@/lib/utils'
import type { Emoji } from '@/types/emoji'

/** 长按多久进入多选 */
const LONG_PRESS_MS = 450
const LONG_PRESS_MOVE_TOLERANCE = 10

interface MobileEmojiGridProps {
  emojiList: Emoji[]
  total: number
  page: number
  pageSize: number
  selecting: boolean
  selectedIds: Set<number>
  onPageChange: (page: number) => void
  onOpen: (emoji: Emoji) => void
  /** 长按某一张：进入多选并选中它 */
  onStartSelecting: (id: number) => void
  onToggleSelect: (id: number) => void
}

/**
 * 手机端表情包：满宽三列的图片网格。点按看详情，长按进入多选；
 * 多选时点按切换选中，右上角是选中圆点。
 */
export function MobileEmojiGrid({
  emojiList,
  total,
  page,
  pageSize,
  selecting,
  selectedIds,
  onPageChange,
  onOpen,
  onStartSelecting,
  onToggleSelect,
}: MobileEmojiGridProps) {
  const pressRef = useRef<{ id: number; x: number; y: number; timer: number; fired: boolean } | null>(
    null
  )
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const cancelPress = () => {
    if (pressRef.current) window.clearTimeout(pressRef.current.timer)
  }

  const handlePointerDown = (event: ReactPointerEvent<HTMLButtonElement>, id: number) => {
    if (selecting) return
    cancelPress()
    const press = {
      id,
      x: event.clientX,
      y: event.clientY,
      fired: false,
      timer: window.setTimeout(() => {
        press.fired = true
        onStartSelecting(id)
      }, LONG_PRESS_MS),
    }
    pressRef.current = press
  }

  const handlePointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const press = pressRef.current
    if (!press) return
    if (
      Math.abs(event.clientX - press.x) > LONG_PRESS_MOVE_TOLERANCE ||
      Math.abs(event.clientY - press.y) > LONG_PRESS_MOVE_TOLERANCE
    ) {
      cancelPress()
    }
  }

  const handleClick = (emoji: Emoji) => {
    const press = pressRef.current
    pressRef.current = null
    // 长按已经进入多选，松手产生的这次点按不再处理
    if (press?.fired && press.id === emoji.id) return
    if (selecting) onToggleSelect(emoji.id)
    else onOpen(emoji)
  }

  if (emojiList.length === 0) {
    return <div className="text-muted-foreground py-12 text-center">暂无数据</div>
  }

  return (
    <div className="space-y-4">
      <div data-mobile-emoji-grid="true" className="-mx-4 grid grid-cols-3 gap-0.5">
        {emojiList.map((emoji) => {
          const selected = selectedIds.has(emoji.id)
          return (
            <button
              key={emoji.id}
              type="button"
              data-mobile-emoji-tile="true"
              data-selected={selected ? 'true' : 'false'}
              aria-pressed={selecting ? selected : undefined}
              aria-label={emoji.description?.trim() || `表情包 ${emoji.id}`}
              onPointerDown={(event) => handlePointerDown(event, emoji.id)}
              onPointerMove={handlePointerMove}
              onPointerUp={cancelPress}
              onPointerCancel={cancelPress}
              onContextMenu={(event) => event.preventDefault()}
              onClick={() => handleClick(emoji)}
              className="bg-muted relative aspect-square overflow-hidden select-none [-webkit-touch-callout:none]"
            >
              <EmojiThumbnail src={getEmojiThumbnailUrl(emoji.id)} alt="" />
              {emoji.status !== 'adopted' && (
                <span className="absolute bottom-1.5 left-1.5 rounded-[5px] bg-black/55 px-1.5 py-px text-[11px] text-white">
                  {emoji.status === 'discarded' ? '已禁用' : '待审核'}
                </span>
              )}
              {selecting && (
                <>
                  {selected && <span aria-hidden="true" className="absolute inset-0 bg-white/35" />}
                  <span
                    aria-hidden="true"
                    className={cn(
                      'absolute top-1.5 right-1.5 flex h-[22px] w-[22px] items-center justify-center rounded-full',
                      selected
                        ? 'bg-primary text-primary-foreground shadow-[0_0_0_2px_#fff]'
                        : 'bg-black/10 shadow-[inset_0_0_0_1.5px_#fff,0_0_0_0.5px_rgb(0_0_0/0.15)]'
                    )}
                  >
                    {selected && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
                  </span>
                </>
              )}
            </button>
          )
        })}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <button
            type="button"
            aria-label="上一页"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            className={cn('flex h-11 w-11 items-center justify-center rounded-full disabled:opacity-40', MOBILE_GLASS_CLASS)}
          >
            <ChevronLeft className="h-5 w-5" strokeWidth={1.7} />
          </button>
          <span className="text-muted-foreground text-sm tabular-nums">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            aria-label="下一页"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
            className={cn('flex h-11 w-11 items-center justify-center rounded-full disabled:opacity-40', MOBILE_GLASS_CLASS)}
          >
            <ChevronRight className="h-5 w-5" strokeWidth={1.7} />
          </button>
        </div>
      )}
    </div>
  )
}
