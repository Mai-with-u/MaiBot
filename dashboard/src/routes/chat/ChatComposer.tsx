import { ImagePlus, Send, X } from 'lucide-react'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { UserEmojiItem } from '@/lib/user-emoji-api'
import { cn } from '@/lib/utils'

import type { ChatImageAttachment } from './types'
import { UserEmojiManager } from './UserEmojiManager'

interface ChatComposerProps {
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
}

/**
 * 聊天输入区：自适应高度的输入框 + 浮动发送按钮，带快捷键提示。
 */
export function ChatComposer({
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
}: ChatComposerProps) {
  const { t } = useTranslation()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (!disabled) onSend()
    }
  }

  const canSend = !disabled && (value.trim().length > 0 || images.length > 0)

  return (
    <div className="bg-card/85 supports-backdrop-filter:bg-card/65 shrink-0 border-t backdrop-blur">
      <div className="mx-auto max-w-4xl px-3 py-3 sm:px-6 sm:py-4">
        {images.length > 0 && (
          <div className="mb-2 flex gap-2 overflow-x-auto px-1 pb-1">
            {images.map((image) => (
              <div
                key={image.id}
                className="bg-muted relative h-16 w-16 shrink-0 overflow-hidden rounded-md border"
              >
                <img
                  src={image.data_url}
                  alt={image.name || t('chat.media.image')}
                  className="h-full w-full object-cover"
                />
                <button
                  type="button"
                  className="bg-background/90 text-foreground hover:bg-background absolute top-0.5 right-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full border shadow-sm"
                  title={t('chat.actions.removeImage')}
                  aria-label={t('chat.actions.removeImage')}
                  onClick={() => onRemoveImage(image.id)}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        <div
          className={cn(
            'group bg-background/80 focus-within:border-primary/60 focus-within:ring-primary/20 relative flex items-end gap-2 rounded-2xl border px-3 py-2 shadow-sm transition focus-within:ring-2',
            !isConnected && 'opacity-70'
          )}
        >
          <input
            ref={fileInputRef}
            className="hidden"
            type="file"
            accept="image/*"
            multiple
            disabled={!isConnected}
            onChange={(e) => {
              if (e.target.files) {
                onAddImages(e.target.files)
              }
              e.currentTarget.value = ''
            }}
          />
          <Button
            aria-label={t('chat.actions.addImage')}
            className="h-9 w-9 shrink-0 rounded-full"
            disabled={!isConnected}
            size="icon"
            title={t('chat.actions.addImage')}
            type="button"
            variant="ghost"
            onClick={() => fileInputRef.current?.click()}
          >
            <ImagePlus className="h-4 w-4" />
          </Button>
          <UserEmojiManager disabled={!isConnected} userId={userId} onSendEmoji={onSendEmoji} />
          <Textarea
            aria-label={t('chat.input.placeholder')}
            autoResize
            className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent px-1 py-1.5 text-sm shadow-none focus-visible:ring-0"
            disabled={!isConnected}
            maxHeight={160}
            minHeight={36}
            placeholder={isConnected ? t('chat.input.placeholder') : t('chat.input.waiting')}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <Button
            aria-label={t('chat.actions.send')}
            className={cn(
              'h-9 w-9 shrink-0 rounded-full transition',
              canSend ? 'shadow-md' : 'opacity-60'
            )}
            disabled={!canSend}
            size="icon"
            title={t('chat.actions.send')}
            onClick={onSend}
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <p className="text-muted-foreground mt-1.5 hidden px-2 text-[11px] sm:block">
          {t('chat.composer.hint')}
        </p>
      </div>
    </div>
  )
}
