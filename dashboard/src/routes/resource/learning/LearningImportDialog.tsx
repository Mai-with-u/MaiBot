import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  BookOpen,
  BookOpenText,
  ChevronRight,
  FileUp,
  MessageSquareText,
  Keyboard,
  PenLine,
  Smile,
  X,
} from 'lucide-react'
import { useRef, useState } from 'react'
import type { DragEvent, ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { DashedButton } from '@/components/ui/dashed-button'
import { SelectionListItem } from '@/components/ui/selection-list-item'
import { StepIndicator } from '@/components/ui/step-indicator'
import { DataPanel } from '@/components/ui/surface'
import { MultiSelect } from '@/components/ui/multi-select'
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
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'
import { getExpressionChatTargets } from '@/lib/expression-api'

import {
  EMOJI_MIME_TYPES,
  emptyBundle,
  readLearningFiles,
  runLearningImport,
  type LearningImportBundle,
  type LearningImportResult,
  type LearningKind,
} from './learning-import'

const KIND_META: Record<
  LearningKind,
  { name: string; note: string; unit: string; icon: ReactNode }
> = {
  expression: { name: '表达方式', note: '情境 → 风格', unit: '条', icon: <MessageSquareText /> },
  jargon: { name: '黑话', note: '词条 → 释义', unit: '条', icon: <BookOpenText /> },
  emoji: { name: '表情包', note: '图片文件', unit: '张', icon: <Smile /> },
}
const KINDS: LearningKind[] = ['expression', 'jargon', 'emoji']

function countOf(bundle: LearningImportBundle, kind: LearningKind): number {
  if (kind === 'expression') return bundle.expressions.length
  if (kind === 'jargon') return bundle.jargons.length
  return bundle.emojis.length
}

/**
 * 学习页唯一的「添加学习内容」入口，先选方式再进入具体流程：
 * 1. 手动导入 → 手动键入（子页的逐条新增表单）或导入数据（多文件，三类一起导入）；
 * 2. 自动导入 → 从文段学习（子页的自动学习对话框）。
 * 表情包页签没有键入和自动学习，直接进入导入数据。
 */
export function LearningImportDialog({
  open,
  onOpenChange,
  createKind = null,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 当前页签可新增的类别；表情包页签为 null，不显示新增 */
  createKind?: 'expression' | 'jargon' | null
  onCreate?: (mode: 'manual' | 'auto') => void
}) {
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const [bundle, setBundle] = useState<LearningImportBundle>(emptyBundle)
  const [kinds, setKinds] = useState<Record<LearningKind, boolean>>({
    expression: true,
    jargon: true,
    emoji: true,
  })
  const [scope, setScope] = useState<'global' | 'chat'>('global')
  const [chatIds, setChatIds] = useState<string[]>([])
  const [conflict, setConflict] = useState<'skip' | 'overwrite'>('skip')
  const [emojiEmotion, setEmojiEmotion] = useState('')
  const [reading, setReading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [results, setResults] = useState<LearningImportResult[] | null>(null)
  const [dragging, setDragging] = useState(false)
  // 第一步的选择层级；表情包页签只有导入数据，直接从手动导入层开始。
  const [level, setLevel] = useState<'root' | 'manual'>('root')
  const showRoot = Boolean(createKind) && level === 'root'

  const chatTargets = useQuery({
    queryKey: ['expression', 'chat-targets'],
    queryFn: () => getExpressionChatTargets(),
    enabled: open && scope === 'chat',
  })

  const step = results ? 3 : KINDS.some((kind) => countOf(bundle, kind) > 0) ? 2 : 1
  const chosen = KINDS.filter((kind) => kinds[kind] && countOf(bundle, kind) > 0)
  const canImport =
    step === 2 && chosen.length > 0 && !importing && (scope === 'global' || chatIds.length > 0)

  const reset = () => {
    setBundle(emptyBundle())
    setResults(null)
    setChatIds([])
    setScope('global')
    setConflict('skip')
    setEmojiEmotion('')
    setKinds({ expression: true, jargon: true, emoji: true })
    setLevel('root')
  }

  const close = (next: boolean) => {
    if (importing) return
    onOpenChange(next)
    if (!next) reset()
  }

  const addFiles = async (files: File[]) => {
    if (files.length === 0) return
    setReading(true)
    try {
      setBundle(await readLearningFiles(files, bundle))
    } finally {
      setReading(false)
    }
  }

  const handleDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    setDragging(false)
    void addFiles(Array.from(event.dataTransfer.files))
  }

  const handleImport = async () => {
    if (!canImport) return
    setImporting(true)
    try {
      const next = await runLearningImport(bundle, {
        kinds,
        chatIds: scope === 'global' ? null : chatIds,
        conflict,
        emojiEmotion,
      })
      setResults(next)
      for (const result of next) {
        void queryClient.invalidateQueries({ queryKey: [result.kind] })
      }
      const imported = next.reduce((sum, item) => sum + item.imported, 0)
      const failed = next.some((item) => item.failed > 0 || item.error)
      toast({
        title: failed ? '导入完成，部分失败' : '导入完成',
        description: `共导入 ${imported} 项`,
        variant: failed ? 'destructive' : undefined,
      })
    } finally {
      setImporting(false)
    }
  }

  const startCreate = (mode: 'manual' | 'auto') => {
    close(false)
    onCreate?.(mode)
  }

  const optionRow = (option: {
    key: string
    icon: ReactNode
    title: string
    note: string
    onSelect: () => void
    selected?: boolean
  }) => (
    <SelectionListItem
      key={option.key}
      selected={option.selected}
      aria-pressed={undefined}
      onClick={option.onSelect}
      className="flex items-center gap-3 px-3 py-3 last:border-b-0"
    >
      <span className="text-muted-foreground shrink-0 [&>svg]:h-5 [&>svg]:w-5" aria-hidden="true">
        {option.icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">{option.title}</span>
        <span className="text-muted-foreground block text-xs">{option.note}</span>
      </span>
      <ChevronRight className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
    </SelectionListItem>
  )
  const kindName = createKind ? KIND_META[createKind].name : ''

  // 拖放区：第一步是导入的主入口，第二步用来继续添加文件。
  const dropZone = (
    <DashedButton
      onClick={() => inputRef.current?.click()}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      data-state={dragging ? 'open' : undefined}
      className="h-auto w-full justify-start gap-3 px-4 py-3 text-left"
    >
      <FileUp className="text-muted-foreground h-5 w-5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="block">
          {reading ? '正在读取…' : step === 1 ? '拖入文件，或点击选择' : '继续添加文件'}
        </span>
        <span className="text-muted-foreground block text-xs font-normal">
          可多选 · .json 导出文件（表达方式、黑话）· png / jpg / gif / webp 表情包
        </span>
      </span>
    </DashedButton>
  )

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="[--dialog-width:36rem]" data-learning-import-dialog="true">
        <DialogHeader>
          <DialogTitle>添加学习内容</DialogTitle>
          <DialogDescription>
            {step === 1 && showRoot
              ? '选择添加方式'
              : step === 1 && createKind
                ? '逐条键入，或导入已有的数据文件'
                : '导入数据文件和表情包图片'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody type="auto" contentClassName="flex flex-col gap-5 pb-1">
          {step === 1 && showRoot && (
            <DataPanel padding="none" className="overflow-hidden" aria-label="添加方式">
              {optionRow({
                key: 'manual',
                icon: <PenLine />,
                title: '手动导入',
                note: `逐条键入${kindName}，或导入已有的数据文件`,
                onSelect: () => setLevel('manual'),
              })}
              {optionRow({
                key: 'auto',
                icon: <BookOpen />,
                title: '自动导入',
                note: `粘贴聊天记录或文章，自动总结${kindName}`,
                onSelect: () => startCreate('auto'),
              })}
            </DataPanel>
          )}
          {step === 1 && !showRoot && (
            <section className="space-y-2" aria-labelledby="learning-add-manual">
              <h3 id="learning-add-manual" className="text-muted-foreground text-xs font-semibold">
                手动导入
              </h3>
              {/* 整块面板都接受拖入文件，不必对准「导入数据」那一行。 */}
              <DataPanel
                padding="none"
                className="overflow-hidden"
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={handleDrop}
              >
                {createKind &&
                  optionRow({
                    key: 'type',
                    icon: <Keyboard />,
                    title: '手动键入',
                    note: `逐条填写${kindName}，精确添加`,
                    onSelect: () => startCreate('manual'),
                  })}
                {optionRow({
                  key: 'data',
                  icon: <FileUp />,
                  title: reading ? '正在读取…' : '导入数据',
                  note: '拖入或选择文件，可多选：.json 导出文件 · png / jpg / gif / webp 表情包',
                  onSelect: () => inputRef.current?.click(),
                  selected: dragging,
                })}
              </DataPanel>
            </section>
          )}
          {step !== 1 && (
            <StepIndicator
              label="导入步骤"
              currentStep={step - 1}
              steps={[
                { id: 'files', label: '选择文件' },
                { id: 'confirm', label: '确认内容与范围' },
                { id: 'done', label: '完成' },
              ]}
            />
          )}

          {step === 2 && dropZone}
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={['.json', 'application/json', ...EMOJI_MIME_TYPES].join(',')}
            className="hidden"
            aria-label="选择要导入的文件"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? [])
              event.target.value = ''
              void addFiles(files)
            }}
          />

          {bundle.rejected.length > 0 && step !== 3 && (
            <div className="text-destructive space-y-1 text-xs" role="alert">
              {bundle.rejected.map((item) => (
                <div key={item.name}>
                  {item.name}：{item.reason}
                </div>
              ))}
            </div>
          )}

          {step === 2 && (
            <>
              <section className="space-y-2">
                <h3 className="text-muted-foreground text-xs font-semibold">导入哪些内容</h3>
                <div className="divide-border divide-y rounded-md border">
                  {KINDS.map((kind) => {
                    const count = countOf(bundle, kind)
                    const meta = KIND_META[kind]
                    return (
                      <label
                        key={kind}
                        className="flex cursor-pointer items-center gap-3 px-3 py-2.5 aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                        aria-disabled={count === 0 || undefined}
                      >
                        <Checkbox
                          checked={count > 0 && kinds[kind]}
                          disabled={count === 0}
                          onCheckedChange={(checked) =>
                            setKinds({ ...kinds, [kind]: checked === true })
                          }
                        />
                        <span
                          className="text-muted-foreground [&>svg]:h-5 [&>svg]:w-5"
                          aria-hidden="true"
                        >
                          {meta.icon}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-semibold">{meta.name}</span>
                          <span className="text-muted-foreground block truncate text-xs">
                            {count === 0 ? '所选文件里没有这一类' : bundle.sources[kind].join('、')}
                          </span>
                        </span>
                        <span className="text-sm tabular-nums">
                          {count} {meta.unit}
                        </span>
                      </label>
                    )
                  })}
                </div>
              </section>

              <div className="grid gap-5 sm:grid-cols-2">
                <section className="space-y-2">
                  <h3 className="text-muted-foreground text-xs font-semibold">
                    表达方式和黑话导入到
                  </h3>
                  <RadioGroup
                    value={scope}
                    onValueChange={(value) => setScope(value as 'global' | 'chat')}
                  >
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="global" id="learning-import-global" />
                      <Label htmlFor="learning-import-global">全局（所有聊天）</Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="chat" id="learning-import-chat" />
                      <Label htmlFor="learning-import-chat">指定聊天</Label>
                    </div>
                  </RadioGroup>
                  {scope === 'chat' && (
                    <MultiSelect
                      options={(chatTargets.data ?? []).map((chat) => ({
                        label: `${formatChatDisplayName(chat.chat_name, chat.account_id)}${chat.is_group ? '（群聊）' : ''}`,
                        value: chat.chat_id,
                      }))}
                      selected={chatIds}
                      onChange={setChatIds}
                      placeholder={chatTargets.isPending ? '加载中…' : '选择一个或多个聊天'}
                      emptyText="没有可选聊天"
                    />
                  )}
                  <p className="text-muted-foreground text-xs">
                    表情包不区分聊天，导入后所有聊天都能用。
                  </p>
                </section>
                <section className="space-y-2">
                  <h3 className="text-muted-foreground text-xs font-semibold">黑话遇到同名词条</h3>
                  <RadioGroup
                    value={conflict}
                    onValueChange={(value) => setConflict(value as 'skip' | 'overwrite')}
                  >
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="skip" id="learning-import-skip" />
                      <Label htmlFor="learning-import-skip">跳过</Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="overwrite" id="learning-import-overwrite" />
                      <Label htmlFor="learning-import-overwrite">覆盖</Label>
                    </div>
                  </RadioGroup>
                  <p className="text-muted-foreground text-xs">
                    表达方式会跳过情境和风格都相同的条目；表情包按图片内容判断，已存在的会重新启用。
                  </p>
                  {kinds.emoji && bundle.emojis.length > 0 && (
                    <div className="space-y-1 pt-1">
                      <Label htmlFor="learning-import-emotion" className="text-xs">
                        表情包情感标签（可选）
                      </Label>
                      <Input
                        id="learning-import-emotion"
                        controlSize="sm"
                        value={emojiEmotion}
                        placeholder="例如：开心"
                        onChange={(event) => setEmojiEmotion(event.target.value)}
                      />
                    </div>
                  )}
                </section>
              </div>
            </>
          )}

          {step === 3 && results && (
            <div className="divide-border divide-y rounded-md border" aria-live="polite">
              {results.map((result) => (
                <div key={result.kind} className="flex items-center gap-3 px-3 py-2.5 text-sm">
                  <span className="font-semibold">{KIND_META[result.kind].name}</span>
                  <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
                    {result.error}
                  </span>
                  <span className="tabular-nums">
                    成功 {result.imported} · 跳过 {result.skipped} · 失败 {result.failed}
                  </span>
                </div>
              ))}
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          {step === 1 && createKind && level === 'manual' && (
            <Button variant="ghost" className="mr-auto" onClick={() => setLevel('root')}>
              <ArrowLeft className="h-4 w-4" />
              返回
            </Button>
          )}
          {step === 2 && (
            <Button
              variant="ghost"
              className="mr-auto"
              onClick={() => setBundle(emptyBundle())}
              disabled={importing}
            >
              <X className="h-4 w-4" />
              清空文件
            </Button>
          )}
          <Button variant="outline" onClick={() => close(false)} disabled={importing}>
            {step === 3 ? '关闭' : '取消'}
          </Button>
          {step === 3 ? (
            <Button onClick={reset}>继续导入</Button>
          ) : step === 1 ? null : (
            <Button
              data-dialog-action="confirm"
              onClick={() => void handleImport()}
              disabled={!canImport}
            >
              <FileUp className="h-4 w-4" />
              {importing
                ? '导入中…'
                : chosen.length > 0
                  ? `导入 ${chosen.length} 类内容`
                  : '开始导入'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
