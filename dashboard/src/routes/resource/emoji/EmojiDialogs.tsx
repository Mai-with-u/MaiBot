import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Markdown } from '@/components/ui/markdown'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

import { useToast } from '@/hooks/use-toast'
import {
  getEmojiOriginalUrl,
  updateEmoji,
} from '@/lib/emoji-api'
import type { Emoji, EmojiStatus, EmojiUpdateRequest } from '@/types/emoji'


const emojiStatusLabel: Record<EmojiStatus, string> = {
  known: '认识',
  unknown: '不认识',
  adopted: '据为己用',
  discarded: '丢弃',
}

function getEmojiStatusVariant(status: EmojiStatus) {
  if (status === 'discarded') return 'destructive' as const
  if (status === 'unknown') return 'secondary' as const
  return status === 'adopted' ? 'default' as const : 'outline' as const
}

// ============================
// 详情对话框组件
// ============================
export function EmojiDetailDialog({
  emoji,
  emotionTags,
  contentTags,
  open,
  onOpenChange,
  actions,
}: {
  emoji: Emoji | null
  emotionTags?: string[] | null
  contentTags?: string[] | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 详情底部的操作按钮；手机上表情包卡片不再带操作栏，由这里提供 */
  actions?: ReactNode
}) {
  if (!emoji) return null
  const displayedEmotion = emotionTags?.join('、') ?? emoji.emotion

  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh]">
        <DialogHeader>
          <DialogTitle>表情包详情</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {/* 表情包预览图 - 使用原图 */}
            <div className="flex justify-center">
              <div className="w-32 h-32 bg-muted rounded-lg flex items-center justify-center overflow-hidden">
                <img
                  src={getEmojiOriginalUrl(emoji.id)}
                  alt={emoji.description || '表情包'}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    const target = e.target as HTMLImageElement
                    target.style.display = 'none'
                    const parent = target.parentElement
                    if (parent) {
                      parent.innerHTML =
                        '<svg class="h-16 w-16 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>'
                    }
                  }}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label className="text-muted-foreground">ID</Label>
                <div className="mt-1 font-mono">{emoji.id}</div>
              </div>
              <div>
                <Label className="text-muted-foreground">格式</Label>
                <div className="mt-1">
                  <Badge variant="outline">{emoji.format.toUpperCase()}</Badge>
                </div>
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">文件路径</Label>
              <div className="mt-1 font-mono text-sm break-all bg-muted p-2 rounded">
                {emoji.full_path}
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">哈希值</Label>
              <div className="mt-1 font-mono text-sm break-all bg-muted p-2 rounded">
                {emoji.emoji_hash}
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">描述</Label>
              {emoji.description ? (
                <div className="mt-1 rounded-lg border bg-muted/50 p-3">
                  <Markdown className="prose-sm">{emoji.description}</Markdown>
                </div>
              ) : (
                <div className="mt-1 text-sm text-muted-foreground">-</div>
              )}
            </div>

            <div>
              <Label className="text-muted-foreground">情绪</Label>
              <div className="mt-1">
                {displayedEmotion ? (
                  <span className="text-sm">{displayedEmotion}</span>
                ) : (
                  <span className="text-sm text-muted-foreground">-</span>
                )}
              </div>
            </div>

            {contentTags?.length ? (
              <div>
                <Label className="text-muted-foreground">内容标签</Label>
                <div className="mt-1 text-sm">{contentTags.join('、')}</div>
              </div>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label className="text-muted-foreground">状态</Label>
                <div className="mt-2 flex gap-2">
                  <Badge variant={getEmojiStatusVariant(emoji.status)}>
                    {emojiStatusLabel[emoji.status]}
                  </Badge>
                  {emoji.is_locked && <Badge variant="outline">已锁定</Badge>}
                </div>
              </div>
              <div>
                <Label className="text-muted-foreground">使用次数</Label>
                <div className="mt-1 font-mono text-lg">
                  {emoji.usage_count}
                </div>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label className="text-muted-foreground">记录时间</Label>
                <div className="mt-1 text-sm">
                  {formatTime(emoji.record_time)}
                </div>
              </div>
              <div>
                <Label className="text-muted-foreground">注册时间</Label>
                <div className="mt-1 text-sm">
                  {formatTime(emoji.register_time)}
                </div>
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">最后使用</Label>
              <div className="mt-1 text-sm">
                {formatTime(emoji.last_used_time)}
              </div>
            </div>
          </div>
        </DialogBody>
        {actions && <DialogFooter>{actions}</DialogFooter>}
      </DialogContent>
    </Dialog>
  )
}

// ============================
// 编辑对话框组件
// ============================
export function EmojiEditDialog({
  emoji,
  open,
  onOpenChange,
  onSuccess,
}: {
  emoji: Emoji | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const [emotionInput, setEmotionInput] = useState('')
  const [locked, setLocked] = useState(false)
  const [status, setStatus] = useState<EmojiStatus>('known')
  const [saving, setSaving] = useState(false)

  const { toast } = useToast()

  useEffect(() => {
    if (emoji) {
      setEmotionInput(emoji.emotion || '')
      setStatus(emoji.status)
      setLocked(emoji.is_locked)
    }
  }, [emoji, open])

  const handleSave = async () => {
    if (!emoji) return

    try {
      setSaving(true)
      // 将输入的标签字符串标准化为逗号分隔格式
      const emotionString = emotionInput
        .split(/[,,]/)
        .map((s) => s.trim())
        .filter(Boolean)
        .join(',')
      const finalEmotion = status === 'unknown' ? '' : emotionString

      const changes: EmojiUpdateRequest = {}
      if (locked !== emoji.is_locked) changes.is_locked = locked
      const onlyLockChanged = locked !== emoji.is_locked && status === emoji.status && emotionInput === (emoji.emotion || '')
      if (!onlyLockChanged) {
        changes.emotion = finalEmotion
        changes.is_registered = status === 'adopted'
        changes.is_banned = status === 'discarded'
      }
      await updateEmoji(emoji.id, changes)

      toast({
        title: '成功',
        description: '表情包信息已更新',
      })
      onOpenChange(false)
      onSuccess()
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  if (!emoji) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>编辑表情包</DialogTitle>
          <DialogDescription>修改表情包的情绪和状态信息</DialogDescription>
        </DialogHeader>
        <DialogBody>
        <div className="space-y-4">
          <div>
            <Label>情绪</Label>
            <Textarea
              value={emotionInput}
              onChange={(e) => setEmotionInput(e.target.value)}
              placeholder="输入情绪描述..."
              rows={2}
              className="mt-1"
            />
            <p className="text-xs text-muted-foreground mt-1">
              输入情绪相关的文本描述
            </p>
          </div>

          <div className="space-y-2">
            <Label>表情包状态</Label>
            <Select value={status} onValueChange={(value) => setStatus(value as EmojiStatus)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="known">认识</SelectItem>
                <SelectItem value="unknown">不认识</SelectItem>
                <SelectItem value="adopted">据为己用</SelectItem>
                <SelectItem value="discarded">丢弃</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Label className="flex items-center gap-2">
            <Checkbox checked={locked} onCheckedChange={(value) => setLocked(value === true)} />
            锁定表情包，防止自动替换
          </Label>
        </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button data-dialog-action="confirm" onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export { EmojiUploadDialog } from './EmojiUploadDialog'
