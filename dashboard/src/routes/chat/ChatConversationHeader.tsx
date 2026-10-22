import { ListFilter, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { ConversationAvatar, ConversationHeader } from '@/components/ui/conversation'
import { StatusIndicator } from '@/components/ui/data-browser'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'

import type { ChatTab } from './types'
import { getChatTabDisplayName } from './utils'

/** 本地与虚拟会话共用标题栏；清空仅作用于当前显示的消息。 */
export function ChatConversationHeader({ tab, onClear }: { tab: ChatTab; onClear: () => void }) {
  const { t } = useTranslation()
  const name = getChatTabDisplayName(tab, t('chat.botNameFallback'))
  const virtual = tab.type === 'virtual'
  const avatar = useResolvedAvatarUrl(
    virtual ? tab.virtualConfig?.platform : 'qq',
    virtual ? tab.virtualConfig?.userId : tab.sessionInfo.bot_qq
  )
  const connection = t(tab.isConnected ? 'chat.sidebar.online' : 'chat.sidebar.offline')
  return (
    <ConversationHeader
      title={name}
      avatar={<ConversationAvatar src={avatar} name={name} bot={!virtual} />}
      subtitle={
        <StatusIndicator variant="inline" status={tab.isConnected ? 'on' : 'off'}>
          {virtual ? t('chat.sidebar.virtualBadge') : '本地聊天室'} · {connection}
        </StatusIndicator>
      }
      actions={
        <>
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" size="icon-sm" aria-label="会话信息">
                <ListFilter />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end">
              <p className="text-sm">{name}</p>
              <p className="text-muted-foreground mt-2 text-xs">
                {tab.messages.length} 条消息 · {connection}
              </p>
            </PopoverContent>
          </Popover>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="清空当前显示"
            title="清空当前显示，保留历史记录"
            onClick={onClear}
          >
            <Trash2 />
          </Button>
        </>
      }
    />
  )
}
