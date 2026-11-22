import { useEffect, useRef, useState } from 'react'
import { FolderOpen, ImagePlus, Plus, Trash2, Upload } from 'lucide-react'

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { ApiError } from '@/lib/http/errors'
import { Checkbox } from '@/components/ui/checkbox'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Label } from '@/components/ui/label'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { getEmojiRecognitionModels, getEmojiUploadUrl, recognizeEmoji } from '@/lib/emoji-api'
import { backendApi } from '@/lib/http'
import { useToast } from '@/hooks/use-toast'

import './emoji-upload.css'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}

interface Draft {
  file: File
  preview: string
  emotion: string
  content: string
  error: string
}

const formats = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']

function splitTags(text: string) {
  return [...new Set(text.split(/[,，、\n]/).map((tag) => tag.trim()).filter(Boolean))]
}

function ready(draft: Draft) {
  return Boolean(splitTags(draft.emotion).length || splitTags(draft.content).length)
}

function BatchTagDialog({ field, drafts, disabled, onApply }: {
  field: 'content' | 'emotion'
  drafts: Draft[]
  disabled: boolean
  onApply: (items: Draft[]) => void
}) {
  const [value, setValue] = useState<string | null>(null)
  const label = field === 'content' ? '内容标签' : '情绪标签'
  const inputId = `emoji-batch-${field}`
  const additions = splitTags(value ?? '')

  function apply() {
    if (disabled || !additions.length) return
    onApply(drafts.map((item) => ({
      ...item,
      [field]: [...new Set([...splitTags(item[field]), ...additions])].join('、'),
    })))
    setValue(null)
  }

  return (
    <Dialog open={value !== null} onOpenChange={(open) => setValue(open ? '' : null)}>
      <DialogTrigger asChild><Button size="sm" variant="outline" className="px-1" disabled={disabled}>追加{label}</Button></DialogTrigger>
      <DialogContent className="[--dialog-width:26rem]" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>批量追加{label}</DialogTitle>
        </DialogHeader>
        <form className="grid min-h-0 gap-3" onSubmit={(event) => {
          event.preventDefault()
          apply()
        }}>
          <Label htmlFor={inputId}>追加的{label}</Label>
          <Input id={inputId} placeholder={field === 'content' ? '例如：橘发、眼镜' : '例如：开心、期待'}
            value={value ?? ''} onChange={(event) => setValue(event.target.value)} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setValue(null)}>取消</Button>
            <Button type="submit" disabled={disabled || !additions.length}>追加到全部</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function RecognitionModelDialog({ count, onClose, onConfirm }: {
  count: number
  onClose: () => void
  onConfirm: (model: string, remember: boolean) => void
}) {
  const [models, setModels] = useState<string[] | null>(null)
  const [model, setModel] = useState('')
  const [error, setError] = useState('')
  const [remember, setRemember] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    getEmojiRecognitionModels(controller.signal).then((names) => {
      if (controller.signal.aborted) return
      setModels(names)
      setModel(names[0] ?? '')
    }).catch((cause) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '获取识别模型失败')
    })
    return () => controller.abort()
  }, [])

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="[--dialog-width:26rem]">
        <DialogHeader>
          <DialogTitle>选择识别模型</DialogTitle>
          <DialogDescription>{count === 1 ? '为当前表情选择视觉模型。' : `用同一模型识别全部 ${count} 张表情。`}</DialogDescription>
        </DialogHeader>
        {error ? <p role="alert">{error}</p> : models === null ? <p role="status">正在加载模型…</p>
          : models.length === 0 ? <p>请先在模型配置中添加支持视觉输入的模型。</p>
          : <RadioGroup aria-label="识别模型" value={model} onValueChange={setModel} className="min-h-0 overflow-auto p-1">
            {models.map((name, i) => <div key={name} className="flex items-center gap-2">
              <RadioGroupItem id={`emoji-model-${i}`} value={name} />
              <Label htmlFor={`emoji-model-${i}`} className="break-all py-1">{name}</Label>
            </div>)}
          </RadioGroup>}
        {count === 1 && <div className="flex items-center gap-2">
          <Checkbox id="emoji-remember-model" checked={remember} onCheckedChange={(checked) => setRemember(checked === true)} />
          <Label htmlFor="emoji-remember-model">本批后续识别均用此模型</Label>
        </div>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button disabled={!model} onClick={() => onConfirm(model, remember)}>开始识别</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function EmojiUploadDialog(props: Props) {
  return props.open ? <UploadForm {...props} /> : null
}

function UploadForm({ onOpenChange, onSuccess }: Props) {
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [selected, setSelected] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [modelTargets, setModelTargets] = useState<Draft[] | null>(null)
  const [batchModel, setBatchModel] = useState('')
  const [recognition, setRecognition] = useState<{ items: Draft[]; index: number } | null>(null)
  const editingDuringRecognition = useRef(false)
  const editedFields = useRef({ emotion: new Set<string>(), content: new Set<string>() })
  const [uploadIndex, setUploadIndex] = useState<number | null>(null)
  const uploading = uploadIndex !== null
  const [duplicate, setDuplicate] = useState<Draft | null>(null)
  const [applyToAll, setApplyToAll] = useState(false)
  const batchOverwrite = useRef<boolean | null>(null)
  const overwriteDecision = useRef<((overwrite: boolean) => void) | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const previews = useRef(new Set<string>())
  const request = useRef<AbortController | null>(null)
  const { toast } = useToast()
  const draft = drafts[selected]
  const busy = uploading || recognition !== null
  const recognizingSelected = recognition !== null && recognition.items[recognition.index].preview === draft?.preview
  const tagsLocked = uploading || recognizingSelected

  const selectedTile = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    selectedTile.current?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
  }, [draft?.preview])

  useEffect(() => {
    const urls = previews.current
    return () => {
      request.current?.abort()
      overwriteDecision.current?.(false)
      urls.forEach((url) => URL.revokeObjectURL(url))
    }
  }, [])

  function add(files: FileList) {
    if (!files.length || busy) return
    if (drafts.length + files.length > 20) return setError('每次最多选择 20 张图片')
    const batch = Array.from(files)
    for (const file of batch) {
      if (!formats.includes(file.type)) return setError(`${file.name} 格式不支持`)
      if (file.size > 10 * 1024 * 1024) return setError(`${file.name} 超过 10 MB`)
    }
    const added = batch.map((file) => {
      const preview = URL.createObjectURL(file)
      previews.current.add(preview)
      return { file, preview, emotion: '', content: '', error: '' }
    })
    setDrafts([...drafts, ...added])
    setSelected(drafts.length)
    setError('')
  }

  function updateDraft(target: Draft, changes: Partial<Draft>) {
    setDrafts((items) => items.map((item) => item.preview === target.preview ? { ...item, ...changes } : item))
  }

  function editTag(field: 'emotion' | 'content', value: string) {
    if (recognition) editedFields.current[field].add(draft.preview)
    updateDraft(draft, { [field]: value })
  }

  function remove(item: Draft) {
    if (drafts.length === 1) setBatchModel('')
    URL.revokeObjectURL(item.preview)
    previews.current.delete(item.preview)
    setDrafts((items) => items.filter((entry) => entry.preview !== item.preview))
    setSelected((index) => Math.max(0, Math.min(index, drafts.length - 2)))
    setError('')
  }

  function chooseModel(items: Draft[]) {
    if (batchModel) void recognize(items, batchModel)
    else setModelTargets(items)
  }

  async function recognize(items: Draft[], model: string) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    let failed = 0
    editingDuringRecognition.current = false
    editedFields.current.emotion.clear()
    editedFields.current.content.clear()
    let firstFailure = -1
    setError('')
    try {
      for (const [i, item] of items.entries()) {
        if (controller.signal.aborted) return
        setRecognition({ items, index: i })
        try {
          const tags = await recognizeEmoji(item.file, controller.signal, model)
          if (controller.signal.aborted) return
          const changes: Partial<Draft> = { error: '' }
          if (!editedFields.current.emotion.has(item.preview)) changes.emotion = tags.emotion_tags.join('、')
          if (!editedFields.current.content.has(item.preview)) changes.content = tags.content_tags.join('、')
          updateDraft(item, changes)
        } catch (cause) {
          if (controller.signal.aborted) return
          if (!failed) firstFailure = drafts.indexOf(item)
          failed++
          updateDraft(item, { error: cause instanceof Error ? cause.message : '自动识别失败' })
        }
      }
      if (items.length > 1) {
        if (failed) {
          if (!editingDuringRecognition.current) setSelected(firstFailure)
          setError(`已识别 ${items.length - failed} 张，${failed} 张失败，可单独重试`)
        } else {
          toast({ title: `已识别全部 ${items.length} 张表情包` })
        }
      }
    } finally {
      request.current = null
      if (!controller.signal.aborted) setRecognition(null)
    }
  }

  function resolveDuplicate(overwrite: boolean, remember = false) {
    if (!overwriteDecision.current) return
    if (remember) batchOverwrite.current = overwrite
    overwriteDecision.current(overwrite)
    overwriteDecision.current = null
    setDuplicate(null)
  }

  async function submit() {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    batchOverwrite.current = null
    setUploadIndex(0)
    setError('')
    let skipped = 0
    const failed: Draft[] = []
    for (const [index, item] of drafts.entries()) {
      if (controller.signal.aborted) return
      setUploadIndex(index)
      const body = new FormData()
      body.append('file', item.file)
      body.append('tags_json', JSON.stringify({ emotion_tags: splitTags(item.emotion), content_tags: splitTags(item.content) }))
      const upload = () => backendApi.post(getEmojiUploadUrl(), { body, signal: controller.signal, parse: 'response', errorMessage: '上传表情包失败' })
      try {
        try {
          await upload()
        } catch (cause) {
          if (controller.signal.aborted) return
          if (!(cause instanceof ApiError) || cause.status !== 409) throw cause
          const overwrite = batchOverwrite.current ?? await new Promise<boolean>((resolve) => {
            overwriteDecision.current = resolve
            setApplyToAll(false)
            setDuplicate(item)
          })
          if (!overwrite) {
            skipped++
            continue
          }
          body.set('overwrite', 'true')
          await upload()
        }
      } catch (cause) {
        failed.push({ ...item, error: cause instanceof Error ? cause.message : '上传失败，请重试' })
      }
    }
    if (controller.signal.aborted) return
    request.current = null
    const count = drafts.length - failed.length - skipped
    const skippedText = skipped ? `，已跳过 ${skipped} 张重复图片` : ''
    if (count) onSuccess()
    setUploadIndex(null)
    if (failed.length) {
      setDrafts(failed)
      setSelected(0)
      setError(`已上传 ${count} 张${skippedText}，还有 ${failed.length} 张需要重试`)
    } else {
      toast({ title: '上传完成', description: `已上传 ${count} 张${skippedText}` })
      onOpenChange(false)
    }
  }

  return (
    <Dialog open onOpenChange={(next) => { if (!uploading) onOpenChange(next) }}>
      <DialogContent className={draft ? 'emoji-upload-dialog [--dialog-width:60rem]' : '[--dialog-width:36rem]'} onPaste={(event) => {
        if (event.clipboardData.files.length) {
          event.preventDefault()
          add(event.clipboardData.files)
        }
      }}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Upload className="size-5" />上传表情包</DialogTitle>
          <DialogDescription className={draft ? 'sr-only' : undefined}>
            {draft ? '可手动填写标签，也可点击“自动识别”生成标签后再修改。' : 'JPG、PNG、GIF、WebP · 单张最大 10 MB · 每次最多 20 张'}
          </DialogDescription>
        </DialogHeader>
        <input hidden type="file" aria-label="选择表情包文件" multiple accept={formats.join(',')} ref={input}
          onChange={(event) => { if (event.target.files) add(event.target.files); event.target.value = '' }} />
        <div className={draft ? 'emoji-upload-body' : 'min-h-0 space-y-4 overflow-y-auto'}>
          {!draft ? (
            <div data-dashboard-file-dropzone="true" data-dragging={dragging} className="flex flex-col items-center gap-3 px-4 py-8"
              onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => { event.preventDefault(); setDragging(false); add(event.dataTransfer.files) }}>
              <ImagePlus aria-hidden className="size-7 text-muted-foreground" />
              <p className="text-base font-semibold">将表情图片拖到这里</p>
              <Button onClick={() => input.current?.click()}><FolderOpen />浏览文件</Button>
            </div>
          ) : (
            <div className="emoji-upload-layout">
              <section aria-label="待上传图片" className="emoji-upload-queue">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="mr-auto font-semibold">待上传 <span className="text-sm text-muted-foreground">{drafts.length}</span></h3>
                  <Button size="icon-sm" variant="outline" title="添加图片" aria-label="添加图片" disabled={busy || drafts.length >= 20} onClick={() => input.current?.click()}><Plus /></Button>
                  {drafts.length > 1 && <Button size="sm" variant="outline" disabled={busy} onClick={() => chooseModel(drafts)}>
                    {recognition && recognition.items.length > 1 ? `识别中 ${recognition.index + 1}/${recognition.items.length}` : '识别全部'}
                  </Button>}
                </div>
                {batchModel && <Button size="sm" variant="outline" disabled={busy} title="重新选择识别模型"
                  onClick={() => setModelTargets([draft])}>
                  <span className="truncate">更换模型 · {batchModel}</span>
                </Button>}
                {drafts.length > 1 && <div className="grid grid-cols-2 gap-2">
                  {(['content', 'emotion'] as const).map((field) => (
                    <BatchTagDialog key={field} field={field} drafts={drafts} disabled={busy} onApply={setDrafts} />
                  ))}
                </div>}
                <TooltipProvider delayDuration={250}>
                  <div className="emoji-upload-strip emoji-upload-screen">
                    {drafts.map((item, i) => (
                      <Tooltip key={item.preview}>
                        <TooltipTrigger asChild>
                          <button type="button" ref={i === selected ? selectedTile : undefined}
                            className="emoji-upload-tile" aria-pressed={i === selected}
                            aria-label={`编辑第 ${i + 1} 张：${item.file.name}`} onClick={() => setSelected(i)}>
                            <img src={item.preview} alt="" className="size-full object-contain" />
                            <span className="absolute bottom-1 left-1 bg-background px-1 text-xs tabular-nums">{i + 1}</span>
                            {item.error && <span aria-label="处理失败" className="absolute right-1 top-1 bg-destructive px-1 text-xs text-white">!</span>}
                          </button>
                        </TooltipTrigger>
                        <TooltipContent side="right" collisionPadding={12} className="pointer-events-none w-44 p-2">
                          <img src={item.preview} alt="" className="h-36 w-full object-contain" />
                          <p className="mt-1 break-all">{item.file.name}</p>
                        </TooltipContent>
                      </Tooltip>
                    ))}
                  </div>
                </TooltipProvider>
              </section>
              <section aria-label="当前图片" className="emoji-upload-editor">
                <div className="flex items-center justify-between gap-3">
                  <h3 title={draft.file.name} className="truncate font-semibold">{draft.file.name}</h3>
                  <Button size="sm" variant="outline" aria-label="移除当前图片" disabled={busy} onClick={() => remove(draft)}><Trash2 />移除</Button>
                </div>
                <div className="emoji-upload-screen grid h-[clamp(8rem,24vh,15rem)] place-items-center bg-muted p-2">
                  <img className="h-full min-h-0 w-full object-contain" src={draft.preview} alt={draft.file.name} />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <h3 className="font-semibold">图片标签</h3>
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => chooseModel([draft])}>{recognizingSelected ? '识别中…' : '自动识别'}</Button>
                </div>
                <div className="grid gap-3 sm:grid-cols-2" onFocusCapture={() => { if (recognition) editingDuringRecognition.current = true }}>
                  <div className="space-y-2">
                    <Label htmlFor="emoji-upload-emotion">情绪标签</Label>
                    <Input id="emoji-upload-emotion" placeholder="例如：开心、期待" value={draft.emotion} disabled={tagsLocked}
                      onChange={(event) => editTag('emotion', event.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="emoji-upload-content">内容标签</Label>
                    <Input id="emoji-upload-content" placeholder="例如：橙猫、举爪" value={draft.content} disabled={tagsLocked}
                      onChange={(event) => editTag('content', event.target.value)} />
                  </div>
                </div>
                {draft.error && <p role="alert" className="text-sm text-destructive">{draft.error}</p>}
              </section>
            </div>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>
        {uploadIndex !== null && <div className="grid gap-2">
          <p role="status" className="truncate text-sm">
            {duplicate ? '等待确认重复表情' : '正在上传并计算标签索引'} · 第 {uploadIndex + 1}/{drafts.length} 张 · {drafts[uploadIndex]?.file.name}
          </p>
          <Progress aria-label="上传和索引进度" max={drafts.length} value={drafts.length === 1 ? null : uploadIndex}
            className={drafts.length === 1 ? 'motion-safe:animate-pulse' : undefined} />
        </div>}
        {draft && <DialogFooter className="flex-row flex-wrap items-center justify-end gap-2 border-t pt-3">
          <span className="mr-auto text-sm text-muted-foreground">已填写标签 {drafts.filter(ready).length} / {drafts.length}</span>
          <Button variant="outline" disabled={uploading} onClick={() => onOpenChange(false)}>取消</Button>
          <Button disabled={busy || !drafts.every(ready)} onClick={submit}>{uploading ? '上传中…' : `确认上传（${drafts.length}）`}</Button>
        </DialogFooter>}
        {modelTargets && <RecognitionModelDialog count={modelTargets.length} onClose={() => setModelTargets(null)}
          onConfirm={(model, remember) => {
            setBatchModel(remember ? model : '')
            setModelTargets(null)
            void recognize(modelTargets, model)
          }} />}
        <AlertDialog open={duplicate !== null} onOpenChange={(open) => { if (!open) resolveDuplicate(false) }}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>覆盖已有表情包？</AlertDialogTitle>
              <AlertDialogDescription className="break-all">
                表情库中已有与「{duplicate?.file.name}」相同的图片。覆盖将替换原有标签，并将该表情注册、锁定。
              </AlertDialogDescription>
            </AlertDialogHeader>
            {duplicate && <img src={duplicate.preview} alt="" className="mx-auto h-24 max-w-full object-contain" />}
            {drafts.length > 1 && <div className="flex items-center gap-2">
              <Checkbox id="emoji-overwrite-all" checked={applyToAll} onCheckedChange={(checked) => setApplyToAll(checked === true)} />
              <Label htmlFor="emoji-overwrite-all">应用到全部重复表情</Label>
            </div>}
            <AlertDialogFooter>
              <AlertDialogCancel onClick={() => resolveDuplicate(false, applyToAll)}>跳过这张</AlertDialogCancel>
              <AlertDialogAction onClick={() => resolveDuplicate(true, applyToAll)}>确认覆盖</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  )
}
