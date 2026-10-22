import { useId, type HTMLAttributes, type ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { AnimatePresence, motion, useIsPresent, useReducedMotion } from 'motion/react'

import { cn } from '@/lib/utils'
import { Avatar, AvatarFallback, AvatarImage } from './avatar'
import { Badge } from './badge'
import { DataPanel } from './surface'

/** 只切换工作页内容，外框不动；同一模式内的新消息和会话更新不会重播动画。 */
export function ConversationTransition({
  mode,
  children,
}: {
  mode: 'chat' | 'observe'
  children: ReactNode
}) {
  const reducedMotion = useReducedMotion()
  const direction = mode === 'observe' ? 1 : -1
  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden" data-conversation-transition="true">
      <AnimatePresence initial={false} mode="wait" custom={direction}>
        <ConversationTransitionPage key={mode} direction={direction} reducedMotion={!!reducedMotion}>
          {children}
        </ConversationTransitionPage>
      </AnimatePresence>
    </div>
  )
}

function ConversationTransitionPage({ children, direction, reducedMotion }: {
  children: ReactNode
  direction: number
  reducedMotion: boolean
}) {
  const present = useIsPresent()
  return (
    <motion.div
      className="flex min-h-0 min-w-0 w-full flex-1 flex-col"
      inert={!present}
      initial={reducedMotion ? false : { opacity: 0, x: direction * 24, y: 4, scale: 0.992 }}
      animate={{ opacity: 1, x: 0, y: 0, scale: 1, transition: { duration: reducedMotion ? 0 : 0.26, ease: [0.22, 1, 0.36, 1] } }}
      exit="leave"
      variants={{
        leave: (nextDirection: number) => ({
          opacity: 0,
          x: reducedMotion ? 0 : -nextDirection * 12,
          scale: reducedMotion ? 1 : 0.996,
          transition: { duration: reducedMotion ? 0 : 0.1, ease: 'easeIn' },
        }),
      }}
    >
      {children}
    </motion.div>
  )
}

/** 聊天列表、消息和窗口标题共用头像；没有图片时显示名称首字。 */
export function ConversationAvatar({
  src,
  name,
  bot = false,
}: {
  src?: string
  name: string
  bot?: boolean
}) {
  const tone = Array.from(name).reduce((sum, char) => sum + char.codePointAt(0)!, 0) % 6
  return (
    <Avatar data-conversation-avatar="true" data-bot={bot} data-avatar-tone={tone}>
      {src && <AvatarImage src={src} alt={`${name} 的头像`} className="object-cover" />}
      <AvatarFallback>
        {bot ? (
          <span data-conversation-bot-mark="true" aria-hidden="true">
            <span />
            <span />
          </span>
        ) : (
          Array.from(name)[0] || '?'
        )}
      </AvatarFallback>
    </Avatar>
  )
}

/** 主选择按钮与设置/关闭操作分开，避免把按钮嵌套进按钮。 */
export function ConversationListItem({
  title,
  preview,
  status,
  avatar,
  selected,
  onSelect,
  actions,
}: {
  title: string
  preview: ReactNode
  status?: ReactNode
  avatar: ReactNode
  selected: boolean
  onSelect: () => void
  actions?: ReactNode
}) {
  return (
    <div data-conversation-item="true" data-selected={selected}>
      <button type="button" aria-pressed={selected} onClick={onSelect}>
        <span aria-hidden="true">{avatar}</span>
        <span data-conversation-item-content="true">
          <span data-conversation-item-heading="true">
            <strong>{title}</strong>
            {!actions && status}
          </span>
          <span data-conversation-item-preview="true">{preview}</span>
        </span>
      </button>
      {actions && (
        <div data-conversation-item-actions="true">
          {status && <span data-conversation-item-status="true">{status}</span>}
          {actions}
        </div>
      )}
    </div>
  )
}

export function ConversationHeader({
  title,
  subtitle,
  avatar,
  children,
  actions,
  className,
  layout = 'default',
}: {
  title: ReactNode
  subtitle?: ReactNode
  avatar?: ReactNode
  children?: ReactNode
  actions?: ReactNode
  className?: string
  layout?: 'default' | 'toolbar'
}) {
  return (
    <header data-conversation-header="true" data-layout={layout} className={className}>
      {avatar}
      <div data-conversation-heading="true">
        <div className="truncate text-base font-medium">{title}</div>
        {subtitle && <div className="text-muted-foreground mt-1 text-xs">{subtitle}</div>}
      </div>
      {children && <div data-conversation-header-tools="true">{children}</div>}
      {actions && <div data-conversation-header-actions="true">{actions}</div>}
    </header>
  )
}

export function MessageBubble({
  name,
  time,
  avatar,
  outgoing = false,
  showMeta = true,
  children,
}: {
  name: string
  time: ReactNode
  avatar?: ReactNode
  outgoing?: boolean
  showMeta?: boolean
  children: ReactNode
}) {
  return (
    <div className="chat-message-row" data-message-row="true" data-outgoing={outgoing}>
      <div data-message-avatar="true">{avatar}</div>
      <div data-message-content="true">
        {showMeta && (
          <div data-message-meta="true">
            <span>{name}</span>
            <span>·</span>
            <span>{time}</span>
          </div>
        )}
        <div data-message-bubble="true">{children}</div>
      </div>
    </div>
  )
}

export function MessageNotice({ children }: { children: ReactNode }) {
  return (
    <div data-message-notice="true">
      <span>{children}</span>
    </div>
  )
}

/** 输入和工具栏在同一块数据屏中；页面提供上传、身份和发送动作。 */
export function MessageComposerFrame({
  children,
  attachments,
  tools,
  hint,
  submit,
}: {
  children: ReactNode
  attachments?: ReactNode
  tools: ReactNode
  hint?: ReactNode
  submit: ReactNode
}) {
  return (
    <DataPanel padding="none" data-message-composer="true">
      {attachments}
      <div data-composer-input="true">{children}</div>
      <div data-composer-toolbar="true">
        <div data-composer-tools="true">{tools}</div>
        <span data-composer-hint="true">{hint}</span>
        {submit}
      </div>
    </DataPanel>
  )
}

/** 一轮思考只有一个外框，展开后每个步骤沿同一条竖线排列。 */
export function TraceRound({
  label,
  time,
  summary,
  metrics,
  active = false,
  open,
  onOpenChange,
  children,
}: {
  label: string
  time: ReactNode
  summary?: ReactNode
  metrics?: ReactNode
  active?: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  const id = useId()
  return (
    <section data-trace-round="true">
      <button
        type="button"
        data-trace-round-trigger="true"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => onOpenChange(!open)}
      >
        {open ? <ChevronDown /> : <ChevronRight />}
        <Badge variant={active ? 'success' : 'outline'}>{label}</Badge>
        <span className="shrink-0 text-xs tabular-nums">{time}</span>
        <span className="min-w-0 flex-1 truncate text-left text-xs">{summary}</span>
        <span className="shrink-0 text-xs tabular-nums">{metrics}</span>
      </button>
      {open && (
        <div id={id} data-trace-round-body="true">
          {children}
        </div>
      )}
    </section>
  )
}

export function TraceStep({
  tone = 'neutral',
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  tone?: 'success' | 'tool' | 'error' | 'neutral'
}) {
  return (
    <div data-trace-step="true" data-tone={tone} className={cn('min-w-0', className)} {...props}>
      <div data-trace-step-content="true">{children}</div>
    </div>
  )
}
