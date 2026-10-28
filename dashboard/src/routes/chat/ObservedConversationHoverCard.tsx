import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'

import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { getChatStreamDetail } from '@/lib/chat-management-api'

export function ObservedConversationHoverCard({
  sessionId,
  name,
  platform,
  targetId,
  isGroupChat,
  status,
  preview,
  children,
}: {
  sessionId: string
  name: string
  platform?: string
  targetId?: string | null
  isGroupChat?: boolean
  status: ReactNode
  preview: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const clearTimer = () => clearTimeout(timer.current)
  const schedule = (nextOpen: boolean) => {
    clearTimer()
    timer.current = setTimeout(() => setOpen(nextOpen), nextOpen ? 350 : 200)
  }
  useEffect(() => () => clearTimeout(timer.current), [])

  // 只为正在查看的会话请求详情；与设置弹窗共享缓存，打开时刷新当前生效规则。
  const detail = useQuery({
    queryKey: ['chat-stream-detail', sessionId],
    queryFn: () => getChatStreamDetail(sessionId),
    enabled: open,
    staleTime: 0,
    refetchInterval: open ? 30_000 : false,
  })

  return (
    <Popover open={open} onOpenChange={(value) => { clearTimer(); setOpen(value) }}>
      <PopoverAnchor asChild>
        <div
          data-conversation-hover-anchor="true"
          onPointerEnter={(event) => { if (event.pointerType === 'mouse') schedule(true) }}
          onPointerLeave={() => schedule(false)}
          onFocus={() => schedule(true)}
          onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) schedule(false) }}
          onClick={() => { clearTimer(); setOpen(false) }}
        >
          {children}
        </div>
      </PopoverAnchor>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={12}
        collisionPadding={12}
        aria-label={`${name}的聊天流信息`}
        className="w-80 max-w-[calc(100vw-1.5rem)] space-y-3 text-xs"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onPointerEnter={clearTimer}
        onPointerLeave={() => schedule(false)}
        onFocus={clearTimer}
        onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) schedule(false) }}
      >
        <div className="space-y-1">
          <p className="break-words text-sm font-semibold">{name}</p>
          <p className="text-muted-foreground break-all">
            {platform ? `${platform} · ` : ''}{isGroupChat ? '群聊' : '私聊'}{targetId ? ` · ${targetId}` : ''}
          </p>
          <div>{status}</div>
        </div>
        {detail.isError ? (
          <p role="alert" className="text-destructive">聊天流信息加载失败，请重新悬停重试。</p>
        ) : detail.data ? (
          <dl className="space-y-2 border-y py-3">
            {(['expression', 'jargon'] as const).map((kind) => (
              <div key={kind} className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">{kind === 'expression' ? '表达' : '黑话'}</dt>
                <dd>
                  学习{detail.data[kind].learn ? '开启' : '关闭'}
                  {' · '}使用{detail.data[kind].use ? '开启' : '关闭'}
                </dd>
              </div>
            ))}
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted-foreground">当前回复频率</dt>
              <dd className="font-medium">{detail.data.talk_frequency.effective_value_label}</dd>
            </div>
          </dl>
        ) : (
          <p role="status" className="text-muted-foreground">正在加载学习状态和回复频率…</p>
        )}
        <div className="space-y-1">
          <p className="text-muted-foreground">最近消息</p>
          <p className="line-clamp-3 break-words">{preview}</p>
        </div>
      </PopoverContent>
    </Popover>
  )
}
