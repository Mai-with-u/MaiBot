import type { ReactNode } from 'react'
import { ArrowDownToLine, ArrowUpToLine, ChevronUp, Eraser } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ConversationHeader } from '@/components/ui/conversation'

/** 正式观察页和预览共用工具栏；导航使用图标，给记录区留出高度。 */
export function ObservationHeader({
  title, subtitle, tools, allExpanded, onToggleAll, autoScroll,
  onScrollToTop, onFindPreviousBotMessage, onScrollToBottom, onClearTimeline,
}: {
  title: ReactNode
  subtitle?: ReactNode
  tools: ReactNode
  allExpanded?: boolean
  onToggleAll?: () => void
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
      actions={
        <>
          {onToggleAll && (
            <Button variant="outline" size="sm" onClick={onToggleAll}>
              {allExpanded ? '全部收起' : '全部展开'}
            </Button>
          )}
          <Button variant="outline" size="icon-sm" aria-label="顶部" title="回到顶部" onClick={onScrollToTop}>
            <ArrowUpToLine />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="上条" title="查找上条麦麦消息" onClick={onFindPreviousBotMessage}>
            <ChevronUp />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="底部" title="回到底部" onClick={onScrollToBottom}>
            <ArrowDownToLine className={autoScroll ? 'text-primary' : undefined} />
          </Button>
          <Button variant="outline" size="icon-sm" aria-label="清空" title="清空" onClick={onClearTimeline}>
            <Eraser />
          </Button>
        </>
      }
    >
      <Button variant="outline" size="sm" aria-pressed="true">思考记录</Button>
      {tools}
    </ConversationHeader>
  )
}
