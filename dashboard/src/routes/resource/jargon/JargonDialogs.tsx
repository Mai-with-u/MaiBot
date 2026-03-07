import { Hash } from 'lucide-react'
import { useEffect, useState } from 'react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

import { createJargon, updateJargon } from '@/lib/jargon-api'

import type { Jargon, JargonChatInfo, JargonCreateRequest, JargonUpdateRequest } from '@/types/jargon'

// ====================
// 信息项组件
// ====================
function InfoItem({
  icon: Icon,
  label,
  value,
  mono = false,
}: {
  icon?: typeof Hash
  label: string
  value: string | null | undefined
  mono?: boolean
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground flex items-center gap-1">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </Label>
      <div className={cn('text-sm', mono && 'font-mono', !value && 'text-muted-foreground')}>
        {value || '-'}
      </div>
    </div>
  )
}

// ====================
// 黑话详情对话框
// ====================
interface JargonDetailDialogProps {
  jargon: Jargon | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function JargonDetailDialog({
  jargon,
  open,
  onOpenChange,
}: JargonDetailDialogProps) {
  if (!jargon) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] grid grid-rows-[auto_1fr_auto] overflow-hidden">
        <DialogHeader>
          <DialogTitle>黑话详情</DialogTitle>
          <DialogDescription>查看黑话的完整信息</DialogDescription>
        </DialogHeader>

        <ScrollArea className="h-full pr-4">
          <div className="space-y-4 pb-2">
            <div className="grid grid-cols-2 gap-4">
              <InfoItem icon={Hash} label="记录ID" value={jargon.id.toString()} mono />
              <InfoItem label="使用次数" value={jargon.count.toString()} />
            </div>
            
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">内容</Label>
              <div className="text-sm p-2 bg-muted rounded break-all whitespace-pre-wrap">{jargon.content}</div>
            </div>

