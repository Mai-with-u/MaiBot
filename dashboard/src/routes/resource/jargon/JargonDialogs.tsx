import { ArrowLeft, ChevronLeft, ChevronRight, Upload } from 'lucide-react'
import { JargonMeaningOverview } from './JargonMeaningOverview'
import { JargonScopeEditor, type DetailTab } from './JargonScopeEditor'
import { useEffect, useRef, useState } from 'react'

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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { Separator } from '@/components/ui/separator'
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
  JargonGroupInfo,
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
  groups?: JargonGroupInfo[]
  onScopeChange?: (jargon: Jargon | null) => void
  pageNavigation?: { index: number; count: number; go: (offset: number) => void }
  onChanged: (jargon: Jargon) => void
}

function detailForm(jargon: Jargon): JargonUpdateRequest {
  return {
    meaning: jargon.meaning || '',
    session_id: jargon.session_id,
    session_ids: jargon.session_ids?.length
      ? jargon.session_ids
      : [jargon.session_id].filter(Boolean),
    is_global: jargon.is_global,
    is_jargon: jargon.is_jargon,
    allow_update: jargon.allow_update,
  }
}

export function JargonDetailDialog({
  jargon,
  open,
  onOpenChange,
  chatList,
  onChanged,
  groups = [],
  onScopeChange,
  pageNavigation,
}: JargonDetailDialogProps) {
  const [formData, setFormData] = useState<JargonUpdateRequest>({})
  const [saving, setSaving] = useState(false)
  const [tab, setTab] = useState<DetailTab>('meaning')
  const [pendingLeave, setPendingLeave] = useState<'overview' | 'close' | null>(null)
  const [changingScope, setChangingScope] = useState(false)
  const titleRef = useRef<HTMLHeadingElement>(null)
  const { toast } = useToast()

  useEffect(() => {
    if (jargon && open) {
      setFormData(detailForm(jargon))
      setPendingLeave(null)
    } else {
      setTab('meaning')
    }
  }, [jargon, open])

  const editMeaning = (meaning: string) => {
    setFormData({ ...formData, meaning, ...(meaning.trim() && { allow_update: false }) })
  }

  const handleSave = async (allScopeRevisions?: Record<number, number>) => {
    if (!jargon) return
    const globalWithoutSources =
      formData.is_global && !jargon.session_id && !jargon.session_ids?.length
    if (!jargon.scope_kind && !globalWithoutSources && formData.session_ids?.length === 0) {
      toast({
        title: '验证失败',
        description: '请至少选择一个聊天',
        variant: 'destructive',
      })
      return
    }

    try {
      setSaving(true)
      const changes: JargonUpdateRequest = {
        ...(formData.is_jargon !== false && formData.meaning !== (jargon.meaning ?? '') && { meaning: formData.meaning }),
        ...(formData.is_jargon !== jargon.is_jargon && { is_jargon: formData.is_jargon }),
        ...(formData.allow_update !== jargon.allow_update && { allow_update: formData.allow_update }),
      }
      const response = await updateJargon(jargon.id, allScopeRevisions ? {
        ...changes, all_scope_revisions: allScopeRevisions, revision: jargon.revision,
      } : {
        ...formData,
        ...(formData.is_jargon === false && { meaning: undefined }),
        ...(jargon.scope_kind && {
          session_ids: undefined,
          session_id: undefined,
          is_global: undefined,
          revision: jargon.revision,
        }),
        ...(globalWithoutSources &&
          !formData.session_ids?.length && { session_ids: undefined, session_id: undefined }),
      })
      if (response.data) {
        onChanged(
          jargon.scope_name ? { ...response.data, scope_name: jargon.scope_name } : response.data
        )
      }
      if (response.data) setTab('meaning')
      toast({
        title: '保存成功',
        description: allScopeRevisions ? `已保存到 ${Object.keys(allScopeRevisions).length} 个作用域` : '黑话已更新',
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
  const status = formData.is_jargon

  const dirty =
    formData.meaning !== undefined &&
    (formData.meaning !== (jargon.meaning ?? '') ||
      formData.allow_update !== jargon.allow_update ||
      formData.is_jargon !== jargon.is_jargon ||
      (!jargon.scope_kind &&
        (formData.is_global !== jargon.is_global ||
          JSON.stringify(formData.session_ids) !== JSON.stringify(detailForm(jargon).session_ids))))
  const busy = saving || changingScope
  const leave = (target: 'overview' | 'close') => {
    if (target === 'close') onOpenChange(false)
    else {
      setFormData(detailForm(jargon))
      setTab('meaning')
    }
  }
  const openVersion = (row: Jargon, view: DetailTab) => {
    if (row !== jargon) onScopeChange?.(row)
    setTab(view)
  }
  const requestLeave = (target: 'overview' | 'close') => {
    if (busy) return
    if (dirty) setPendingLeave(target)
    else leave(target)
  }
  const renderEditor = (editRevisions: Record<number, number>) => (
    <>
      <fieldset disabled={busy} className="custom-scrollbar flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto">
        <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2">
          <Select
            value={String(status ?? 'pending')}
            disabled={busy}
            onValueChange={(value) => setFormData({ ...formData, is_jargon: value === 'pending' ? null : value === 'true' })}
          >
            <SelectTrigger aria-label="黑话状态" className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent side="bottom" align="start">
              <SelectItem value="pending">积累中</SelectItem>
              <SelectItem value="true">黑话</SelectItem>
              <SelectItem value="false">不是黑话</SelectItem>
            </SelectContent>
          </Select>
          <Label htmlFor="detail_allow_update" className="flex items-center gap-2">
            <Checkbox id="detail_allow_update" checked={formData.allow_update}
              onCheckedChange={(checked) => setFormData({ ...formData, allow_update: checked === true })} />
            允许更新
          </Label>
        </div>
        <Textarea
          id="detail_meaning"
          aria-label="含义"
          disabled={status === false}
          value={formData.meaning ?? ''}
          readOnly={jargon.archived}
          onChange={(e) => editMeaning(e.target.value)}
          placeholder="输入黑话含义"
          autoResize={false}
          className="min-h-8 flex-1 resize-none overflow-y-auto leading-relaxed"
        />
        {!jargon.scope_kind && (
          <div className="space-y-3">
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
            <div className="flex items-center gap-2">
              <Switch
                id="detail_is_global"
                disabled={jargon.is_global}
                checked={formData.is_global}
                onCheckedChange={(checked) => setFormData({ ...formData, is_global: checked })}
              />
              <Label htmlFor="detail_is_global">通用词条</Label>
            </div>
          </div>
        )}
      </fieldset>
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
        {dirty && <span className="text-muted-foreground mr-auto text-xs">有未保存的修改</span>}
        <Button variant="ghost" size="sm" disabled={!dirty || busy} onClick={() => requestLeave('overview')}>
          取消
        </Button>
        {Object.keys(editRevisions).length > 1 && (
          <Button variant="outline" size="sm" disabled={!dirty || busy}
            title={`将本次修改保存到此词的全部 ${Object.keys(editRevisions).length} 个作用域`}
            onClick={() => handleSave(editRevisions)}>保存到全部</Button>
        )}
        <Button size="sm" disabled={!dirty || busy} onClick={() => handleSave()}>
          {saving ? '保存中…' : '保存'}
        </Button>
      </div>
    </>
  )

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) requestLeave('close')
      }}
    >
      <DialogContent
        data-jargon-detail="true"
        className="flex h-[min(39rem,calc(100dvh-2rem))] flex-col gap-0 overflow-hidden p-0 [--dialog-width:68rem] max-sm:pt-4"
        aria-describedby={undefined}
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          titleRef.current?.focus()
        }}
      >
        <DialogHeader className="shrink-0 px-5 py-4 pr-12 text-left">
          <div className="flex min-w-0 items-center justify-between gap-3">
            <DialogTitle ref={titleRef} tabIndex={-1} className="min-w-0 break-words outline-none">
              {jargon.content}
            </DialogTitle>
            {tab === 'meaning' && pageNavigation && pageNavigation.count > 1 && (
              <nav aria-label="本页词条" className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label="上一个词条"
                  title="上一个词条"
                  disabled={busy || dirty || !pageNavigation.index}
                  onClick={() => {
                    setTab('meaning')
                    pageNavigation.go(-1)
                  }}
                >
                  <ChevronLeft />
                </Button>
                <span className="text-muted-foreground text-xs tabular-nums">
                  {pageNavigation.index + 1} / {pageNavigation.count}
                </span>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label="下一个词条"
                  title="下一个词条"
                  disabled={busy || dirty || pageNavigation.index === pageNavigation.count - 1}
                  onClick={() => {
                    setTab('meaning')
                    pageNavigation.go(1)
                  }}
                >
                  <ChevronRight />
                </Button>
              </nav>
            )}
          </div>
          {(tab === 'evidence' || tab === 'manage') && (
            <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <Button
                size="xs"
                variant="ghost"
                onClick={() => requestLeave('overview')}
                disabled={busy}
              >
                <ArrowLeft />
                返回释义
              </Button>
            </div>
          )}
        </DialogHeader>
        <div className="mx-3 shrink-0 sm:mx-5">
          <Separator variant="groove" />
        </div>
        <div
          key={jargon.content}
          className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden px-3 py-3 sm:px-5 sm:py-4"
        >
          {jargon.scope_kind && onScopeChange ? (
            <JargonScopeEditor
              key={jargon.id}
              jargon={jargon}
              groups={groups}
              chats={chatList}
              tab={tab}
              onTabChange={setTab}
              busy={busy}
              dirty={dirty}
              onChangingScope={setChangingScope}
              onSelect={openVersion}
              onChange={(row) => {
                setTab('meaning')
                onScopeChange(row)
              }}
            >
              {renderEditor}
            </JargonScopeEditor>
          ) : (
            <JargonMeaningOverview
              current={jargon}
              rows={[jargon]}
              onOpen={openVersion}
              scoped={false}
              panel={jargon.archived ? undefined : renderEditor({})}
            />
          )}
        </div>
      </DialogContent>
      <AlertDialog
        open={pendingLeave !== null}
        onOpenChange={(next) => {
          if (!next) setPendingLeave(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle>
            <AlertDialogDescription>
              {jargon.scope_name || jargon.content}的修改尚未保存。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingLeave) leave(pendingLeave)
              }}
            >
              放弃修改
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Dialog>
  )
}

