import { HelpCircle, Upload } from 'lucide-react'
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
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
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
import { formatChatDisplayName } from '@/lib/chat-display'

import { createJargon, updateJargon } from '@/lib/jargon-api'

import type {
  Jargon,
  JargonChatInfo,
  JargonCreateRequest,
  JargonUpdateRequest,
} from '@/types/jargon'

// ====================
// 黑话详情对话框
// ====================
interface JargonDetailDialogProps {
  jargon: Jargon | null
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: JargonChatInfo[]
  onChanged: (jargon: Jargon) => void
}

export function JargonDetailDialog({
  jargon,
  open,
  onOpenChange,
  chatList,
  onChanged,
}: JargonDetailDialogProps) {
  const [formData, setFormData] = useState<JargonUpdateRequest>({})
  const [saving, setSaving] = useState(false)
  const [locked, setLocked] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (jargon && open) {
      setLocked(jargon.created_by === 'MANUAL')
      setFormData({
        content: jargon.content,
        meaning: jargon.meaning || '',
        session_id: jargon.session_id,
        session_ids: jargon.session_ids?.length
          ? jargon.session_ids
          : [jargon.session_id].filter(Boolean),
        is_global: jargon.is_global,
        is_jargon: jargon.is_jargon,
      })
    }
  }, [jargon, open])

  const editText = (field: 'content' | 'meaning', value: string) => {
    const next = { ...formData, [field]: value }
    setFormData(next)
    if (next.meaning?.trim()) setLocked(true)
  }

  const locking = locked && jargon?.created_by !== 'MANUAL'
  const handleSave = async () => {
    if (!jargon) return
    if (formData.content !== undefined && !formData.content.trim()) {
      toast({
        title: '验证失败',
        description: '黑话内容不能为空',
        variant: 'destructive',
      })
      return
    }
    if (locking && !formData.meaning?.trim()) {
      toast({ title: '验证失败', description: '请先填写要锁定的含义', variant: 'destructive' })
      return
    }
    const globalWithoutSources = formData.is_global && !jargon.session_id && !jargon.session_ids?.length
    if (!globalWithoutSources && formData.session_ids?.length === 0) {
      toast({
        title: '验证失败',
        description: '请至少选择一个聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      const response = await updateJargon(jargon.id, {
        ...formData,
        ...(globalWithoutSources && !formData.session_ids?.length && { session_ids: undefined, session_id: undefined }),
        ...(locking && { created_by: 'MANUAL', is_jargon: true, meaning: formData.meaning?.trim() }),
        ...(!locked && jargon.created_by === 'MANUAL' && { created_by: 'AI' }),
      })
      if (response.data) {
        onChanged(response.data)
      }
      toast({
        title: '保存成功',
        description: '黑话已更新',
      })
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
  const status = locking ? true : formData.is_jargon

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden [--dialog-width:68rem]"
        aria-describedby={undefined}
        confirmOnEnter
      >
        <DialogHeader>
          <DialogTitle>黑话详情</DialogTitle>
        </DialogHeader>

        <DialogBody type="auto" className="min-w-0" viewportClassName="pr-5">
          <div className="grid gap-5 pb-2 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <div className="min-w-0 space-y-4">
              <p className="text-muted-foreground text-sm">使用次数 {jargon.count}</p>

              <div className="space-y-1">
                <Label htmlFor="detail_content">内容</Label>
                <Input
                  id="detail_content"
                  value={formData.content || ''}
                  onChange={(e) => editText('content', e.target.value)}
                  placeholder="输入黑话内容"
                />
              </div>

              <div className="space-y-1">
                <div className="flex items-center gap-4">
                  <Label htmlFor="detail_meaning">含义</Label>
                  <Label htmlFor="detail_lock_meaning" className="flex items-center gap-1.5 text-xs">
                    <Checkbox
                      id="detail_lock_meaning"
                      checked={locked}
                      disabled={saving || (!locked && !formData.meaning?.trim())}
                      onCheckedChange={(checked) => setLocked(checked === true)}
                    />
                    锁定含义
                  </Label>
                </div>
                <Textarea
                  id="detail_meaning"
                  value={formData.meaning || ''}
                  onChange={(e) => editText('meaning', e.target.value)}
                  placeholder="输入黑话含义"
                  rows={4}
                />
              </div>
            </div>
            <div className="min-w-0 space-y-4">
              <div className="space-y-1">
                <Label>聊天</Label>
                <MultiSelect
                  sortable={false}
                  options={chatList.map((chat) => ({
                    label: formatChatDisplayName(chat.chat_name, chat.account_id),
                    value: chat.session_id,
                  }))}
                  selected={formData.session_ids || []}
                  onChange={(values) =>
                    setFormData({ ...formData, session_ids: values, session_id: values[0] })
                  }
                  placeholder="选择关联的聊天"
                  emptyText="没有可选聊天"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-muted-foreground text-xs">状态</Label>
                <div className="flex flex-wrap items-center gap-2">
                  {status === true && (
                    <Badge variant="default" className="bg-green-600">
                      黑话
                    </Badge>
                  )}
                  {status === null && <Badge variant="outline">积累中</Badge>}
                  {status === false && <Badge variant="secondary">不是黑话</Badge>}
                  {jargon.is_legacy_empty_meaning && (
                    <Badge variant="outline">
                      <HelpCircle className="mr-1 h-3 w-3" />
                      旧数据
                    </Badge>
                  )}
                  {locked ? (
                    <Badge variant="outline">已锁定</Badge>
                  ) : (
                    <Badge variant="secondary">AI</Badge>
                  )}
                  {jargon.is_global && (
                    <Badge variant="outline" className="border-blue-500 text-blue-500">
                      全局
                    </Badge>
                  )}
                  {jargon.is_complete && (
                    <Badge variant="outline" className="border-purple-500 text-purple-500">
                      推断完成
                    </Badge>
                  )}
                </div>
              </div>

              <div className="space-y-2">
                <Label>黑话状态</Label>
                <Select
                  disabled={locking}
                  value={String(status ?? 'pending')}
                  onValueChange={(value) =>
                    setFormData({
                      ...formData,
                      is_jargon: value === 'pending' ? null : value === 'true',
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="true">黑话</SelectItem>
                    {jargon.is_jargon === null && <SelectItem value="pending">积累中</SelectItem>}
                    <SelectItem value="false">不是黑话</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex items-center space-x-2">
                <Switch
                  id="detail_is_global"
                  checked={formData.is_global}
                  onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
                />
                <Label htmlFor="detail_is_global">全局黑话</Label>
              </div>
            </div>
          </div>
        </DialogBody>

        <DialogFooter className="flex-shrink-0">
          <Button
            data-dialog-action="confirm"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? '保存中...' : '保存'}
          </Button>
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
  onSuccess: (isGlobal: boolean) => void
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
    session_ids: [],
    is_global: false,
  })
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const handleCreate = async () => {
    if (!formData.content.trim() || (!formData.is_global && !formData.session_ids?.length)) {
      toast({
        title: '验证失败',
        description: formData.is_global ? '请填写必填字段：内容' : '请填写必填字段：内容和聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      await createJargon(
        formData.is_global
          ? {
              content: formData.content,
              meaning: formData.meaning,
              is_global: true,
              session_ids: [],
            }
          : formData
      )
      toast({
        title: '创建成功',
        description: '黑话已创建',
      })
      setFormData({ content: '', meaning: '', session_ids: [], is_global: false })
      onSuccess(formData.is_global === true)
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
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>新增黑话</DialogTitle>
          <DialogDescription>创建新的黑话记录</DialogDescription>
        </DialogHeader>

        <DialogBody viewportClassName="pr-7">
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

            <div className="flex items-center gap-2">
              <Switch
                id="jargon-create-global"
                checked={formData.is_global ?? false}
                disabled={saving}
                onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
              />
              <Label htmlFor="jargon-create-global">全局（所有聊天）</Label>
            </div>
            {!formData.is_global && (
              <div className="space-y-2">
                <Label>
                  聊天 <span className="text-destructive">*</span>
                </Label>
                <MultiSelect
                  sortable={false}
                  options={chatList.map((chat) => ({
                    label: formatChatDisplayName(chat.chat_name, chat.account_id),
                    value: chat.session_id,
                  }))}
                  selected={formData.session_ids || []}
                  onChange={(values) =>
                    setFormData({ ...formData, session_ids: values, session_id: values[0] })
                  }
                  placeholder="选择关联的聊天"
                  emptyText="没有可选聊天"
                />
              </div>
            )}
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

// ====================
// 黑话导出对话框
// ====================
export type JargonExportScope = 'all' | 'selected'

interface JargonExportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedCount: number
  scope: JargonExportScope
  includeChatInfo: boolean
  exporting: boolean
  onScopeChange: (scope: JargonExportScope) => void
  onIncludeChatInfoChange: (includeChatInfo: boolean) => void
  onExport: (scope: JargonExportScope, includeChatInfo: boolean) => Promise<void>
}

export function JargonExportDialog({
  open,
  onOpenChange,
  selectedCount,
  scope,
  includeChatInfo,
  exporting,
  onScopeChange,
  onIncludeChatInfoChange,
  onExport,
}: JargonExportDialogProps) {
  const effectiveScope = selectedCount === 0 && scope === 'selected' ? 'all' : scope

  const handleExport = async () => {
    await onExport(effectiveScope, includeChatInfo)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>导出黑话</DialogTitle>
          <DialogDescription>选择导出范围和文件包含的信息</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>导出范围</Label>
              <Select
                value={effectiveScope}
                onValueChange={(value) => onScopeChange(value as JargonExportScope)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部黑话</SelectItem>
                  <SelectItem value="selected" disabled={selectedCount === 0}>
                    已选择 {selectedCount} 个
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center justify-between gap-3 rounded-md border p-3">
              <div className="space-y-0.5">
                <Label htmlFor="jargon_export_include_chat">包含聊天目标信息</Label>
                <p className="text-muted-foreground text-xs">
                  导出 platform、id、type 等目标信息，不包含聊天显示名。
                </p>
              </div>
              <Switch
                id="jargon_export_include_chat"
                checked={includeChatInfo}
                onCheckedChange={onIncludeChatInfoChange}
              />
            </div>
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={exporting}>
            取消
          </Button>
          <Button onClick={handleExport} disabled={exporting}>
            <Upload className="mr-1 h-4 w-4" />
            {exporting ? '导出中...' : '导出'}
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
