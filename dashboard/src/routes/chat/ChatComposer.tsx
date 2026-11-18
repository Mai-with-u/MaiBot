import { ImagePlus, Paperclip, Send, X } from 'lucide-react'
import { useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { UserEmojiItem } from '@/lib/user-emoji-api'
import { MessageComposerFrame } from '@/components/ui/conversation'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

import type { ChatImageAttachment } from './types'
import { ChatUserIdentity } from './ChatUserIdentity'
import { UserEmojiManager } from './UserEmojiManager'

interface ChatComposerProps {
  files?: Array<{ file_ref: string; name: string }>
  onAddFiles?: (files: FileList) => void
  onRemoveFile?: (ref: string) => void
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onAddImages: (files: FileList) => void
  onRemoveImage: (id: string) => void
  onSendEmoji: (item: UserEmojiItem) => Promise<void>
  disabled: boolean
  images: ChatImageAttachment[]
  isConnected: boolean
  userId: string
  userName: string
  userAvatarVersion?: number
  isUploadingUserAvatar: boolean
  onUpdateUserAvatar: (file: File) => Promise<void>
  onUpdateUserName: (name: string) => void
}

/**
 * 聊天输入区：自适应高度的输入框 + 独立工具栏，带快捷键提示。
 */
export function ChatComposer({
  files = [],
  onAddFiles,
  onRemoveFile,
  value,
  onChange,
  onSend,
  onAddImages,
  onRemoveImage,
  onSendEmoji,
  disabled,
  images,
  isConnected,
  userId,
  userName,
  userAvatarVersion,
  isUploadingUserAvatar,
  onUpdateUserAvatar,
  onUpdateUserName,
}: ChatComposerProps) {
  const { t } = useTranslation()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const attachmentInputRef = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState(value)
  const composingRef = useRef(false)

  // 首页带入的文字、发送后清空等外部变化同步到输入区。
  useLayoutEffect(() => setDraft(value), [value])

  const canSend = !disabled && (draft.trim().length > 0 || images.length > 0 || files.length > 0)
  const handleSend = () => {
    if (!canSend || composingRef.current) return
    onSend()
    setDraft('')
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !composingRef.current && !e.nativeEvent.isComposing && e.keyCode !== 229) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <MessageComposerFrame
      attachments={
        images.length > 0 && (
          <div className="flex gap-2 overflow-x-auto p-3">
            {images.map((image) => (
              <div key={image.id} className="relative h-16 w-16 shrink-0">
                <img
                  src={image.data_url}
                  alt={image.name || t('chat.media.image')}
                  className="h-full w-full object-cover"
                />
                <Button
                  type="button"
                  size="icon-xs"
                  variant="outline"
                  className="absolute top-0 right-0"
                  aria-label={t('chat.actions.removeImage')}
                  onClick={() => onRemoveImage(image.id)}
                >
                  <X />
                </Button>
              </div>
            ))}
          </div>
        )
      }
      tools={
        <>
          {onAddFiles && <>
            <input ref={attachmentInputRef} className="hidden" type="file" multiple disabled={disabled}
              onChange={e => { if (e.target.files) onAddFiles(e.target.files); e.currentTarget.value = '' }} />
            <Button type="button" size="icon-sm" variant="ghost" disabled={disabled} aria-label={t('files.upload')}
              title={t('files.upload')} onClick={() => attachmentInputRef.current?.click()}><Paperclip /></Button>
            {files.map(file => <Button key={file.file_ref} type="button" variant="outline" size="sm"
              onClick={() => onRemoveFile?.(file.file_ref)} title={t('files.removeAttachment')}>
              <span className="max-w-32 truncate">{file.name}</span><X />
            </Button>)}
          </>}
          <input
            ref={fileInputRef}
            className="hidden"
            type="file"
            accept="image/*"
            multiple
            disabled={!isConnected}
            onChange={(e) => {
              if (e.target.files) onAddImages(e.target.files)
              e.currentTarget.value = ''
            }}
          />
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            disabled={!isConnected}
            aria-label={t('chat.actions.addImage')}
            title={t('chat.actions.addImage')}
            onClick={() => fileInputRef.current?.click()}
          >
            <ImagePlus />
          </Button>
          <UserEmojiManager disabled={!isConnected} userId={userId} onSendEmoji={onSendEmoji} />
          <Popover>
            <PopoverTrigger asChild>
              <Button type="button" variant="outline" size="sm">
                以「{userName}」发送
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-auto">
              <ChatUserIdentity
                userId={userId}
                userName={userName}
                userAvatarVersion={userAvatarVersion}
                isUploadingUserAvatar={isUploadingUserAvatar}
                onUpdateUserAvatar={onUpdateUserAvatar}
                onUpdateUserName={onUpdateUserName}
              />
            </PopoverContent>
          </Popover>
        </>
      }
      hint={t('chat.composer.hint')}
      submit={
        <Button
          type="button"
          size="sm"
          disabled={!canSend}
          onClick={handleSend}
          aria-label={t('chat.actions.send')}
        >
          <Send />
          {t('chat.actions.send')}
        </Button>
      }
    >
      <Textarea
        aria-label={t('chat.input.placeholder')}
        autoResize
        variant="composer"
        disabled={!isConnected}
        maxHeight={160}
        minHeight={56}
        placeholder={isConnected ? t('chat.input.placeholder') : t('chat.input.waiting')}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value)
          onChange(e.target.value)
        }}
        onCompositionStart={() => { composingRef.current = true }}
        onCompositionEnd={(e) => {
          composingRef.current = false
          setDraft(e.currentTarget.value)
          onChange(e.currentTarget.value)
        }}
        onKeyDown={handleKeyDown}
      />
    </MessageComposerFrame>
  )
}
