import { CheckCircle2, Circle, Clock, Database, Hash, Info } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'

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
import { Checkbox } from '@/components/ui/checkbox'
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
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

import {
  createExpression,
  getExpressionChatTargets,
  importLegacyExpressions,
  previewLegacyExpressionImport,
  previewLegacyExpressionImportFile,
  updateExpression,
} from '@/lib/expression-api'

import type {
  ChatInfo,
  Expression,
  ExpressionCreateRequest,
  ExpressionUpdateRequest,
  LegacyExpressionGroupPreview,
  LegacyExpressionImportPreviewResponse,
} from '@/types/expression'

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

  const getChatName = (): string => {
    return expression.chat_name || chatNameMap.get(expression.chat_id) || expression.chat_id
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>表达方式详情</DialogTitle>
          <DialogDescription>
            查看表达方式的完整信息
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
        <div className="space-y-4">
            <div className="grid gap-4">
            <InfoItem label="情境" value={expression.situation} />
            <InfoItem label="风格" value={expression.style} />
            <InfoItem 
              label="聊天" 
              value={getChatName()} 
            />
            <InfoItem icon={Hash} label="记录ID" value={expression.id.toString()} mono />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <InfoItem icon={Clock} label="创建时间" value={formatTime(expression.create_date)} />
          </div>

          {/* 状态标记 */}
          <div className="rounded-lg border bg-muted/50 p-4">
            <Label className="text-xs text-muted-foreground mb-3 block">状态标记</Label>
            <div className="grid gap-4">
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
            </div>
          </div>
        </div>
        </DialogBody>

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
 * 从旧版数据库导入表达方式对话框
 */
export function LegacyExpressionImportDialog({
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
  const [dbPath, setDbPath] = useState('')
  const [preview, setPreview] = useState<LegacyExpressionImportPreviewResponse | null>(null)
  const [targetMap, setTargetMap] = useState<Record<string, string>>({})
  const [enabledMap, setEnabledMap] = useState<Record<string, boolean>>({})
  const [targetChatList, setTargetChatList] = useState<ChatInfo[]>(chatList)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [importing, setImporting] = useState(false)
  const { toast } = useToast()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const isBusy = loadingPreview || importing

  const getLocalFilePath = (file: File): string => {
    return (file as File & { path?: string }).path || ''
  }

  const handleDialogOpenChange = (nextOpen: boolean) => {
    if (isBusy) return
    onOpenChange(nextOpen)
  }

  useEffect(() => {
    if (!open) return

    const loadTargets = async () => {
      try {
        const targets = await getExpressionChatTargets()
        setTargetChatList(targets)
      } catch {
        setTargetChatList(chatList)
      }
    }

    loadTargets()
  }, [open, chatList])

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return

    const localPath = getLocalFilePath(file)
    setDbPath(localPath || file.name)
    setLoadingPreview(true)
    try {
      const result = localPath
        ? await previewLegacyExpressionImport({ db_path: localPath })
        : await previewLegacyExpressionImportFile(file)

      const initialMap: Record<string, string> = {}
      const initialEnabledMap: Record<string, boolean> = {}
      result.groups.forEach((group) => {
        if (group.matched_sessions.length > 1) {
          initialMap[group.old_chat_id] = '__all_matched__'
        } else if (group.matched_session_id) {
          initialMap[group.old_chat_id] = group.matched_session_id
        }
        initialEnabledMap[group.old_chat_id] = group.matched_sessions.length > 0 || Boolean(group.matched_session_id)
      })
      setPreview(result)
      setDbPath(localPath || result.db_path)
      setTargetMap(initialMap)
      setEnabledMap(initialEnabledMap)
    } catch (error) {
      toast({
        title: '预览失败',
        description: error instanceof Error ? error.message : '预览旧版导入失败',
        variant: 'destructive',
      })
    } finally {
      setLoadingPreview(false)
    }
  }

  const handleImport = async () => {
    if (!preview) return

    const mappings = preview.groups.map((group) => {
      const selectedTarget = targetMap[group.old_chat_id]
      const targetChatIds = selectedTarget === '__all_matched__'
        ? group.matched_sessions.map((session) => session.session_id)
        : []
      return {
        old_chat_id: group.old_chat_id,
        target_chat_id: enabledMap[group.old_chat_id] && selectedTarget !== '__all_matched__'
          ? selectedTarget || null
          : null,
        target_chat_ids: enabledMap[group.old_chat_id] ? targetChatIds : [],
      }
    })

    setImporting(true)
    try {
      const result = await importLegacyExpressions({
        db_path: preview.db_path,
        mappings,
      })
      toast({
        title: '导入完成',
        description: result.message,
      })
      onSuccess()
      onOpenChange(false)
    } catch (error) {
      toast({
        title: '导入失败',
        description: error instanceof Error ? error.message : '旧版导入失败',
        variant: 'destructive',
      })
    } finally {
      setImporting(false)
    }
  }

  const renderGroupLabel = (group: LegacyExpressionGroupPreview) => {
    if (group.platform && group.target_id && group.chat_type) {
      return `${group.platform}:${group.chat_type === 'group' ? '群' : '私聊'}:${group.target_id}`
    }
    return group.old_chat_id
  }

  return (
    <Dialog open={open} onOpenChange={handleDialogOpenChange}>
      <DialogContent className="!fixed !top-4 !translate-y-0 [--dialog-width:72rem] sm:!top-6">
        {isBusy && (
          <div className="absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-background/80 backdrop-blur-sm">
            <div className="rounded-lg border bg-background px-5 py-4 text-center shadow-lg">
              {loadingPreview ? (
                <ThinkingIllustration className="mx-auto" />
              ) : (
                <div className="text-sm font-medium">正在导入表达方式，请勿关闭</div>
              )}
              <div className="mt-1 text-xs text-muted-foreground">数据量较大时可能需要等待一会儿</div>
            </div>
          </div>
        )}
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Database className="h-5 w-5" />
            从旧版本导入
          </DialogTitle>
          <DialogDescription>
            读取旧数据库中的 expression 表，并根据 chat_streams 自动匹配当前聊天流。
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
          <div className="space-y-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="flex-1 space-y-2">
                <Label htmlFor="legacy-db-path">旧数据库路径</Label>
                <Input
                  id="legacy-db-path"
                  value={dbPath}
                  readOnly
                  placeholder="选择旧版 SQLite 数据库文件"
                />
              </div>
              <Button variant="outline" onClick={() => fileInputRef.current?.click()} disabled={isBusy}>
                浏览
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".db,.sqlite,.sqlite3,application/vnd.sqlite3,application/octet-stream"
                className="hidden"
                onChange={handleFileChange}
              />
            </div>

            {preview && (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
                  <span>表达方式 {preview.total_count} 条</span>
                  <span>已匹配 {preview.matched_count} 组</span>
                  <span>未匹配 {preview.unmatched_count} 组</span>
                </div>

                <div className="max-h-[50vh] overflow-y-auto rounded-lg border">
                  <div className="grid grid-cols-[2.5rem_minmax(0,1.4fr)_5rem_minmax(0,1.1fr)_minmax(14rem,1fr)] gap-3 border-b bg-muted/50 px-3 py-2 text-xs font-medium text-muted-foreground">
                    <div>导入</div>
                    <div>旧聊天流</div>
                    <div>数量</div>
                    <div>自动匹配</div>
                    <div>导入到</div>
                  </div>
                  {preview.groups.map((group) => (
                    <div
                      key={group.old_chat_id}
                      className="grid grid-cols-[2.5rem_minmax(0,1.4fr)_5rem_minmax(0,1.1fr)_minmax(14rem,1fr)] items-center gap-3 border-b px-3 py-2 last:border-b-0"
                    >
                      <Checkbox
                        checked={enabledMap[group.old_chat_id] ?? false}
                        onCheckedChange={(checked) => {
                          setEnabledMap((current) => ({
                            ...current,
                            [group.old_chat_id]: checked === true,
                          }))
                        }}
                      />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium" title={renderGroupLabel(group)}>
                          {renderGroupLabel(group)}
                        </div>
                        <div className="truncate text-xs text-muted-foreground" title={group.old_chat_id}>
                          {group.old_chat_id}
                        </div>
                      </div>
                      <div className="text-sm">{group.expression_count}</div>
                      <div className="min-w-0 text-sm">
                        {group.matched ? (
                          <span
                            className="truncate text-green-600"
                            title={group.matched_sessions.map((session) => session.chat_name).join(' / ') || undefined}
                          >
                            {group.matched_sessions.length > 1
                              ? `${group.matched_sessions.length} 个匹配`
                              : group.matched_chat_name || group.matched_session_id}
                          </span>
                        ) : (
                          <span className="text-amber-600">未找到</span>
                        )}
                      </div>
                      <Select
                        value={targetMap[group.old_chat_id] || 'skip'}
                        onValueChange={(value) => {
                          setTargetMap((current) => ({
                            ...current,
                            [group.old_chat_id]: value === 'skip' ? '' : value,
                          }))
                          setEnabledMap((current) => ({
                            ...current,
                            [group.old_chat_id]: value !== 'skip',
                          }))
                        }}
                      >
                        <SelectTrigger className="h-8">
                          <SelectValue placeholder="跳过" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="skip">跳过</SelectItem>
                          {group.matched_sessions.length > 1 && (
                            <SelectItem value="__all_matched__">全部匹配项</SelectItem>
                          )}
                          {group.matched_sessions.map((session) => (
                            <SelectItem key={`matched-${session.session_id}`} value={session.session_id}>
                              {session.chat_name}
                            </SelectItem>
                          ))}
                          {targetChatList
                            .filter((chat) => !group.matched_sessions.some((session) => session.session_id === chat.chat_id))
                            .map((chat) => (
                            <SelectItem key={chat.chat_id} value={chat.chat_id}>
                              {chat.chat_name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  ))}
                  {preview.groups.length === 0 && (
                    <div className="px-3 py-8 text-center text-sm text-muted-foreground">
                      未读取到可导入的表达方式
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isBusy}>
            取消
          </Button>
          <Button onClick={handleImport} disabled={!preview || isBusy}>
            {importing ? '导入中...' : '确认导入'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
      await createExpression(formData)
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
          <DialogDescription>
            创建新的表达方式记录
          </DialogDescription>
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>编辑表达方式</DialogTitle>
          <DialogDescription>
            修改表达方式的信息
          </DialogDescription>
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
                <p>• 已检查：表示该表达方式已通过人工审核</p>
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
                <Label className="text-sm font-medium">
                  已检查
                </Label>
                <p className="text-xs text-muted-foreground">
                  已通过审核
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
          <AlertDialogAction onClick={onConfirm} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
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
