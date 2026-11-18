import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, BookOpen, Loader2, PenLine } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { LearningCandidateRow } from '@/components/learning-candidate-row'
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
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { Progress } from '@/components/ui/progress'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'
import { getExpressionChatTargets } from '@/lib/expression-api'
import { getJargonChatList } from '@/lib/jargon-api'
import {
  extractTextCandidates,
  importLearningCandidates,
  mergeLearningCandidates,
  prepareLearningText,
  type LearnedCandidate,
  type LearningKind,
} from '@/lib/text-learning-api'

interface LearningCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kind: LearningKind
  initialTargetIds?: string[]
  onManual: () => void
  onSuccess: (isGlobal: boolean) => void
  /** 'auto' 时直接进入自动学习（方式已在外部入口选过），不显示「返回选择」 */
  initialMode?: 'choose' | 'auto'
}

type ChunkState = { status: 'pending' | 'succeeded' | 'failed'; error?: string }

/** 表达和黑话共用新增入口；各段独立记录结果，用户可随时确认已学到的候选。 */
export function LearningCreateDialog({
  open,
  onOpenChange,
  kind,
  initialTargetIds = [],
  onManual,
  onSuccess,
  initialMode = 'choose',
}: LearningCreateDialogProps) {
  const [mode, setMode] = useState<'choose' | 'auto'>(initialMode)
  const [text, setText] = useState('')
  const [targetIds, setTargetIds] = useState<string[]>(initialTargetIds)
  const [isGlobal, setIsGlobal] = useState(false)
  const [chunks, setChunks] = useState<string[]>([])
  const [chunkStates, setChunkStates] = useState<ChunkState[]>([])
  const [activeChunkIndex, setActiveChunkIndex] = useState<number | null>(null)
  const [candidates, setCandidates] = useState<LearnedCandidate[]>([])
  const [candidateEdits, setCandidateEdits] = useState<Record<number, LearnedCandidate>>({})
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [running, setRunning] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')
  const [importResult, setImportResult] = useState('')
  const [importedByTarget, setImportedByTarget] = useState<Record<string, Set<number>>>({})
  const stopRequested = useRef(false)
  const learningBusy = useRef(false)
  const importBusy = useRef(false)
  useEffect(
    () => () => {
      stopRequested.current = true
    },
    []
  )
  const { toast } = useToast()
  const noun = kind === 'expression' ? '表达方式' : '黑话'
  const failedIndices = chunkStates.flatMap((chunk, index) =>
    chunk.status === 'failed' ? [index] : []
  )
  const succeededCount = chunkStates.filter((chunk) => chunk.status === 'succeeded').length
  const completed = succeededCount + failedIndices.length
  const finished = chunks.length > 0 && completed === chunks.length
  const targetQuery = useQuery({
    queryKey: ['text-learning', 'targets', kind],
    enabled: open && mode === 'auto',
    queryFn: async () =>
      kind === 'expression'
        ? (await getExpressionChatTargets()).map((chat) => ({
            value: chat.chat_id,
            label: formatChatDisplayName(chat.chat_name, chat.account_id),
          }))
        : (await getJargonChatList({ include_empty: true })).data.map((chat) => ({
            value: chat.session_id,
            label: formatChatDisplayName(chat.chat_name, chat.account_id),
          })),
  })
  const targets = targetQuery.data ?? []
  const validTargetIds = targetIds.filter((id) => targets.some((target) => target.value === id))
  const targetKey = isGlobal ? 'global' : JSON.stringify([...validTargetIds].sort())
  const importedIndices = importedByTarget[targetKey]
  const availableIndices = candidates.flatMap((_, index) =>
    importedIndices?.has(index) ? [] : [index]
  )
  const selectedIndices = availableIndices.filter((index) => selected.has(index))
  const editedCandidates = candidates.map((candidate, index) => candidateEdits[index] ?? candidate)
  const invalidSelection = selectedIndices.some(
    (index) => !editedCandidates[index].title.trim() || !editedCandidates[index].description.trim()
  )

  const resetLearning = () => {
    if (learningBusy.current || importBusy.current) return
    setChunks([])
    setChunkStates([])
    setActiveChunkIndex(null)
    setCandidates([])
    setCandidateEdits({})
    setSelected(new Set())
    setError('')
    setImportResult('')
    setImportedByTarget({})
  }

  const learn = async (retryIndices?: number[]) => {
    if (learningBusy.current) return
    learningBusy.current = true
    stopRequested.current = false
    setRunning(true)
    setStopping(false)
    setError('')
    try {
      const parts = chunks.length ? chunks : await prepareLearningText(text)
      setChunks(parts)
      const states: ChunkState[] = chunks.length
        ? [...chunkStates]
        : parts.map(() => ({ status: 'pending' }))
      setChunkStates([...states])
      const queue =
        retryIndices ??
        states.flatMap((chunk, index) => (chunk.status === 'pending' ? [index] : []))
      let collected = candidates
      // 单段失败只记录该段的错误，继续队列；重试仅处理指定失败段，不重复已成功的段落。
      for (const index of queue) {
        if (stopRequested.current) break
        setActiveChunkIndex(index)
        try {
          const extracted = parts[index].trim()
            ? await extractTextCandidates(kind, parts[index])
            : []
          const merged = mergeLearningCandidates(collected, extracted, kind)
          const previousCount = collected.length
          setSelected((previous) => {
            const next = new Set(previous)
            for (let added = previousCount; added < merged.length; added++) next.add(added)
            return next
          })
          collected = merged
          setCandidates(merged)
          states[index] = { status: 'succeeded' }
        } catch (cause) {
          states[index] = {
            status: 'failed',
            error: cause instanceof Error ? cause.message : '该段学习失败',
          }
        }
        setChunkStates([...states])
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '学习失败，请重试当前段')
    } finally {
      learningBusy.current = false
      setRunning(false)
      setStopping(false)
      setActiveChunkIndex(null)
    }
  }

  const importSelected = async () => {
    if (
      importBusy.current ||
      !selectedIndices.length ||
      (!isGlobal && !validTargetIds.length) ||
      invalidSelection
    )
      return
    importBusy.current = true
    setImporting(true)
    setError('')
    try {
      const result = await importLearningCandidates(
        kind,
        isGlobal ? [] : validTargetIds,
        selectedIndices.map((index) => editedCandidates[index]),
        isGlobal
      )
      if (!result.success) throw new Error(result.message)
      onSuccess(isGlobal)
      const message = `成功导入 ${result.imported_count} 条，跳过重复 ${result.skipped_count} 条，失败 ${result.failed_count} 条`
      if (result.failed_count > 0) setError(`${message}，可重新导入所选，已导入的重复项会跳过。`)
      else {
        setImportResult(message)
        // 仅标记本次提交的候选；学习或失败段重试随后新增的候选仍可继续导入。
        setImportedByTarget((previous) => ({
          ...previous,
          [targetKey]: new Set([...(previous[targetKey] ?? []), ...selectedIndices]),
        }))
        setSelected((previous) => {
          const next = new Set(previous)
          selectedIndices.forEach((index) => next.delete(index))
          return next
        })
      }
      toast({ title: '导入完成', description: message })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '导入失败')
    } finally {
      importBusy.current = false
      setImporting(false)
    }
  }

  const changeOpen = (next: boolean) => {
    if (learningBusy.current || importBusy.current) return
    if (!next) setMode(initialMode)
    onOpenChange(next)
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogContent
        className={
          mode === 'auto' ? 'max-w-3xl [--dialog-width:48rem]' : 'max-w-2xl [--dialog-width:40rem]'
        }
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>{mode === 'choose' ? `新增${noun}` : `自动学习${noun}`}</DialogTitle>
          <DialogDescription>
            {mode === 'choose'
              ? '选择添加方式'
              : '粘贴文段，已学到的结果可随时勾选导入，失败段可单独重试。'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {mode === 'choose' ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                className="hover:bg-accent focus-visible:ring-ring flex flex-col items-start gap-3 rounded-lg border p-5 text-left outline-none focus-visible:ring-2"
                onClick={() => {
                  onOpenChange(false)
                  onManual()
                }}
              >
                <PenLine className="text-muted-foreground h-6 w-6" />
                <span className="font-medium">手动新增</span>
                <span className="text-muted-foreground text-sm">适用于精确的手动输入直接添加</span>
              </button>
              <button
                type="button"
                className="hover:bg-accent focus-visible:ring-ring flex flex-col items-start gap-3 rounded-lg border p-5 text-left outline-none focus-visible:ring-2"
                onClick={() => {
                  setTargetIds(initialTargetIds)
                  setMode('auto')
                }}
              >
                <BookOpen className="text-muted-foreground h-6 w-6" />
                <span className="font-medium">自动学习</span>
                <span className="text-muted-foreground text-sm">
                  {kind === 'expression'
                    ? '适用于从文段中总结可用的表达'
                    : '适用于从文段中总结黑话及其含义'}
                </span>
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {chunks.length === 0 && (
                <div className="space-y-2">
                  <Label htmlFor={`${kind}-learning-text`}>学习文段</Label>
                  <Textarea
                    id={`${kind}-learning-text`}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder="粘贴聊天记录、文章或其他文段……"
                    rows={9}
                    maxLength={200000}
                    disabled={running}
                  />
                  <p className="text-muted-foreground text-xs">
                    {text.length.toLocaleString()} / 200,000 字 · 长文自动按段落、句子分段学习
                  </p>
                </div>
              )}
              {chunks.length > 0 && (
                <div className="space-y-2" aria-live="polite">
                  <div className="flex items-center justify-between gap-2 text-sm">
                    <span className="flex items-center gap-2">
                      {running && <Loader2 className="h-4 w-4 animate-spin" />}
                      {running
                        ? stopping
                          ? '完成当前段后暂停…'
                          : activeChunkIndex !== null
                            ? `正在学习第 ${activeChunkIndex + 1} 段`
                            : '正在准备学习…'
                        : finished
                          ? failedIndices.length
                            ? '本轮学习结束，部分段落失败'
                            : '学习完成'
                          : '学习已暂停'}
                    </span>
                    <span className="text-muted-foreground tabular-nums">
                      {completed} / {chunks.length} 段
                    </span>
                  </div>
                  <Progress aria-label="学习进度" value={(completed / chunks.length) * 100} />
                  <p className="text-muted-foreground text-xs">
                    成功 {succeededCount} 段 · 失败 {failedIndices.length} 段 · 已提取{' '}
                    {candidates.length} 条候选，跨段重复已合并
                  </p>
                </div>
              )}
              {finished && !failedIndices.length && !candidates.length && (
                <p className="text-muted-foreground py-6 text-center text-sm">
                  这段素材中没有找到可用的{noun}，可以更换文段再试。
                </p>
              )}
              {candidates.length > 0 && (
                <>
                  <div className="space-y-2">
                    <div role="group" aria-label="导入范围" className="flex flex-wrap gap-2">
                      <Button
                        variant={isGlobal ? 'outline' : 'default'}
                        size="sm"
                        aria-pressed={!isGlobal}
                        disabled={importing}
                        onClick={() => setIsGlobal(false)}
                      >
                        指定聊天
                      </Button>
                      <Button
                        variant={isGlobal ? 'default' : 'outline'}
                        size="sm"
                        aria-pressed={isGlobal}
                        disabled={importing}
                        onClick={() => setIsGlobal(true)}
                      >
                        全局（所有聊天）
                      </Button>
                    </div>
                    {isGlobal ? (
                      <p className="text-muted-foreground text-xs">
                        所有现有及以后新增的聊天均可使用，仍遵守各聊天的使用开关。
                      </p>
                    ) : (
                      <>
                        <Label htmlFor={`${kind}-learning-target`}>导入到聊天</Label>
                        {kind === 'expression' ? (
                          <Select
                            value={validTargetIds[0] ?? ''}
                            onValueChange={(id) => setTargetIds([id])}
                            disabled={importing}
                          >
                            <SelectTrigger id={`${kind}-learning-target`}>
                              <SelectValue placeholder="选择关联的聊天" />
                            </SelectTrigger>
                            <SelectContent>
                              {targets.map((target) => (
                                <SelectItem key={target.value} value={target.value}>
                                  {target.label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        ) : (
                          <MultiSelect
                            options={targets}
                            selected={validTargetIds}
                            onChange={setTargetIds}
                            placeholder="选择关联的聊天"
                            emptyText="没有可选聊天"
                            disabled={importing}
                          />
                        )}
                        {targetQuery.isPending && (
                          <p className="text-muted-foreground text-xs">正在加载聊天列表…</p>
                        )}
                        {targetQuery.isError && (
                          <div role="alert" className="text-destructive text-xs">
                            {targetQuery.error.message}
                            <Button variant="link" size="xs" onClick={() => targetQuery.refetch()}>
                              重新加载
                            </Button>
                          </div>
                        )}
                        {targetQuery.isSuccess && !targets.length && (
                          <p className="text-muted-foreground text-xs">
                            暂无可用聊天，请先创建聊天流。
                          </p>
                        )}
                      </>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-2 text-sm">
                    <label
                      htmlFor={`${kind}-learning-select-all`}
                      className="flex items-center gap-2"
                    >
                      <Checkbox
                        id={`${kind}-learning-select-all`}
                        checked={
                          selectedIndices.length > 0 &&
                          selectedIndices.length === availableIndices.length
                            ? true
                            : selectedIndices.length > 0
                              ? 'indeterminate'
                              : false
                        }
                        onCheckedChange={(checked) =>
                          setSelected(checked ? new Set(availableIndices) : new Set())
                        }
                        disabled={importing || !availableIndices.length}
                      />
                      全选
                    </label>
                    <span className="text-muted-foreground">
                      已选 {selectedIndices.length} / {availableIndices.length} 条
                    </span>
                  </div>
                  <div className="space-y-2">
                    {editedCandidates.map((candidate, index) => (
                      <LearningCandidateRow
                        key={index}
                        candidate={candidate}
                        index={index}
                        kind={kind}
                        selected={!importedIndices?.has(index) && selected.has(index)}
                        imported={importedIndices?.has(index) === true}
                        importing={importing}
                        onChange={(change) =>
                          setCandidateEdits((previous) => ({
                            ...previous,
                            [index]: { ...(previous[index] ?? candidates[index]), ...change },
                          }))
                        }
                        onSelectedChange={(checked) =>
                          setSelected((previous) => {
                            const next = new Set(previous)
                            if (checked) next.add(index)
                            else next.delete(index)
                            return next
                          })
                        }
                      />
                    ))}
                  </div>
                </>
              )}
              {failedIndices.length > 0 && (
                <section aria-label="失败段落" className="space-y-3 rounded-md border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-medium">失败段落（{failedIndices.length}）</h3>
                    <Button
                      variant="outline"
                      size="xs"
                      disabled={running}
                      onClick={() => learn(failedIndices)}
                    >
                      重试全部失败段
                    </Button>
                  </div>
                  {failedIndices.map((index) => (
                    <div key={index} className="space-y-2 border-t pt-2 text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <span>第 {index + 1} 段</span>
                        <Button
                          variant="outline"
                          size="xs"
                          disabled={running}
                          onClick={() => learn([index])}
                        >
                          重试第 {index + 1} 段
                        </Button>
                      </div>
                      <p className="text-destructive break-words">{chunkStates[index].error}</p>
                      <details>
                        <summary className="text-muted-foreground cursor-pointer text-xs">
                          查看第 {index + 1} 段原文
                        </summary>
                        <p className="mt-2 max-h-40 overflow-y-auto text-xs break-words whitespace-pre-wrap">
                          {chunks[index]}
                        </p>
                      </details>
                    </div>
                  ))}
                </section>
              )}
              {error && (
                <p role="alert" className="text-destructive text-sm">
                  {error}
                </p>
              )}
              {importResult && (
                <p role="status" className="text-sm">
                  {importResult}
                </p>
              )}
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          {mode === 'auto' && initialMode === 'choose' && (
            <Button
              variant="ghost"
              className="mr-auto"
              disabled={running || importing}
              onClick={() => setMode('choose')}
            >
              <ArrowLeft className="h-4 w-4" />
              返回选择
            </Button>
          )}
          {running ? (
            <Button
              variant="outline"
              disabled={stopping}
              onClick={() => {
                stopRequested.current = true
                setStopping(true)
              }}
            >
              暂停学习
            </Button>
          ) : (
            <Button variant="outline" disabled={importing} onClick={() => changeOpen(false)}>
              关闭
            </Button>
          )}
          {mode === 'auto' && !finished && (
            <>
              {chunks.length > 0 && (
                <Button variant="outline" disabled={running || importing} onClick={resetLearning}>
                  更换文段
                </Button>
              )}
              <Button disabled={running || !text.trim()} onClick={() => learn()}>
                {running ? '学习中…' : chunks.length ? '继续学习' : '开始学习'}
              </Button>
            </>
          )}
          {mode === 'auto' && finished && (
            <>
              <Button variant="outline" disabled={running || importing} onClick={resetLearning}>
                重新学习
              </Button>
            </>
          )}
          {mode === 'auto' && candidates.length > 0 && (
            <Button
              disabled={
                importing ||
                !selectedIndices.length ||
                (!isGlobal && !validTargetIds.length) ||
                invalidSelection
              }
              onClick={importSelected}
            >
              {importing ? '导入中…' : `导入所选（${selectedIndices.length}）`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
