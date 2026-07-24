import { Loader2, Plus, SmilePlus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useToast } from '@/hooks/use-toast'
import {
  addUserEmoji,
  deleteUserEmoji,
  listUserEmojis,
  resolveUserEmojiUrl,
  type UserEmojiItem,
} from '@/lib/user-emoji-api'

const MAX_USER_EMOJI_BYTES = 2 * 1024 * 1024

interface DisplayUserEmoji extends UserEmojiItem {
  displayUrl: string
}

interface UserEmojiManagerProps {
  disabled: boolean
  userId: string
  onSendEmoji: (item: UserEmojiItem) => Promise<void>
}

export function UserEmojiManager({ disabled, userId, onSendEmoji }: UserEmojiManagerProps) {
  const { t } = useTranslation()
  const { toast } = useToast()
  const inputRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<DisplayUserEmoji[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [deletingId, setDeletingId] = useState<string>()
  const [sendingId, setSendingId] = useState<string>()

  const resolveDisplayItem = useCallback(async (item: UserEmojiItem) => {
    return {
      ...item,
      displayUrl: await resolveUserEmojiUrl(item),
    }
  }, [])

  const loadItems = useCallback(async () => {
    setIsLoading(true)
    try {
      const response = await listUserEmojis(userId)
      setItems(await Promise.all(response.items.map(resolveDisplayItem)))
    } catch (error) {
      console.error('加载用户表情包失败', error)
      toast({
        title: t('chat.toast.emojiLoadFailed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setIsLoading(false)
    }
  }, [resolveDisplayItem, t, toast, userId])

  useEffect(() => {
    if (open) {
      void loadItems()
    }
  }, [loadItems, open])

  const handleAdd = async (file: File) => {
    if (
      (file.type && !file.type.startsWith('image/')) ||
      file.size === 0 ||
      file.size > MAX_USER_EMOJI_BYTES
    ) {
      toast({
        title: t('chat.toast.emojiUploadFailed'),
        description: t('chat.toast.emojiUnsupportedDesc'),
        variant: 'destructive',
      })
      return
    }

    setIsUploading(true)
    try {
      const item = await addUserEmoji(userId, file)
      const displayItem = await resolveDisplayItem(item)
      setItems((current) => [displayItem, ...current])
    } catch (error) {
      console.error('添加用户表情包失败', error)
      toast({
        title: t('chat.toast.emojiUploadFailed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setIsUploading(false)
    }
  }

  const handleDelete = async (emojiId: string) => {
    setDeletingId(emojiId)
    try {
      await deleteUserEmoji(userId, emojiId)
      setItems((current) => current.filter((item) => item.id !== emojiId))
    } catch (error) {
      console.error('删除用户表情包失败', error)
      toast({
        title: t('chat.toast.emojiDeleteFailed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setDeletingId(undefined)
    }
  }

  const handleSend = async (item: UserEmojiItem) => {
    setSendingId(item.id)
    try {
      await onSendEmoji(item)
      setOpen(false)
    } catch (error) {
      console.error('发送用户表情包失败', error)
      toast({
        title: t('chat.toast.emojiSendFailed'),
        description: error instanceof Error ? error.message : undefined,
        variant: 'destructive',
      })
    } finally {
      setSendingId(undefined)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-9 w-9 shrink-0 rounded-full"
          disabled={disabled}
          aria-label={t('chat.actions.openEmojiManager')}
          title={t('chat.actions.openEmojiManager')}
        >
          <SmilePlus className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side="top"
        className="w-[min(20rem,calc(100vw-1rem))] p-3"
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-medium">{t('chat.emojiManager.title')}</p>
            <p className="text-muted-foreground text-xs">{t('chat.emojiManager.hint')}</p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 shrink-0"
            disabled={isUploading}
            onClick={() => inputRef.current?.click()}
          >
            {isUploading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="mr-1.5 h-3.5 w-3.5" />
            )}
            {t('chat.emojiManager.add')}
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            className="hidden"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0]
              event.currentTarget.value = ''
              if (file) {
                void handleAdd(file)
              }
            }}
          />
        </div>

        <ScrollArea className="h-56">
          {isLoading ? (
            <div className="text-muted-foreground flex h-52 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : items.length === 0 ? (
            <div className="text-muted-foreground flex h-52 flex-col items-center justify-center gap-2 text-center text-xs">
              <SmilePlus className="h-8 w-8 opacity-50" />
              <span>{t('chat.emojiManager.empty')}</span>
            </div>
          ) : (
            <div className="grid grid-cols-4 gap-2 pr-2">
              {items.map((item) => {
                const isSending = sendingId === item.id
                const isDeleting = deletingId === item.id
                return (
                  <div
                    key={item.id}
                    className="group bg-muted/50 relative aspect-square overflow-hidden rounded-lg border"
                  >
                    <button
                      type="button"
                      className="hover:bg-muted flex h-full w-full items-center justify-center p-1 transition"
                      disabled={Boolean(sendingId || deletingId)}
                      aria-label={t('chat.emojiManager.send')}
                      onClick={() => void handleSend(item)}
                    >
                      <img
                        src={item.displayUrl}
                        alt={t('chat.media.emoji')}
                        className="max-h-full max-w-full object-contain"
                      />
                      {isSending && (
                        <span className="bg-background/70 absolute inset-0 flex items-center justify-center">
                          <Loader2 className="h-4 w-4 animate-spin" />
                        </span>
                      )}
                    </button>
                    <button
                      type="button"
                      className="bg-destructive text-destructive-foreground absolute top-0.5 right-0.5 flex h-5 w-5 items-center justify-center rounded-full opacity-80 shadow transition sm:opacity-0 sm:group-hover:opacity-100 focus-visible:opacity-100"
                      disabled={Boolean(sendingId || deletingId)}
                      aria-label={t('chat.emojiManager.delete')}
                      onClick={() => void handleDelete(item.id)}
                    >
                      {isDeleting ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Trash2 className="h-3 w-3" />
                      )}
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </ScrollArea>
      </PopoverContent>
    </Popover>
  )
}
