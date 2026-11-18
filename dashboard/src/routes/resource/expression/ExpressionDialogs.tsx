import { Info } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Alert, AlertDescription } from '@/components/ui/alert'
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
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'

import { createExpression, updateExpression } from '@/lib/expression-api'

import type { ChatInfo, Expression, ExpressionCreateRequest, ExpressionUpdateRequest } from '@/types/expression'

/**
 * 表达方式创建对话框
 */
export function ExpressionCreateDialog({
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: ChatInfo[]
  onSuccess: (isGlobal: boolean) => void
}) {
  const [formData, setFormData] = useState<ExpressionCreateRequest>({
    situation: '',
    style: '',
    chat_id: '',
  })
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const handleCreate = async () => {
    if (!formData.situation.trim() || !formData.style.trim() || (!formData.is_global && !formData.chat_id)) {
      toast({
        title: '验证失败',
        description: formData.is_global ? '请填写必填字段：情境和风格' : '请填写必填字段：情境、风格和聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      await createExpression(formData.is_global ? {
        situation: formData.situation, style: formData.style, is_global: true,
      } : formData)
      toast({
        title: '创建成功',
        description: '表达方式已创建',
      })
      setFormData({
        situation: '',
        style: '',
        chat_id: '',
      })
      onSuccess(formData.is_global === true)
    } catch (error) {
      toast({
        title: '创建失败',
        description: error instanceof Error ? error.message : '无法创建表达方式',
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>新增表达方式</DialogTitle>
          <DialogDescription>创建新的表达方式记录</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="situation">
                  情境 <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="situation"
                  value={formData.situation}
                  onChange={(e) => setFormData({ ...formData, situation: e.target.value })}
                  placeholder="描述使用场景"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="style">
                  风格 <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="style"
                  value={formData.style}
                  onChange={(e) => setFormData({ ...formData, style: e.target.value })}
                  placeholder="描述表达风格"
                />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Switch id="expression-create-global" checked={formData.is_global ?? false} disabled={saving}
                onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })} />
              <Label htmlFor="expression-create-global">全局（所有聊天）</Label>
            </div>
            {!formData.is_global && <div className="space-y-2">
              <Label htmlFor="chat_id">
                聊天 <span className="text-destructive">*</span>
              </Label>
              <Select
                value={formData.chat_id}
                onValueChange={(value) => setFormData({ ...formData, chat_id: value })}
              >
                <SelectTrigger id="chat_id">
                  <SelectValue placeholder="选择关联的聊天" />
                </SelectTrigger>
                <SelectContent>
                  {chatList.map((chat) => (
                    <SelectItem key={chat.chat_id} value={chat.chat_id}>
                      <span className="truncate" style={{ wordBreak: 'keep-all' }}>
                        {formatChatDisplayName(chat.chat_name, chat.account_id)}
                        {chat.is_group && (
                          <span className="text-muted-foreground ml-1">(群聊)</span>
                        )}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>}
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button data-dialog-action="confirm" onClick={handleCreate} disabled={saving}>
            {saving ? '创建中...' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 表达方式编辑对话框
 */
export function ExpressionEditDialog({
  expression,
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: {
  expression: Expression | null
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: ChatInfo[]
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<ExpressionUpdateRequest>({})
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (expression) {
      setFormData({
        situation: expression.situation,
        style: expression.style,
        chat_id: expression.chat_id,
      })
    }
  }, [expression])

  const handleSave = async () => {
    if (!expression) return

    try {
      setSaving(true)
      await updateExpression(expression.id, formData)
      toast({
        title: '保存成功',
        description: '表达方式已更新',
      })
      onSuccess()
    } catch (error) {
      toast({
        title: '保存失败',
        description: error instanceof Error ? error.message : '无法更新表达方式',
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  if (!expression) return null

  const createdAt = expression.create_date
    ? new Date(expression.create_date * 1000).toLocaleString('zh-CN')
    : '-'
  const isCurated = expression.checked && expression.modified_by?.toLowerCase() === 'user'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>编辑表达方式</DialogTitle>
          <DialogDescription>修改表达方式的信息</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="edit_situation">情境</Label>
                <Input
                  id="edit_situation"
                  value={formData.situation || ''}
                  onChange={(e) => setFormData({ ...formData, situation: e.target.value })}
                  placeholder="描述使用场景"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit_style">风格</Label>
                <Input
                  id="edit_style"
                  value={formData.style || ''}
                  onChange={(e) => setFormData({ ...formData, style: e.target.value })}
                  placeholder="描述表达风格"
                />
              </div>
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
                      <span className="truncate" style={{ wordBreak: 'keep-all' }}>
                        {formatChatDisplayName(chat.chat_name, chat.account_id)}
                        {chat.is_group && (
                          <span className="text-muted-foreground ml-1">(群聊)</span>
                        )}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="space-y-1">
                <Label className="text-muted-foreground text-xs">记录 ID</Label>
                <div className="font-mono">{expression.id}</div>
              </div>
              <div className="space-y-1">
                <Label className="text-muted-foreground text-xs">创建时间</Label>
                <div>{createdAt}</div>
              </div>
            </div>

            {/* 状态标记 */}
            <Alert>
              <Info className="h-4 w-4" />
              <AlertDescription className="text-xs">
                <div className="space-y-1">
                  <p>
                    <strong>精选状态说明：</strong>
                  </p>
                  <p>• 已人工精选：表示该表达方式已由人工确认可使用</p>
                  <p className="text-muted-foreground mt-2">
                    根据配置中“仅使用精选表达”设置：
                    <br />
                    • 开启时：只有人工精选的项目会被使用
                    <br />• 关闭时：未精选的项目也会被使用
                  </p>
                </div>
              </AlertDescription>
            </Alert>

            <div className="grid grid-cols-2 gap-4">
              <div className="flex items-center justify-between space-x-2 rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label className="text-sm font-medium">精选状态</Label>
                  <p className="text-muted-foreground text-xs">
                    {isCurated ? '已人工精选' : '未人工精选'}
                  </p>
                </div>
              </div>
            </div>
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

/**
 * 批量删除确认对话框
 */
export function BatchDeleteConfirmDialog({
  open,
  onOpenChange,
  onConfirm,
  count,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
  count: number
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确认批量删除</AlertDialogTitle>
          <AlertDialogDescription>
            您即将删除 {count} 个表达方式，此操作无法撤销。确定要继续吗？
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            确认删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/**
 * 清除单个聊天全部表达方式确认对话框
 */
export function ClearChatExpressionsConfirmDialog({
  open,
  onOpenChange,
  chatName,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  chatName: string
  onConfirm: () => Promise<void>
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确认清除表达方式</AlertDialogTitle>
          <AlertDialogDescription>
            即将清除“{chatName || '当前聊天'}”下的全部表达方式。此操作无法撤销，建议先导出备份。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            确认清除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/**
 * 单个删除确认对话框
 */
export function DeleteConfirmDialog({
  expression,
  open,
  onOpenChange,
  onConfirm,
}: {
  expression: Expression | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => Promise<void>
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>确认删除</AlertDialogTitle>
          <AlertDialogDescription>
            确定要删除表达方式 "{expression?.situation}" 吗？ 此操作不可撤销。
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