            {jargon.raw_content && (
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">原始内容</Label>
                <div className="text-sm p-2 bg-muted rounded break-all">
                  {(() => {
                    try {
                      const rawArray = JSON.parse(jargon.raw_content)
                      if (Array.isArray(rawArray)) {
                        return rawArray.map((item, index) => (
                          <div key={index}>
                            {index > 0 && <hr className="my-3 border-border" />}
                            <div className="whitespace-pre-wrap">{item}</div>
                          </div>
                        ))
                      }
                      return <div className="whitespace-pre-wrap">{jargon.raw_content}</div>
                    } catch {
                      return <div className="whitespace-pre-wrap">{jargon.raw_content}</div>
                    }
                  })()}
                </div>
              </div>
            )}

            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">含义</Label>
              <div className="text-sm p-2 bg-muted rounded break-all">
                {jargon.meaning ? (
                  <MarkdownRenderer content={jargon.meaning} />
                ) : (
                  '-'
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <InfoItem label="聊天" value={jargon.chat_name || jargon.chat_id} />
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">状态</Label>
                <div className="flex items-center gap-2">
                  {jargon.is_jargon === true && <Badge variant="default" className="bg-green-600">是黑话</Badge>}
                  {jargon.is_jargon === false && <Badge variant="secondary">非黑话</Badge>}
                  {jargon.is_jargon === null && <Badge variant="outline">未判定</Badge>}
                  {jargon.is_global && <Badge variant="outline" className="border-blue-500 text-blue-500">全局</Badge>}
                  {jargon.is_complete && <Badge variant="outline" className="border-purple-500 text-purple-500">推断完成</Badge>}
                </div>
              </div>
            </div>

            {jargon.inference_with_context && (
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">上下文推断结果</Label>
                <div className="p-2 bg-muted rounded break-all whitespace-pre-wrap font-mono text-xs max-h-[200px] overflow-y-auto">{jargon.inference_with_context}</div>
              </div>
            )}

            {jargon.inference_content_only && (
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">纯词条推断结果</Label>
                <div className="p-2 bg-muted rounded break-all whitespace-pre-wrap font-mono text-xs max-h-[200px] overflow-y-auto">{jargon.inference_content_only}</div>
              </div>
            )}
          </div>
        </ScrollArea>

        <DialogFooter className="flex-shrink-0">
          <Button onClick={() => onOpenChange(false)}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ====================
// 黑话创建对话框
// ====================
interface JargonCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: JargonChatInfo[]
  onSuccess: () => void
}

export function JargonCreateDialog({
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: JargonCreateDialogProps) {
  const [formData, setFormData] = useState<JargonCreateRequest>({
    content: '',
    meaning: '',
    chat_id: '',
    is_global: false,
  })
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const handleCreate = async () => {
    if (!formData.content || !formData.chat_id) {
      toast({
        title: '验证失败',
        description: '请填写必填字段：内容和聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      await createJargon(formData)
      toast({
        title: '创建成功',
        description: '黑话已创建',
      })
      setFormData({ content: '', meaning: '', chat_id: '', is_global: false })
      onSuccess()
    } catch (error) {
      toast({
        title: '创建失败',
        description: error instanceof Error ? error.message : '无法创建黑话',
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>新增黑话</DialogTitle>
          <DialogDescription>创建新的黑话记录</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="content">
              内容 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="content"
              value={formData.content}
              onChange={(e) => setFormData({ ...formData, content: e.target.value })}
              placeholder="输入黑话内容"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="meaning">含义</Label>
            <Textarea
              id="meaning"
              value={formData.meaning || ''}
 onChange={(e) => setFormData({ ...formData, meaning: e.target.value })}
              placeholder="输入黑话含义（可选）"
              rows={3}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="chat_id">
              聊天 <span className="text-destructive">*</span>
            </Label>
            <Select
              value={formData.chat_id}
              onValueChange={(value) => setFormData({ ...formData, chat_id: value })}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择关联的聊天" />
              </SelectTrigger>
              <SelectContent>
                {chatList.map((chat) => (
                  <SelectItem key={chat.chat_id} value={chat.chat_id}>
                    {chat.chat_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="is_global"
              checked={formData.is_global}
              onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
            />
            <Label htmlFor="is_global">设为全局黑话</Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={handleCreate} disabled={saving}>
            {saving ? '创建中...' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ====================
// 黑话编辑对话框
// ====================
interface JargonEditDialogProps {
  jargon: Jargon | null
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: JargonChatInfo[]
  onSuccess: () => void
}

export function JargonEditDialog({
  jargon,
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: JargonEditDialogProps) {
  const [formData, setFormData] = useState<JargonUpdateRequest>({})
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (jargon) {
      setFormData({
        content: jargon.content,
        meaning: jargon.meaning || '',
        chat_id: jargon.stream_id || jargon.chat_id,
        is_global: jargon.is_global,
        is_jargon: jargon.is_jargon,
      })
    }
  }, [jargon])

  const handleSave = async () => {
    if (!jargon) return

    try {
      setSaving(true)
      await updateJargon(jargon.id, formData)
      toast({
        title: '保存成功',
        description: '黑话已更新',
      })
      onSuccess()
    } catch (error) {
      toast({
        title: '保存失败',
        description: error instanceof Error ? error.message : '无法更新黑话',
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  if (!jargon) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑黑话</DialogTitle>
          <DialogDescription>修改黑话的信息</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="edit_content">内容</Label>
            <Input
              id="edit_content"
              value={formData.content || ''}
              onChange={(e) => setFormData({ ...formData, content: e.target.value })}
              placeholder="输入黑话内容"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="edit_meaning">含义</Label>
            <Textarea
              id="edit_meaning"
              value={formData.meaning || ''}
              onChange={(e) => setFormData({ ...formData, meaning: e.target.value })}
              placeholder="输入黑话含义"
              rows={3}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="edit_chat_id">聊天</Label>
            <Select
              value={formData.chat_id || ''}
              onValueChange={(value) => setFormData({ ...formData, chat_id: value })}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择关联的聊天" />
              </SelectTrigger>
              <SelectContent>
                {chatList.map((chat) => (
                  <SelectItem key={chat.chat_id} value={chat.chat_id}>
                    {chat.chat_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>黑话状态</Label>
            <Select
              value={formData.is_jargon === null ? 'null' : formData.is_jargon?.toString() || 'null'}
              onValueChange={(value) => setFormData({ ...formData, is_jargon: value === 'null' ? null : value === 'true' })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="null">未判定</SelectItem>
                <SelectItem value="true">是黑话</SelectItem>
                <SelectItem value="false">非黑话</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="edit_is_global"
              checked={formData.is_global}
              onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
            />
            <Label htmlFor="edit_is_global">全局黑话</Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ====================
// 删除确认对话框
// ====================
interface DeleteConfirmDialogProps {
  jargon: Jargon | null
  open: boolean
  onOpenChange: () => void
  onConfirm: () => void
}

export function DeleteConfirmDialog({
  jargon,
  open,
  onOpenChange,
  onConfirm,
}: DeleteConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确认删除</AlertDialogTitle>
          <AlertDialogDescription>
            确定要删除黑话 "{jargon?.content}" 吗？此操作不可撤销。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// ====================
// 批量删除确认对话框
// ====================
interface BatchDeleteConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
  count: number
}

export function BatchDeleteConfirmDialog({
  open,
  onOpenChange,
  onConfirm,
  count,
}: BatchDeleteConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确认批量删除</AlertDialogTitle>
          <AlertDialogDescription>
            您即将删除 {count} 个黑话，此操作无法撤销。确定要继续吗？
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
            确认删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
