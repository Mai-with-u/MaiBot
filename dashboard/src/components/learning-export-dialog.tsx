import { Check, ChevronDown, Upload } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useToast } from '@/hooks/use-toast'

type ExportScope = 'selected' | 'all' | 'chat' | 'group'

export interface LearningExportOptions {
  ids?: number[]
  chatIds?: string[]
  includeChatInfo: boolean
}

interface LearningExportDialogProps {
  kind: 'expression' | 'jargon'
  selectedIds: Set<number>
  chats: { id: string; name: string; targetId?: string | null }[]
  groups: { id: string | number; name: string; chatIds: string[]; isGlobal: boolean; details: string }[]
  loading: boolean
  error: boolean
  onRetry: () => void
  onBack: () => void
  onExport: (options: LearningExportOptions) => Promise<{ count: number }>
}

/** 表达与黑话共用导出卡片，范围独立于列表的搜索、分页和筛选。 */
export function LearningExportDialog({
  kind,
  selectedIds,
  chats,
  groups,
  loading,
  error,
  onRetry,
  onBack,
  onExport,
}: LearningExportDialogProps) {
  const name = kind === 'expression' ? '表达方式' : '黑话'
  const [scope, setScope] = useState<ExportScope>(selectedIds.size > 0 ? 'selected' : 'all')
  const [chatId, setChatId] = useState('')
  const [groupId, setGroupId] = useState('')
  const [targetPickerOpen, setTargetPickerOpen] = useState(false)
  const [includeChatInfo, setIncludeChatInfo] = useState(false)
  const [exporting, setExporting] = useState(false)
  const { toast } = useToast()
  const chat = chats.find((item) => item.id === chatId)
  const group = groups.find((item) => String(item.id) === groupId)
  const needsTargets = scope === 'chat' || scope === 'group'
  const targetOptions =
    scope === 'chat'
      ? chats.map((item) => ({
          id: item.id,
          label: item.name,
          keywords: [item.name, item.targetId ?? ''],
          disabled: false,
          details: undefined,
        }))
      : groups.map((item) => ({
          id: String(item.id),
          label: `${item.name}${item.isGlobal ? '（全部聊天）' : `（${item.chatIds.length} 个聊天）`}`,
          keywords: [item.name, ...item.chatIds],
          disabled: !item.isGlobal && item.chatIds.length === 0,
          details: item.details,
        }))
  const selectedTargetId = scope === 'chat' ? chatId : groupId
  const selectedTarget = targetOptions.find((item) => item.id === selectedTargetId)

  const invalid =
    (scope === 'selected' && selectedIds.size === 0) ||
    (scope === 'chat' && !chat) ||
    (scope === 'group' && (!group || (!group.isGlobal && group.chatIds.length === 0))) ||
    (needsTargets && (loading || error))

  const handleExport = async () => {
    if (invalid || exporting) return
    setExporting(true)
    try {
      const result = await onExport({
        ids: scope === 'selected' ? Array.from(selectedIds) : undefined,
        chatIds:
          scope === 'chat'
            ? [chatId]
            : scope === 'group' && group && !group.isGlobal
              ? group.chatIds
              : undefined,
        includeChatInfo,
      })
      const suffix = scope === 'chat' ? chat!.name : scope === 'group' ? group!.name : scope
      const filename = `${kind === 'expression' ? 'expressions' : 'jargons'}-${suffix.replace(/[\\/:*?"<>|]/g, '_').slice(0, 60)}.json`
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(result, null, 2)], { type: 'application/json;charset=utf-8' })
      )
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      toast({ title: '导出成功', description: `已导出 ${result.count} 个${name}` })
    } catch (error) {
      toast({
        title: '导出失败',
        description: error instanceof Error ? error.message : `无法导出${name}`,
        variant: 'destructive',
      })
    } finally {
      setExporting(false)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !exporting) onBack()
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>导出{name}</DialogTitle>
          <DialogDescription>选择导出范围，下载为 JSON 文件。</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <fieldset disabled={exporting} className="space-y-4">
            <legend className="mb-3 text-sm font-medium">导出范围</legend>
            <RadioGroup value={scope} onValueChange={(value) => setScope(value as ExportScope)}>
              {(
                [
                  ['selected', `已选内容（${selectedIds.size} 个）`],
                  ['all', `全部${name}`],
                  ['chat', '按聊天导出'],
                  ['group', '按共享组导出'],
                ] as const
              ).map(([value, label]) => (
                <Label
                  key={value}
                  className="flex cursor-pointer items-center gap-3 rounded-md border p-3"
                >
                  <RadioGroupItem
                    value={value}
                    disabled={value === 'selected' && selectedIds.size === 0}
                  />
                  {label}
                </Label>
              ))}
            </RadioGroup>
            <p className="text-muted-foreground text-sm">
              导出范围不受列表搜索、分页及状态筛选影响。
            </p>
            {needsTargets && (
              <div className="space-y-2">
                {loading ? (
                  <p className="text-muted-foreground text-sm">正在加载聊天与共享组…</p>
                ) : error ? (
                  <div role="alert" className="flex items-center gap-2 text-sm">
                    加载导出范围失败
                    <Button variant="outline" size="sm" onClick={onRetry}>
                      重试
                    </Button>
                  </div>
                ) : (
                  <Popover open={targetPickerOpen && !exporting} onOpenChange={setTargetPickerOpen}>
                    <PopoverTrigger asChild>
                      <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={targetPickerOpen}
                        title={selectedTarget?.details}
                        aria-label={scope === 'chat' ? '选择导出聊天' : '选择导出共享组'}
                        className="w-full justify-between font-normal"
                      >
                        <span className="min-w-0 truncate">
                          {selectedTarget?.label ?? (scope === 'chat' ? '选择聊天' : '选择共享组')}
                        </span>
                        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="start"
                      className="z-[100] w-[var(--radix-popover-trigger-width)] p-0"
                    >
                      <Command
                        filter={(value, search, keywords) =>
                          [value, ...(keywords ?? [])].some((text) =>
                            text.toLowerCase().includes(search.trim().toLowerCase())
                          )
                            ? 1
                            : 0
                        }
                      >
                        <CommandInput placeholder="搜索名称或 ID…" aria-label="搜索名称或 ID" />
                        <CommandList>
                          <CommandEmpty>没有匹配的结果</CommandEmpty>
                          <TooltipProvider delayDuration={300}>
                            {targetOptions.map((item) => (
                              <Tooltip key={item.id}>
                                <CommandItem
                                  value={item.id}
                                  keywords={item.keywords}
                                  disabled={item.disabled}
                                  className="p-0"
                                  onSelect={() => {
                                    if (scope === 'chat') setChatId(item.id)
                                    else setGroupId(item.id)
                                    setTargetPickerOpen(false)
                                  }}
                                >
                                  {/* cmdk 会覆盖行上的 onPointerMove，提示触发器放在内部以保留悬停事件。 */}
                                  <TooltipTrigger asChild>
                                    <span className="flex w-full min-w-0 items-center gap-2 px-2 py-1.5">
                                      <span className="min-w-0 flex-1 break-words">{item.label}</span>
                                      {item.id === selectedTargetId && (
                                        <Check className="h-4 w-4 shrink-0" />
                                      )}
                                    </span>
                                  </TooltipTrigger>
                                </CommandItem>
                                {item.details && (
                                  <TooltipContent
                                    side="right"
                                    align="start"
                                    className="z-[110] max-h-72 max-w-sm overflow-y-auto break-words whitespace-pre-line"
                                  >
                                    {item.details}
                                  </TooltipContent>
                                )}
                              </Tooltip>
                            ))}
                          </TooltipProvider>
                        </CommandList>
                      </Command>
                    </PopoverContent>
                  </Popover>
                )}
                {!loading &&
                  !error &&
                  (scope === 'chat' ? chats.length === 0 : groups.length === 0) && (
                    <p className="text-muted-foreground text-sm">
                      暂无可选的{scope === 'chat' ? '聊天' : '共享组'}。
                    </p>
                  )}
              </div>
            )}
            {kind === 'jargon' && (
              <Label className="flex cursor-pointer items-center justify-between gap-3 rounded-md border p-3">
                包含聊天目标信息
                <Switch checked={includeChatInfo} onCheckedChange={setIncludeChatInfo} />
              </Label>
            )}
          </fieldset>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onBack} disabled={exporting}>
            取消
          </Button>
          <Button onClick={handleExport} disabled={invalid || exporting}>
            <Upload className="h-4 w-4" />
            {exporting ? '导出中…' : '导出 JSON'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
