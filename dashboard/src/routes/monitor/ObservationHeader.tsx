import type { ReactNode } from 'react'
import { ArrowDownToLine, ArrowUpToLine, ChevronUp, Eraser, Pencil } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { PixelIcon } from '@/components/ui/pixel-icon'
import { ConversationHeader } from '@/components/ui/conversation'

/** 正式观察页和预览共用工具栏；导航使用图标，给记录区留出高度。 */
export function ObservationHeader({
  title, subtitle, tools, allExpanded, onToggleAll, autoScroll, onEditChat,
  onScrollToTop, onFindPreviousBotMessage, onScrollToBottom, onClearTimeline,
}: {
  title: ReactNode
  subtitle?: ReactNode
  tools: ReactNode
  allExpanded?: boolean
  onToggleAll?: () => void
  onEditChat?: () => void
  autoScroll: boolean
  onScrollToTop: () => void
  onFindPreviousBotMessage: () => void
  onScrollToBottom: () => void
  onClearTimeline: () => void
}) {
  return (
    <ConversationHeader
      title={title}
      subtitle={subtitle}
      layout="toolbar"
      className="observation-toolbar"
      actions={
        <>
          <Separator variant="groove" orientation="vertical" className="hidden h-[18px] me-1" />
          {onEditChat && (
            <Button variant="outline" size="icon-sm" aria-label="编辑聊天流" title="编辑聊天流" onClick={onEditChat}>
              <PixelIcon name="edit" />
              <Pencil />
            </Button>
          )}
          {onToggleAll && (
            <Button variant="outline" size="sm" onClick={onToggleAll}>
              {allExpanded ? '全部收起' : '全部展开'}
            </Button>
          )}
          <Button variant="outline" size="icon-sm" aria-label="顶部" title="回到顶部" onClick={onScrollToTop}>
            <PixelIcon name="top" />
            <ArrowUpToLine />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="上条" title="查找上条麦麦消息" onClick={onFindPreviousBotMessage}>
            <PixelIcon name="previous" />
            <ChevronUp />
          </Button>
          <Button variant="outline" size="icon-sm" data-auto-scroll={autoScroll} aria-label="底部" title="回到底部" onClick={onScrollToBottom}>
            <PixelIcon name="bottom" />
            <ArrowDownToLine className={autoScroll ? 'text-primary' : undefined} />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="清空" title="清空" onClick={onClearTimeline}>
            <PixelIcon name="clear" />
            <Eraser />
          </Button>
          <Separator variant="groove" className="absolute inset-x-0 bottom-0 hidden" />
        </>
      }
    >
      {tools}
      <Separator variant="groove" className="absolute inset-x-0 bottom-0 hidden" />
    </ConversationHeader>
  )
}
