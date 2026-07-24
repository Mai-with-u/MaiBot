import { Bot, Camera, Loader2, UserCircle2, X } from 'lucide-react'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'
import { cn } from '@/lib/utils'

import type { ChatTab } from './types'
import { getChatTabDisplayName } from './utils'

interface ChatTabBarProps {
  tabs: ChatTab[]
  activeTabId: string
  userId: string
  userName: string
  userAvatarVersion?: number
  isUploadingUserAvatar: boolean
  onSwitch: (tabId: string) => void
  onClose: (tabId: string, e?: React.MouseEvent | React.KeyboardEvent) => void
  onUpdateUserAvatar: (file: File) => Promise<void>
}

/**
 * 移动端横向会话切换条：在窄屏隐藏侧边栏时使用，保持与桌面端一致的视觉语言。
 */
export function ChatTabBar({
  tabs,
  activeTabId,
  userId,
  userName,
  userAvatarVersion,
  isUploadingUserAvatar,
  onSwitch,
  onClose,
  onUpdateUserAvatar,
}: ChatTabBarProps) {
  const { t } = useTranslation()
  const avatarInputRef = useRef<HTMLInputElement>(null)
  const userAvatarUrl = useResolvedAvatarUrl(
    userAvatarVersion ? 'webui' : undefined,
    userId,
    'user',
    userAvatarVersion
  )

  return (
    <div className="bg-card/85 supports-backdrop-filter:bg-card/65 shrink-0 border-b backdrop-blur">
      <div className="scrollbar-thin flex items-center gap-1 overflow-x-auto px-3 py-2">
        {tabs.map((tab) => {
          const active = activeTabId === tab.id
          const Icon = tab.type === 'virtual' ? UserCircle2 : Bot
          const displayName = getChatTabDisplayName(tab, t('chat.botNameFallback'))
          return (
            <div
              key={tab.id}
              className={cn(
                'group flex shrink-0 items-center rounded-full border text-xs transition',
                active
                  ? 'bg-primary text-primary-foreground border-transparent shadow-sm'
                  : 'bg-background/60 text-muted-foreground hover:text-foreground hover:bg-background border-transparent'
              )}
            >
              <button
                type="button"
                className="flex items-center gap-1.5 rounded-full px-3 py-1.5"
                onClick={() => onSwitch(tab.id)}
              >
                <Icon className="h-3.5 w-3.5" />
                <span className="max-w-32 truncate font-medium">{displayName}</span>
                <span
                  aria-hidden
                  className={cn(
                    'h-1.5 w-1.5 rounded-full transition-colors',
                    active
                      ? tab.isConnected
                        ? 'bg-primary-foreground'
                        : 'bg-primary-foreground/50'
                      : tab.isConnected
                        ? 'bg-emerald-500'
                        : 'bg-muted-foreground/40'
                  )}
                />
              </button>
              {tab.id !== 'webui-default' && (
                <button
                  type="button"
                  aria-label={t('chat.sidebar.closeConversation', { label: displayName })}
                  className={cn(
                    'mr-1 rounded-full p-0.5 transition',
                    active ? 'hover:bg-primary-foreground/20' : 'hover:bg-muted'
                  )}
                  onClick={(e) => onClose(tab.id, e)}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          )
        })}
        <button
          type="button"
          aria-label={t('chat.sidebar.editAvatar')}
          className="relative ml-auto shrink-0 rounded-full disabled:cursor-wait"
          disabled={isUploadingUserAvatar}
          onClick={() => avatarInputRef.current?.click()}
        >
          <Avatar className="ring-border/60 h-7 w-7 ring-1">
            {userAvatarUrl && (
              <AvatarImage
                src={userAvatarUrl}
                alt={t('chat.sidebar.userAvatarAlt', { name: userName })}
                className="object-cover"
              />
            )}
            <AvatarFallback className="bg-secondary text-secondary-foreground">
              <UserCircle2 className="h-3.5 w-3.5" />
            </AvatarFallback>
          </Avatar>
          <span className="bg-primary text-primary-foreground border-card absolute -right-0.5 -bottom-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full border">
            {isUploadingUserAvatar ? (
              <Loader2 className="h-2 w-2 animate-spin" />
            ) : (
              <Camera className="h-2 w-2" />
            )}
          </span>
        </button>
        <input
          ref={avatarInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
          className="hidden"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0]
            event.currentTarget.value = ''
            if (file) {
              void onUpdateUserAvatar(file)
            }
          }}
        />
      </div>
    </div>
  )
}
