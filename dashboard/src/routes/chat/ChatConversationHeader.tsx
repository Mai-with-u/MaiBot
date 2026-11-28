import { ListFilter, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { ConversationAvatar, ConversationHeader } from '@/components/ui/conversation'
import { StatusIndicator } from '@/components/ui/data-browser'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useChatTabAvatarUrl } from './local-bot-avatar'
import { LocalBotAvatarSettings } from './LocalBotAvatarSettings'

import type { ChatTab } from './types'
import { getChatTabDisplayName } from './utils'

/** 本地与虚拟会话共用标题栏；清空仅作用于当前显示的消息。 */
export function ChatConversationHeader({ tab, onClear, onObserve }: { tab: ChatTab; onClear: () => void; onObserve?: () => void }) {
  const { t } = useTranslation()
  const name = getChatTabDisplayName(tab, t('chat.botNameFallback'))
  const virtual = tab.type === 'virtual'
  const avatar = useChatTabAvatarUrl(tab)
  const connection = t(tab.isConnected ? 'chat.sidebar.online' : 'chat.sidebar.offline')
  return (
    <ConversationHeader
      title={name}
      avatar={<ConversationAvatar src={avatar} name={name} bot={!virtual} />}
      subtitle={
        <StatusIndicator variant="inline" status={tab.isConnected ? 'on' : 'off'}>
          <span className="inline-flex items-center gap-2">
            <span>{virtual ? t('chat.sidebar.virtualBadge') : '本地聊天室'}</span>
            <span>{connection}</span>
          </span>
        </StatusIndicator>
      }
      actions={
        <>
          {onObserve && <Button variant="outline" size="sm" onClick={onObserve}>观察</Button>}
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" size="icon-sm" aria-label="会话信息">
                <ListFilter />
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end">
              <p className="text-sm">{name}</p>
              <p className="text-muted-foreground mt-2 flex items-center gap-2 text-xs">
                <span className="tabular-nums">{tab.messages.length} 条消息</span>
                <span>{connection}</span>
              </p>
              {!virtual && <LocalBotAvatarSettings botQq={tab.sessionInfo.bot_qq} />}
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