// ====================
// 黑话创建对话框
// ====================
interface JargonCreateDialogProps {
  groups?: JargonGroupInfo[]
  open: boolean
  onOpenChange: (open: boolean) => void
  chatList: JargonChatInfo[]
  onSuccess: (isGlobal: boolean) => void
}

export function JargonCreateDialog({
  groups = [],
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

  const universalGroup = groups.find((group) => group.is_global)
  const groupedChats = new Set(groups.flatMap((group) => group.session_ids))
  const independentChats = universalGroup ? [] : chatList.filter((chat) => !groupedChats.has(chat.session_id))
  const groupId = formData.group_id ?? (independentChats.length ? undefined : universalGroup?.id ?? groups[0]?.id)

  const handleCreate = async () => {
    if (!formData.content.trim() || (!formData.is_global && !groupId && !formData.session_ids?.length)) {
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
          : groupId ? { ...formData, group_id: groupId, session_ids: [], session_id: undefined } : formData
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
              <Label htmlFor="jargon-create-global">通用词条</Label>
            </div>
            {formData.is_global && <p className="text-muted-foreground text-xs">所有启用黑话的聊天都可使用。各聊天的新线索会共同用于更新通用释义。</p>}
            {!formData.is_global && groups.length > 0 && (
              <Select value={groupId ?? 'chat'} onValueChange={(value) =>
                setFormData({ ...formData, group_id: value === 'chat' ? undefined : value, session_ids: [], session_id: undefined })}>
                <SelectTrigger aria-label="释义归属"><SelectValue /></SelectTrigger>
                <SelectContent>{independentChats.length > 0 && <SelectItem value="chat">指定聊天</SelectItem>}
                  {groups.map(g => <SelectItem key={g.id} value={g.id}>共享组 · {g.name}{g.is_global && '（所有聊天）'}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
            {!formData.is_global && !groupId && (
              <div className="space-y-2">
                <Label>
                  聊天 <span className="text-destructive">*</span>
                </Label>
                <MultiSelect
                  sortable={false}
                  options={independentChats.map((chat) => ({
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
            删除「{jargon?.content}」在{jargon?.scope_names?.join("、") || jargon?.chat_name || "当前范围"}的释义？
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
            将删除选中词条在所选范围内的 {count} 条释义。
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
