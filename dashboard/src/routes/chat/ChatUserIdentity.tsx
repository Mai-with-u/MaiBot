import { Camera, Loader2, Pencil, UserCircle2 } from 'lucide-react'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useResolvedAvatarUrl } from '@/lib/avatar-url'

import { ChatNicknameSettings } from './ChatNicknameSettings'

interface ChatUserIdentityProps {
  userId: string
  userName: string
  userAvatarVersion?: number
  isUploadingUserAvatar: boolean
  onUpdateUserAvatar: (file: File) => Promise<void>
  onUpdateUserName: (name: string) => void
}

/** 输入区旁的本地用户身份卡片，集中编辑头像和昵称。 */
export function ChatUserIdentity({
  userId,
  userName,
  userAvatarVersion,
  isUploadingUserAvatar,
  onUpdateUserAvatar,
  onUpdateUserName,
}: ChatUserIdentityProps) {
  const { t } = useTranslation()
  const avatarInputRef = useRef<HTMLInputElement>(null)
  const userAvatarUrl = useResolvedAvatarUrl(
    userAvatarVersion ? 'webui' : undefined,
    userId,
    'user',
    userAvatarVersion
  )

  return (
    <div className="bg-background/70 flex w-40 min-w-0 shrink-0 items-center gap-2 rounded-xl border p-1.5">
      <div className="relative shrink-0">
        <Avatar className="ring-border/60 h-8 w-8 ring-1">
          {userAvatarUrl && (
            <AvatarImage
              src={userAvatarUrl}
              alt={t('chat.sidebar.userAvatarAlt', { name: userName })}
              className="object-cover"
            />
          )}
          <AvatarFallback className="bg-secondary text-secondary-foreground">
            <UserCircle2 className="h-4 w-4" />
          </AvatarFallback>
        </Avatar>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={t('chat.sidebar.editAvatar')}
              className="bg-primary text-primary-foreground hover:bg-primary/90 border-card absolute -right-1 -bottom-1 flex h-4 w-4 items-center justify-center rounded-full border-2 shadow-sm transition disabled:cursor-wait"
              disabled={isUploadingUserAvatar}
              onClick={() => avatarInputRef.current?.click()}
            >
              {isUploadingUserAvatar ? (
                <Loader2 className="h-2 w-2 animate-spin" />
              ) : (
                <Camera className="h-2 w-2" />
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent side="top">
            {isUploadingUserAvatar ? t('chat.sidebar.savingAvatar') : t('chat.sidebar.editAvatar')}
          </TooltipContent>
        </Tooltip>
        <input
          ref={avatarInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
          className="hidden"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0]
            event.currentTarget.value = ''
            if (file) void onUpdateUserAvatar(file)
          }}
        />
      </div>
      <ChatNicknameSettings
        userName={userName}
        onUpdateUserName={onUpdateUserName}
        trigger={
          <button
            type="button"
            aria-label={t('chat.sidebar.editName')}
            title={t('chat.sidebar.editName')}
            className="hover:bg-muted focus-visible:ring-primary flex min-w-0 flex-1 items-center gap-1 rounded-md px-1 py-1 text-left text-sm font-medium focus-visible:ring-2 focus-visible:outline-none"
          >
            <span className="min-w-0 flex-1 truncate">{userName}</span>
            <Pencil className="text-muted-foreground h-3 w-3 shrink-0" />
          </button>
        }
      />
    </div>
  )
}
