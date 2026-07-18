import { Check, Hash, HelpCircle, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ChangeEvent } from 'react'

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
import { cn } from '@/lib/utils'

import { createJargon, importJargons, updateJargon } from '@/lib/jargon-api'

import type {
  Jargon,
  JargonChatInfo,
  JargonCreateRequest,
  JargonExportItem,
  JargonUpdateRequest,
} from '@/types/jargon'

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
      <Label className="text-muted-foreground flex items-center gap-1 text-xs">
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
  const [pinning, setPinning] = useState(false)
  const [unpinning, setUnpinning] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (jargon && open) {
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
    if (formData.session_ids && formData.session_ids.length === 0) {
      toast({
        title: '验证失败',
        description: '请至少选择一个聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      const response = await updateJargon(jargon.id, formData)
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

  const handlePinMeaning = async () => {
    if (!jargon) return
    const meaning = (formData.meaning ?? jargon.meaning ?? '').trim()
    if (!meaning) {
      toast({
        title: '无法固定',
        description: '当前黑话还没有含义，不能固定为手动记录',
        variant: 'destructive',
      })
      return
    }

    try {
      setPinning(true)
      const response = await updateJargon(jargon.id, {
        ...formData,
        meaning,
        created_by: 'MANUAL',
        is_jargon: true,
      })
      if (response.data) {
        onChanged(response.data)
      }
      toast({
        title: '已固定含义',
        description: '这条黑话已标记为手动记录，后续 AI 学习不会再覆盖它',
      })
    } catch (error) {
      toast({
        title: '固定失败',
        description: error instanceof Error ? error.message : '无法固定黑话含义',
        variant: 'destructive',
      })
    } finally {
      setPinning(false)
    }
  }

  const handleUnpinMeaning = async () => {
    if (!jargon) return

    try {
      setUnpinning(true)
      const response = await updateJargon(jargon.id, {
        ...formData,
        created_by: 'AI',
      })
      if (response.data) {
        onChanged(response.data)
      }
      toast({
        title: '已取消固定',
        description: '这条黑话已恢复为 AI 学习记录',
      })
    } catch (error) {
      toast({
        title: '取消固定失败',
        description: error instanceof Error ? error.message : '无法取消固定黑话含义',
        variant: 'destructive',
      })
    } finally {
      setUnpinning(false)
    }
  }

  if (!jargon) return null

  const canPinMeaning =
    jargon.created_by !== 'MANUAL' && Boolean((formData.meaning ?? jargon.meaning ?? '').trim())

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="grid max-h-[80vh] max-w-2xl grid-rows-[auto_1fr_auto] overflow-hidden"
        confirmOnEnter
      >
        <DialogHeader>
          <DialogTitle>黑话详情</DialogTitle>
          <DialogDescription>查看并修改黑话信息</DialogDescription>
        </DialogHeader>

        <DialogBody className="h-full">
          <div className="space-y-4 pb-2">
            <div className="grid grid-cols-2 gap-4">
              <InfoItem icon={Hash} label="记录ID" value={jargon.id.toString()} mono />
              <InfoItem label="使用次数" value={jargon.count.toString()} />
            </div>

            <div className="space-y-1">
              <Label htmlFor="detail_content">内容</Label>
              <Input
                id="detail_content"
                value={formData.content || ''}
                onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                placeholder="输入黑话内容"
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="detail_meaning">含义</Label>
              <Textarea
                id="detail_meaning"
                value={formData.meaning || ''}
                onChange={(e) => setFormData({ ...formData, meaning: e.target.value })}
                placeholder="输入黑话含义"
                rows={4}
              />
            </div>

            <div className="space-y-4">
              <div className="space-y-1">
                <Label>聊天</Label>
                <MultiSelect
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
                <div className="flex items-center gap-2">
                  {formData.is_jargon === true && (
                    <Badge variant="default" className="bg-green-600">
                      是黑话
                    </Badge>
                  )}
                  {formData.is_jargon !== true && <Badge variant="secondary">无黑话</Badge>}
                  {jargon.is_legacy_empty_meaning && (
                    <Badge variant="outline">
                      <HelpCircle className="mr-1 h-3 w-3" />
                      旧数据
                    </Badge>
                  )}
                  {jargon.created_by === 'MANUAL' ? (
                    <Badge variant="outline">手动</Badge>
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
            </div>

            <div className="space-y-2">
              <Label>黑话状态</Label>
              <Select
                value={formData.is_jargon ? 'true' : 'false'}
                onValueChange={(value) =>
                  setFormData({
                    ...formData,
                    is_jargon: value === 'true',
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">是黑话</SelectItem>
                  <SelectItem value="false">无黑话</SelectItem>
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
        </DialogBody>

        <DialogFooter className="flex-shrink-0">
          {jargon.created_by === 'MANUAL' ? (
            <Button
              variant="outline"
              onClick={handleUnpinMeaning}
              disabled={saving || unpinning}
              title="恢复为 AI 学习记录，后续可由学习流程更新"
            >
              {unpinning ? '取消中...' : '取消固定'}
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={handlePinMeaning}
              disabled={saving || pinning || !canPinMeaning}
              title={canPinMeaning ? '固定当前含义，后续不再由 AI 更新' : '当前黑话还没有含义'}
            >
              <Check className="mr-1 h-4 w-4" />
              {pinning ? '固定中...' : '固定含义'}
            </Button>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          <Button
            data-dialog-action="confirm"
            onClick={handleSave}
            disabled={saving || pinning || unpinning}
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
    session_ids: [],
    is_global: false,
  })
  const [saving, setSaving] = useState(false)
  const { toast } = useToast()

  const handleCreate = async () => {
    if (!formData.content || !formData.session_ids?.length) {
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
      setFormData({ content: '', meaning: '', session_ids: [], is_global: false })
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
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>新增黑话</DialogTitle>
          <DialogDescription>创建新的黑话记录</DialogDescription>
        </DialogHeader>

        <DialogBody>
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
              <Label>
                聊天 <span className="text-destructive">*</span>
              </Label>
              <MultiSelect
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

            <div className="flex items-center space-x-2">
              <Switch
                id="is_global"
                checked={formData.is_global}
                onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
              />
              <Label htmlFor="is_global">设为全局黑话</Label>
            </div>
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
// 黑话导入对话框
// ====================
interface JargonImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: JargonChatInfo[]
  onSuccess: () => void
}

function normalizeJargonImportItems(payload: unknown): JargonExportItem[] {
  if (Array.isArray(payload)) {
    return payload as JargonExportItem[]
  }
  if (
    payload &&
    typeof payload === 'object' &&
    Array.isArray((payload as { jargons?: unknown }).jargons)
  ) {
    return (payload as { jargons: JargonExportItem[] }).jargons
  }
  return []
}

export function JargonImportDialog({
  open,
  onOpenChange,
  chatList,
  onSuccess,
}: JargonImportDialogProps) {
  const [items, setItems] = useState<JargonExportItem[]>([])
  const [fileName, setFileName] = useState('')
  const [targetSessionIds, setTargetSessionIds] = useState<string[]>([])
  const [conflictStrategy, setConflictStrategy] = useState<'skip' | 'overwrite'>('skip')
  const [importing, setImporting] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    if (!open) {
      setItems([])
      setFileName('')
      setTargetSessionIds([])
      setConflictStrategy('skip')
    }
  }, [open])

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return

    try {
      const payload = JSON.parse(await file.text()) as unknown
      const nextItems = normalizeJargonImportItems(payload)
      if (nextItems.length === 0) {
        toast({
          title: '读取失败',
          description: 'JSON 中没有可导入的黑话',
          variant: 'destructive',
        })
        return
      }
      setItems(nextItems)
      setFileName(file.name)
    } catch (error) {
      toast({
        title: '读取失败',
        description: error instanceof Error ? error.message : '无法解析 JSON 文件',
        variant: 'destructive',
      })
    }
  }

  const handleImport = async () => {
    if (items.length === 0) {
      toast({
        title: '请选择文件',
        description: '请先选择要导入的黑话 JSON 文件',
        variant: 'destructive',
      })
      return
    }
    if (targetSessionIds.length === 0) {
      toast({
        title: '请选择聊天',
        description: '请至少选择一个导入目标聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setImporting(true)
      const result = await importJargons({
        target_session_ids: targetSessionIds,
        jargons: items,
        conflict_strategy: conflictStrategy,
      })
      toast({
        title: '导入完成',
        description: `成功 ${result.imported_count} 个，跳过 ${result.skipped_count} 个，失败 ${result.failed_count} 个`,
      })
      onSuccess()
      onOpenChange(false)
    } catch (error) {
      toast({
        title: '导入失败',
        description: error instanceof Error ? error.message : '无法导入黑话',
        variant: 'destructive',
      })
    } finally {
      setImporting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>导入黑话</DialogTitle>
          <DialogDescription>将 JSON 中的黑话导入到一个或多个聊天</DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="jargon_import_file">JSON 文件</Label>
              <Input id="jargon_import_file" type="file" accept=".json,application/json" onChange={handleFileChange} />
              <p className="text-muted-foreground text-xs">
                {fileName ? `${fileName}，共 ${items.length} 条黑话` : '支持 maibot.jargon.export 或黑话数组'}
              </p>
            </div>

            <div className="space-y-2">
              <Label>导入目标</Label>
              <MultiSelect
                options={chatList.map((chat) => ({
                  label: formatChatDisplayName(chat.chat_name, chat.account_id),
                  value: chat.session_id,
                }))}
                selected={targetSessionIds}
                onChange={setTargetSessionIds}
                placeholder="选择一个或多个聊天"
                emptyText="没有可选聊天"
              />
            </div>

            <div className="space-y-2">
              <Label>冲突处理</Label>
              <Select
                value={conflictStrategy}
                onValueChange={(value) => setConflictStrategy(value as 'skip' | 'overwrite')}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="skip">跳过已有黑话</SelectItem>
                  <SelectItem value="overwrite">覆盖已有黑话</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleImport} disabled={importing}>
            {importing ? '导入中...' : '导入'}
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
