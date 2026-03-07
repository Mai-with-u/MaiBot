import { CheckCircle2, Circle, Clock, Hash, Info, XCircle } from 'lucide-react'
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
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

import { createExpression, updateExpression } from '@/lib/expression-api'

import type { Expression, ExpressionCreateRequest, ExpressionUpdateRequest, ChatInfo } from '@/types/expression'

/**
 * 表达方式详情对话框
 */
export function ExpressionDetailDialog({
  expression,
  open,
  onOpenChange,
  chatNameMap,
}: {
  expression: Expression | null
  open: boolean
  onOpenChange: (open: boolean) => void
  chatNameMap: Map<string, string>
}) {
  if (!expression) return null

  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN')
  }

  const getChatName = (chatId: string): string => {
    return chatNameMap.get(chatId) || chatId
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>表达方式详情</DialogTitle>
          <DialogDescription>
            查看表达方式的完整信息
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <InfoItem label="情境" value={expression.situation} />
            <InfoItem label="风格" value={expression.style} />
            <InfoItem 
              label="聊天" 
              value={getChatName(expression.chat_id)} 
            />
            <InfoItem icon={Hash} label="记录ID" value={expression.id.toString()} mono />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <InfoItem icon={Clock} label="创建时间" value={formatTime(expression.create_date)} />
          </div>

          {/* 状态标记 */}
          <div className="rounded-lg border bg-muted/50 p-4">
            <Label className="text-xs text-muted-foreground mb-3 block">状态标记</Label>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex items-center gap-2">
                <div className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-full",
                  expression.checked ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400" : "bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-600"
                )}>
                  {expression.checked ? (
                    <CheckCircle2 className="h-5 w-5" />
                  ) : (
                    <Circle className="h-5 w-5" />
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium">已检查</p>
                  <p className="text-xs text-muted-foreground">
                    {expression.checked ? "已通过审核" : "未审核"}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-full",
                  expression.rejected ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" : "bg-gray-100 text-gray-400 dark:bg-gray-800 dark:text-gray-600"
                )}>
                  {expression.rejected ? (
                    <XCircle className="h-5 w-5" />
                  ) : (
                    <Circle className="h-5 w-5" />
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium">已拒绝</p>
                  <p className="text-xs text-muted-foreground">
                    {expression.rejected ? "不会被使用" : "正常"}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 信息项组件
 */
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
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<ExpressionCreateRequest>({
    situation: '',
    style: '',
    chat_id: '',
  })
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const handleCreate = async () => {
    if (!formData.situation || !formData.style || !formData.chat_id) {
      toast({
        title: '验证失败',
        description: '请填写必填字段：情境、风格和聚天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      const result = await createExpression(formData)
      if (result.success) {
        toast({
          title: '创建成功',
          description: '表达方式已创建',
        })
        setFormData({
          situation: '',
          style: '',
          chat_id: '',
        })
        onSuccess()
      } else {
        toast({
          title: '创建失败',
          description: result.error,
          variant: 'destructive',
        })
      }
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
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>新增表达方式</DialogTitle>
          <DialogDescription>
            创建新的表达方式记录
          </DialogDescription>
        </DialogHeader>

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
                    <span className="truncate" style={{ wordBreak: 'keep-all' }}>
                      {chat.chat_name}
                      {chat.is_group && <span className="text-muted-foreground ml-1">(群聊)</span>}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleCreate} disabled={saving}>
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
        checked: expression.checked,
        rejected: expression.rejected,
      })
    }
  }, [expression])

  const handleSave = async () => {
    if (!expression) return

    try {
      setSaving(true)
      const result = await updateExpression(expression.id, formData)
      if (result.success) {
        toast({
          title: '保存成功',
          description: '表达方式已更新',
        })
        onSuccess()
      } else {
        toast({
          title: '保存失败',
          description: result.error,
          variant: 'destructive',
        })
      }
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>编辑表达方式</DialogTitle>
          <DialogDescription>
            修改表达方式的信息
          </DialogDescription>
        </DialogHeader>

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
                      {chat.chat_name}
                      {chat.is_group && <span className="text-muted-foreground ml-1">(群聊)</span>}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 状态标记 */}
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription className="text-xs">
              <div className="space-y-1">
                <p><strong>状态标记说明：</strong></p>
                <p>• 已检查：表示该表达方式已通过审核（可由AI自动检查或人工审核）</p>
                <p>• 已拒绝：表示该表达方式被标记为不合适，将永远不会被使用</p>
                <p className="text-muted-foreground mt-2">
                  根据配置中"仅使用已审核通过的表达方式"设置：<br/>
                  • 开启时：只有通过审核（已检查）的项目会被使用<br/>
                  • 关闭时：未审核的项目也会被使用
                </p>
              </div>
            </AlertDescription>
          </Alert>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex items-center justify-between space-x-2 rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label htmlFor="edit_checked" className="text-sm font-medium">
                  已检查
                </Label>
                <p className="text-xs text-muted-foreground">
                  已通过审核
                </p>
              </div>
              <Switch
                id="edit_checked"
                checked={formData.checked ?? false}
                onCheckedChange={(checked) => setFormData({ ...formData, checked })}
              />
            </div>

            <div className="flex items-center justify-between space-x-2 rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label htmlFor="edit_rejected" className="text-sm font-medium">
                  已拒绝
                </Label>
                <p className="text-xs text-muted-foreground">
                  不会被使用
                </p>
              </div>
              <Switch
                id="edit_rejected"
                checked={formData.rejected ?? false}
                onCheckedChange={(rejected) => setFormData({ ...formData, rejected })}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving}>
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
          <AlertDialogAction onClick={onConfirm} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
            确认删除
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
            确定要删除表达方式 "{expression?.situation}" 吗？
            此操作不可撤销。
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
