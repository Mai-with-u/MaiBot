import { useQuery } from '@tanstack/react-query'
import { Check, MessagesSquare } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConversationAvatar, ConversationListItem } from '@/components/ui/conversation'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useHostRuntime } from '@/host/state'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { getAllChatStreams, type ChatStream } from '@/lib/chat-management-api'
import type { FieldHookComponentProps } from '@/lib/field-hooks'

function targetLabel(stream: ChatStream) {
  const kind = stream.chat_type === 'group' ? '群号' : '账号'
  const platform = stream.platform === 'qq' ? `QQ ${kind}` : `${stream.platform} ${kind}`
  return `${platform} ${stream.target_id}${stream.account_id ? ` · Bot ${stream.account_id}` : ''}${stream.scope ? ` · ${stream.scope}` : ''}`
}

function SessionAvatar({ stream }: { stream: ChatStream }) {
  const avatar = useResolvedAvatarUrl(stream.platform, stream.target_id,
    stream.chat_type === 'group' ? 'group' : 'user', undefined,
    { accountId: stream.account_id, scope: stream.scope })
  return <ConversationAvatar src={avatar} name={stream.display_name} />
}

export function DesktopPetConversationField({ value, onChange }: FieldHookComponentProps) {
  const selected = typeof value === 'string' ? value : ''
  const { selected_bot_id } = useHostRuntime()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(selected)
  const [search, setSearch] = useState('')
  const sessions = useQuery({
    queryKey: ['desktop-pet', 'conversations', selected_bot_id],
    queryFn: getAllChatStreams,
    enabled: open || Boolean(selected),
    select: items => items.filter(item => item.target_id &&
      !['internal', 'bot_console', 'maisaka_cli'].includes(item.platform)),
  })
  const selectedStream = sessions.data?.find(item => item.session_id === selected)
  const keyword = search.trim().toLowerCase()
  const filtered = (sessions.data ?? []).filter(item =>
    `${item.display_name} ${targetLabel(item)}`.toLowerCase().includes(keyword))
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">聊天会话</div>
      <Dialog open={open} onOpenChange={next => {
        if (next) { setDraft(selected); setSearch('') }
        setOpen(next)
      }}>
        <DialogTrigger asChild>
          <Button type="button" variant="outline" aria-label="选择聊天会话" disabled={!onChange}
            className="h-auto min-h-10 max-w-full justify-start whitespace-normal text-left">
            {selectedStream ? <SessionAvatar stream={selectedStream} /> : <MessagesSquare />}
            <span className="min-w-0 break-all">
              {selectedStream?.display_name ?? (selected ? (sessions.isPending ? '正在加载会话…' : '所选会话不可用') : '主人私聊')}
              {selectedStream && <small className="text-muted-foreground block">{targetLabel(selectedStream)}</small>}
            </span>
          </Button>
        </DialogTrigger>
        <DialogContent aria-describedby={undefined}>
          <DialogHeader><DialogTitle>选择聊天会话</DialogTitle></DialogHeader>
          <Input aria-label="搜索名称或号码" placeholder="搜索名称或号码" value={search} onChange={event => setSearch(event.target.value)} />
          <div className="max-h-[min(45dvh,22rem)] min-h-0 overflow-auto">
            {!keyword && <ConversationListItem title="主人私聊" preview="" selected={!draft}
              avatar={<ConversationAvatar name="主人私聊" bot />} onSelect={() => setDraft('')}
              status={!draft ? <Check className="size-4" /> : undefined} />}
            {filtered.map(item => <ConversationListItem key={item.session_id} title={item.display_name} preview={targetLabel(item)}
              selected={draft === item.session_id} avatar={<SessionAvatar stream={item} />}
              onSelect={() => setDraft(item.session_id)} status={draft === item.session_id ? <Check className="size-4" /> : undefined} />)}
            {sessions.isPending && <p role="status" className="p-2 text-sm">正在加载会话…</p>}
            {sessions.isError && <Button type="button" variant="ghost" onClick={() => void sessions.refetch()}>读取失败，重试</Button>}
            {sessions.isSuccess && !filtered.length && <p className="text-muted-foreground p-2 text-sm">没有匹配的会话</p>}
          </div>
          {draft && draft !== selected && <p role="status" className="text-muted-foreground text-sm">Bot 的回复可能发送到所选聊天。</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button type="button" disabled={draft === selected || (Boolean(draft) && !sessions.data?.some(item => item.session_id === draft))}
              onClick={() => { onChange?.(draft); setOpen(false) }}>确认绑定</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
