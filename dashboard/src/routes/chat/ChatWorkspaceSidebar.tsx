import { Bot, Check, Edit2, Plus, UserCircle2, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'

import type { ChatMessage, ChatTab } from './types'
import { getChatTabDisplayName } from './utils'

interface ChatWorkspaceSidebarProps {
  className?: string
  tabs: ChatTab[]
  activeTabId: string
  userName: string
  onSwitch: (tabId: string) => void
  onClose: (tabId: string, e?: React.MouseEvent | React.KeyboardEvent) => void
  onAddVirtual: () => void
  onUpdateUserName: (name: string) => void
}

function getMessagePreview(message: ChatMessage | undefined, fallback: string) {
  if (!message) return fallback
  if (message.type === 'system' || message.type === 'error') return message.content || fallback
  return message.content || fallback
}

function ConversationItem({
  tab,
  active,
  onSwitch,
  onClose,
}: {
  tab: ChatTab
  active: boolean
  onSwitch: (id: string) => void
  onClose: (id: string, e?: React.MouseEvent | React.KeyboardEvent) => void
}) {
  const { t } = useTranslation()
  const isVirtual = tab.type === 'virtual'
  const lastMessage = tab.messages[tab.messages.length - 1]
  const preview = getMessagePreview(
    lastMessage,
    t('chat.sidebar.emptyPreview')
  )
  const displayName = getChatTabDisplayName(tab, t('chat.botNameFallback'))
  const Icon = isVirtual ? UserCircle2 : Bot
  const avatarUrl = useResolvedAvatarUrl(
    isVirtual ? tab.virtualConfig?.platform : 'qq',
    isVirtual ? tab.virtualConfig?.userId : tab.sessionInfo.bot_qq
  )
  const avatarAlt = isVirtual
    ? `${tab.virtualConfig?.userName || tab.label} 的头像`
    : `${displayName} 的头像`

  return (
    <div
      className={cn(
        'group relative flex w-full min-w-0 items-center gap-1 rounded-xl pr-1 transition-colors',
        active
          ? 'bg-primary/12 text-foreground shadow-inner'
          : 'hover:bg-muted/70 text-foreground/90'
      )}
    >
      {active && (
        <span aria-hidden className="bg-primary absolute top-2 bottom-2 left-0 w-1 rounded-full" />
      )}
      <button
        type="button"
        className="flex w-full min-w-0 flex-1 items-center gap-3 overflow-hidden rounded-xl px-2.5 py-2 text-left"
        onClick={() => onSwitch(tab.id)}
      >
        <div className="relative shrink-0">
          <Avatar className="h-11 w-11 ring-1 ring-border/60">
            {avatarUrl && <AvatarImage src={avatarUrl} alt={avatarAlt} className="object-cover" />}
            <AvatarFallback
              className={cn(
                'text-xs',
                isVirtual
                  ? 'bg-secondary text-secondary-foreground'
                  : 'bg-primary-gradient text-primary-foreground'
              )}
            >
              <Icon className="h-5 w-5" />
            </AvatarFallback>
          </Avatar>
          <span
            aria-hidden
            className={cn(
              'border-card absolute right-0 bottom-0 h-3 w-3 rounded-full border-2 transition-colors',
              tab.isConnected ? 'bg-emerald-500' : 'bg-muted-foreground/40'
            )}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{displayName}</span>
            {isVirtual && (
              <span className="bg-secondary text-secondary-foreground shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium tracking-wide">
                {t('chat.sidebar.virtualBadge')}
              </span>
            )}
          </div>
          <p className="text-muted-foreground mt-0.5 truncate text-xs">{preview}</p>
        </div>
      </button>

      {tab.id !== 'webui-default' && (
        <button
          type="button"
          aria-label={t('chat.sidebar.closeConversation', { label: displayName })}
          className="text-muted-foreground hover:bg-background hover:text-foreground rounded-md p-1 opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
          onClick={(e) => onClose(tab.id, e)}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

export function ChatWorkspaceSidebar({
  className,
  tabs,
  activeTabId,
  userName,
  onSwitch,
  onClose,
  onAddVirtual,
  onUpdateUserName,
}: ChatWorkspaceSidebarProps) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [draftName, setDraftName] = useState(userName)

  const startEditing = () => {
    setDraftName(userName)
    setEditing(true)
  }

  const commit = () => {
    const next = draftName.trim() || t('chat.userNameFallback')
    onUpdateUserName(next)
    setEditing(false)
  }

  return (
    <aside
      className={cn(
        'bg-card/90 supports-backdrop-filter:bg-card/70 flex h-full w-72 shrink-0 flex-col border-r backdrop-blur xl:w-80',
        className
      )}
    >
      {/* 头部：标题 + 新建按钮 */}
      <div className="border-b px-4 pt-5 pb-4">
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold tracking-tight">
              {t('chat.sidebar.title')}
            </h2>
            <p className="text-muted-foreground mt-0.5 truncate text-xs">
              {t('chat.sidebar.subtitle', { count: tabs.length })}
            </p>
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label={t('chat.sidebar.newVirtual')}
                className="h-9 w-9 shrink-0 rounded-full shadow-sm"
                size="icon"
                onClick={onAddVirtual}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{t('chat.sidebar.newVirtual')}</TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* 会话列表 */}
      <ScrollArea
        className="min-h-0 flex-1"
        contentClassName="!block w-full min-w-0"
        scrollbars="vertical"
        viewportClassName="[&>div]:!block [&>div]:!min-w-0 [&>div]:w-full"
      >
        <nav aria-label={t('chat.sidebar.conversations')} className="space-y-0.5 p-2">
          {tabs.map((tab) => (
            <ConversationItem
              key={tab.id}
              active={activeTabId === tab.id}
              tab={tab}
              onSwitch={onSwitch}
              onClose={onClose}
            />
          ))}
        </nav>
      </ScrollArea>

      {/* 底部：本地用户身份 */}
      <div className="border-t p-3">
        <div className="bg-background/70 hover:bg-background flex items-center gap-3 rounded-xl border p-2.5 transition-colors">
          <Avatar className="h-10 w-10 shrink-0 ring-1 ring-border/60">
            <AvatarFallback className="bg-secondary text-secondary-foreground">
              <UserCircle2 className="h-5 w-5" />
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <p className="text-muted-foreground text-[11px] uppercase tracking-wide">
              {t('chat.sidebar.profileTitle')}
            </p>
            {editing ? (
              <div className="mt-0.5 flex items-center gap-1">
                <Input
                  autoFocus
                  className="h-7 text-sm"
                  placeholder={t('chat.identity.namePlaceholder')}
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      commit()
                    } else if (e.key === 'Escape') {
                      setEditing(false)
                    }
                  }}
                />
                <Button
                  aria-label={t('chat.sidebar.saveName')}
                  className="h-7 w-7 shrink-0"
                  size="icon"
                  variant="ghost"
                  onClick={commit}
                >
                  <Check className="h-3.5 w-3.5" />
                </Button>
              </div>
            ) : (
              <div className="flex min-w-0 items-center gap-1">
                <p className="min-w-0 flex-1 truncate text-sm font-medium">{userName}</p>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      aria-label={t('chat.sidebar.editName')}
                      className="h-6 w-6 shrink-0 opacity-60 hover:opacity-100"
                      size="icon"
                      variant="ghost"
                      onClick={startEditing}
                    >
                      <Edit2 className="h-3 w-3" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">{t('chat.sidebar.editName')}</TooltipContent>
                </Tooltip>
              </div>
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}
