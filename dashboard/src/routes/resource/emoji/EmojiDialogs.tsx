import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Check, CheckCircle2, ImageIcon, Upload, X } from 'lucide-react'
import Dashboard from '@uppy/react/dashboard'
import Uppy from '@uppy/core'
import '@uppy/core/css/style.min.css'
import '@uppy/dashboard/css/style.min.css'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
import { Markdown } from '@/components/ui/markdown'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Textarea } from '@/components/ui/textarea'

import '@/styles/uppy-custom.css'
import { useToast } from '@/hooks/use-toast'
import {
  getEmojiOriginalUrl,
  getEmojiUploadUrl,
  updateEmoji,
} from '@/lib/emoji-api'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import type { Emoji } from '@/types/emoji'

import type { UploadedFileInfo, UploadStep } from './types'

// ============================
// 详情对话框组件
// ============================
export function EmojiDetailDialog({
  emoji,
  open,
  onOpenChange,
}: {
  emoji: Emoji | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  if (!emoji) return null

  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh]">
        <DialogHeader>
          <DialogTitle>表情包详情</DialogTitle>
        </DialogHeader>
        <ScrollArea className="max-h-[calc(90vh-8rem)] pr-4">
          <div className="space-y-4">
            {/* 表情包预览图 - 使用原图 */}
            <div className="flex justify-center">
              <div className="w-32 h-32 bg-muted rounded-lg flex items-center justify-center overflow-hidden">
                <img
                  src={getEmojiOriginalUrl(emoji.id)}
                  alt={emoji.description || '表情包'}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    const target = e.target as HTMLImageElement
                    target.style.display = 'none'
                    const parent = target.parentElement
                    if (parent) {
                      parent.innerHTML =
                        '<svg class="h-16 w-16 text-muted-foreground" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>'
                    }
                  }}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label className="text-muted-foreground">ID</Label>
                <div className="mt-1 font-mono">{emoji.id}</div>
              </div>
              <div>
                <Label className="text-muted-foreground">格式</Label>
                <div className="mt-1">
                  <Badge variant="outline">{emoji.format.toUpperCase()}</Badge>
                </div>
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">文件路径</Label>
              <div className="mt-1 font-mono text-sm break-all bg-muted p-2 rounded">
                {emoji.full_path}
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">哈希值</Label>
              <div className="mt-1 font-mono text-sm break-all bg-muted p-2 rounded">
                {emoji.emoji_hash}
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">描述</Label>
              {emoji.description ? (
                <div className="mt-1 rounded-lg border bg-muted/50 p-3">
                  <Markdown className="prose-sm">{emoji.description}</Markdown>
                </div>
              ) : (
                <div className="mt-1 text-sm text-muted-foreground">-</div>
              )}
            </div>

            <div>
              <Label className="text-muted-foreground">情绪</Label>
              <div className="mt-1">
                {emoji.emotion ? (
                  <span className="text-sm">{emoji.emotion}</span>
                ) : (
                  <span className="text-sm text-muted-foreground">-</span>
                )}
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label className="text-muted-foreground">状态</Label>
                <div className="mt-2 flex gap-2">
                  {emoji.is_registered && (
                    <Badge variant="default" className="bg-green-600">
                      已注册
                    </Badge>
                  )}
                  {emoji.is_banned && (
                    <Badge variant="destructive">已封禁</Badge>
                  )}
                  {!emoji.is_registered && !emoji.is_banned && (
                    <Badge variant="outline">未注册</Badge>
                  )}
                </div>
              </div>
              <div>
                <Label className="text-muted-foreground">使用次数</Label>
                <div className="mt-1 font-mono text-lg">
                  {emoji.usage_count}
                </div>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label className="text-muted-foreground">记录时间</Label>
                <div className="mt-1 text-sm">
                  {formatTime(emoji.record_time)}
                </div>
              </div>
              <div>
                <Label className="text-muted-foreground">注册时间</Label>
                <div className="mt-1 text-sm">
                  {formatTime(emoji.register_time)}
                </div>
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground">最后使用</Label>
              <div className="mt-1 text-sm">
                {formatTime(emoji.last_used_time)}
              </div>
            </div>
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

// ============================
// 编辑对话框组件
// ============================
export function EmojiEditDialog({
  emoji,
  open,
  onOpenChange,
  onSuccess,
}: {
  emoji: Emoji | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const [emotionInput, setEmotionInput] = useState('')
  const [isRegistered, setIsRegistered] = useState(false)
  const [isBanned, setIsBanned] = useState(false)
  const [saving, setSaving] = useState(false)

  const { toast } = useToast()

  useEffect(() => {
    if (emoji) {
      setEmotionInput(emoji.emotion || '')
      setIsRegistered(emoji.is_registered)
      setIsBanned(emoji.is_banned)
    }
  }, [emoji])

  const handleSave = async () => {
    if (!emoji) return

    try {
      setSaving(true)
      // 将输入的标签字符串标准化为逗号分隔格式
      const emotionString = emotionInput
        .split(/[,,]/)
        .map((s) => s.trim())
        .filter(Boolean)
        .join(',')

      await updateEmoji(emoji.id, {
        emotion: emotionString || undefined,
        is_registered: isRegistered,
        is_banned: isBanned,
      })

      toast({
        title: '成功',
        description: '表情包信息已更新',
      })
      onOpenChange(false)
      onSuccess()
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存失败'
      toast({
        title: '错误',
        description: message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  if (!emoji) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>编辑表情包</DialogTitle>
          <DialogDescription>修改表情包的情绪和状态信息</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label>情绪</Label>
            <Textarea
              value={emotionInput}
              onChange={(e) => setEmotionInput(e.target.value)}
              placeholder="输入情绪描述..."
              rows={2}
              className="mt-1"
            />
            <p className="text-xs text-muted-foreground mt-1">
              输入情绪相关的文本描述
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex items-center space-x-2">
              <Checkbox
                id="is_registered"
                checked={isRegistered}
                onCheckedChange={(checked) => {
                  if (checked === true) {
                    setIsRegistered(true)
                    setIsBanned(false) // 注册时自动取消封禁
                  } else {
                    setIsRegistered(false)
                  }
                }}
              />
              <Label htmlFor="is_registered" className="cursor-pointer">
                已注册
              </Label>
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="is_banned"
                checked={isBanned}
                onCheckedChange={(checked) => {
                  if (checked === true) {
                    setIsBanned(true)
                    setIsRegistered(false) // 封禁时自动取消注册
                  } else {
                    setIsBanned(false)
                  }
                }}
              />
              <Label htmlFor="is_banned" className="cursor-pointer">
                已封禁
              </Label>
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

// ============================
// 上传对话框组件
// ============================
export function EmojiUploadDialog({
  open,
  onOpenChange,
  onSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const [step, setStep] = useState<UploadStep>('select')
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileInfo[]>([])
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const { toast } = useToast()

  // 创建 Uppy 实例（仅用于文件选择，不自动上传）
  const uppy = useMemo(() => {
    const uppyInstance = new Uppy({
      id: 'emoji-uploader',
      autoProceed: false,
      restrictions: {
        maxFileSize: 10 * 1024 * 1024, // 10MB
        allowedFileTypes: [
          'image/jpeg',
          'image/png',
          'image/gif',
          'image/webp',
        ],
        maxNumberOfFiles: 20,
      },
      locale: {
        pluralize: () => 0,
        strings: {
          addMoreFiles: '添加更多文件',
          addingMoreFiles: '正在添加更多文件',
          allowedFileTypes: '允许的文件类型:%{types}',
          cancel: '取消',
          closeModal: '关闭',
          complete: '完成',
          connectedToInternet: '已连接到互联网',
          copyLink: '复制链接',
          copyLinkToClipboardFallback: '复制下方链接',
          copyLinkToClipboardSuccess: '链接已复制到剪贴板',
          dashboardTitle: '选择文件',
          dashboardWindowTitle: '文件选择窗口(按 ESC 关闭)',
          done: '完成',
          dropHereOr: '拖放文件到这里或 %{browse}',
          dropHint: '将文件拖放到此处',
          dropPasteFiles: '将文件拖放到这里或 %{browseFiles}',
          dropPasteFolders: '将文件拖放到这里或 %{browseFolders}',
          dropPasteBoth: '将文件拖放到这里,%{browseFiles} 或 %{browseFolders}',
          dropPasteImportFiles:
            '将文件拖放到这里,%{browseFiles} 或从以下位置导入:',
          dropPasteImportFolders:
            '将文件拖放到这里,%{browseFolders} 或从以下位置导入:',
          dropPasteImportBoth:
            '将文件拖放到这里,%{browseFiles},%{browseFolders} 或从以下位置导入:',
          editFile: '编辑文件',
          editing: '正在编辑 %{file}',
          emptyFolderAdded: '未从空文件夹添加文件',
          exceedsSize: '%{file} 超过了最大允许大小 %{size}',
          failedToUpload: '上传 %{file} 失败',
          fileSource: '文件来源:%{name}',
          filesUploadedOfTotal: {
            0: '已上传 %{complete} / %{smart_count} 个文件',
            1: '已上传 %{complete} / %{smart_count} 个文件',
          },
          filter: '筛选',
          finishEditingFile: '完成编辑文件',
          folderAdded: {
            0: '已从 %{folder} 添加 %{smart_count} 个文件',
            1: '已从 %{folder} 添加 %{smart_count} 个文件',
          },
          generatingThumbnails: '正在生成缩略图...',
          import: '导入',
          importFiles: '从以下位置导入文件:',
          importFrom: '从 %{name} 导入',
          loading: '加载中...',
          logOut: '登出',
          myDevice: '我的设备',
          noFilesFound: '这里没有文件或文件夹',
          noInternetConnection: '无网络连接',
          openFolderNamed: '打开文件夹 %{name}',
          pause: '暂停',
          pauseUpload: '暂停上传',
          paused: '已暂停',
          poweredBy: '技术支持:%{uppy}',
          processingXFiles: {
            0: '正在处理 %{smart_count} 个文件',
            1: '正在处理 %{smart_count} 个文件',
          },
          recording: '录制中',
          removeFile: '移除文件',
          resetFilter: '重置筛选',
          resume: '继续',
          resumeUpload: '继续上传',
          retry: '重试',
          retryUpload: '重试上传',
          save: '保存',
          saveChanges: '保存更改',
          selectFileNamed: '选择文件 %{name}',
          selectX: {
            0: '选择 %{smart_count}',
            1: '选择 %{smart_count}',
          },
          smile: '笑一个!',
          startRecording: '开始录制视频',
          stopRecording: '停止录制视频',
          takePicture: '拍照',
          timedOut: '上传已停滞 %{seconds} 秒,正在中止。',
          upload: '下一步',
          uploadComplete: '上传完成',
          uploadFailed: '上传失败',
          uploadPaused: '上传已暂停',
          uploadXFiles: {
            0: '下一步(%{smart_count} 个文件)',
            1: '下一步(%{smart_count} 个文件)',
          },
          uploadXNewFiles: {
            0: '下一步(+%{smart_count} 个文件)',
            1: '下一步(+%{smart_count} 个文件)',
          },
          uploading: '正在上传',
          uploadingXFiles: {
            0: '正在上传 %{smart_count} 个文件',
            1: '正在上传 %{smart_count} 个文件',
          },
          xFilesSelected: {
            0: '已选择 %{smart_count} 个文件',
            1: '已选择 %{smart_count} 个文件',
          },
          xMoreFilesAdded: {
            0: '又添加了 %{smart_count} 个文件',
            1: '又添加了 %{smart_count} 个文件',
          },
          xTimeLeft: '剩余 %{time}',
          youCanOnlyUploadFileTypes: '您只能上传:%{types}',
          youCanOnlyUploadX: {
            0: '您只能上传 %{smart_count} 个文件',
            1: '您只能上传 %{smart_count} 个文件',
          },
          youHaveToAtLeastSelectX: {
            0: '您至少需要选择 %{smart_count} 个文件',
            1: '您至少需要选择 %{smart_count} 个文件',
          },
          browseFiles: '浏览文件',
          browseFolders: '浏览文件夹',
          cancelUpload: '取消上传',
          addMore: '添加更多',
          back: '返回',
          editFileWithFilename: '编辑文件 %{file}',
        },
      },
    })

    return uppyInstance
  }, [])

  // 处理"下一步"按钮点击 - 进入编辑阶段
  useEffect(() => {
    const handleUpload = () => {
      const files = uppy.getFiles()
      if (files.length === 0) return

      // 将选择的文件转换为我们的数据结构
      const fileInfos: UploadedFileInfo[] = files.map((file) => ({
        id: file.id,
        name: file.name,
        previewUrl: file.preview || URL.createObjectURL(file.data as File),
        emotion: '',
        description: '',
        isRegistered: true,
        file: file.data as File,
      }))

      setUploadedFiles(fileInfos)

      // 根据文件数量决定进入哪个步骤
      if (files.length === 1) {
        setSelectedFileId(fileInfos[0].id)
        setStep('edit-single')
      } else {
        setStep('edit-multiple')
      }
    }

    uppy.on('upload', handleUpload)
    return () => {
      uppy.off('upload', handleUpload)
    }
  }, [uppy])

  // 对话框关闭时重置状态
  useEffect(() => {
    if (!open) {
      uppy.cancelAll()
      setStep('select')
      setUploadedFiles([])
      setSelectedFileId(null)
      setUploading(false)
    }
  }, [open, uppy])

  // 更新单个文件的元数据
  const updateFileInfo = useCallback(
    (fileId: string, updates: Partial<UploadedFileInfo>) => {
      setUploadedFiles((prev) =>
        prev.map((f) => (f.id === fileId ? { ...f, ...updates } : f))
      )
    },
    []
  )

  // 检查文件是否填写完成必填项(情感标签必填)
  const isFileComplete = useCallback((file: UploadedFileInfo) => {
    return file.emotion.trim().length > 0
  }, [])

  // 检查所有文件是否都填写完成
  const allFilesComplete = useMemo(() => {
    return uploadedFiles.length > 0 && uploadedFiles.every(isFileComplete)
  }, [uploadedFiles, isFileComplete])

  // 获取当前选中的文件
  const selectedFile = useMemo(() => {
    return uploadedFiles.find((f) => f.id === selectedFileId) || null
  }, [uploadedFiles, selectedFileId])

  // 返回上一步
  const handleBack = useCallback(() => {
    if (step === 'edit-single' || step === 'edit-multiple') {
      setStep('select')
      setUploadedFiles([])
      setSelectedFileId(null)
    }
  }, [step])

  // 执行实际上传
  const handleSubmit = useCallback(async () => {
    if (!allFilesComplete) {
      toast({
        title: '请填写必填项',
        description: '每个表情包的情感标签都是必填的',
        variant: 'destructive',
      })
      return
    }

    setUploading(true)
    let successCount = 0
    let failedCount = 0

    try {
      for (const fileInfo of uploadedFiles) {
        const formData = new FormData()
        formData.append('file', fileInfo.file)
        formData.append('emotion', fileInfo.emotion)
        formData.append('description', fileInfo.description)
        formData.append('is_registered', fileInfo.isRegistered.toString())

        try {
          const response = await fetchWithAuth(getEmojiUploadUrl(), {
            method: 'POST',
            body: formData,
          })

          if (response.ok) {
            successCount++
          } else {
            failedCount++
          }
        } catch {
          failedCount++
        }
      }

      if (failedCount === 0) {
        toast({
          title: '上传成功',
          description: `成功上传 ${successCount} 个表情包`,
        })
        onOpenChange(false)
        onSuccess()
      } else {
        toast({
          title: '部分上传失败',
          description: `成功 ${successCount} 个,失败 ${failedCount} 个`,
          variant: 'destructive',
        })
        onSuccess()
      }
    } finally {
      setUploading(false)
    }
  }, [allFilesComplete, uploadedFiles, toast, onOpenChange, onSuccess])

  // 渲染文件选择步骤
  const renderSelectStep = () => (
    <div className="space-y-4">
      <div className="border rounded-lg overflow-hidden w-full">
        <Dashboard
          uppy={uppy}
          proudlyDisplayPoweredByUppy={false}
          hideProgressDetails
          height={350}
          width="100%"
          theme="auto"
          note="支持 JPG、PNG、GIF、WebP 格式,最多 20 个文件"
        />
      </div>
    </div>
  )

  // 渲染单个文件编辑步骤
  const renderEditSingleStep = () => {
    const file = uploadedFiles[0]
    if (!file) return null

    return (
      <div className="space-y-4">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={handleBack}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <span className="text-sm text-muted-foreground">
            编辑表情包信息
          </span>
        </div>

        <div className="flex gap-6">
          {/* 预览图 */}
          <div className="flex-shrink-0">
            <div className="w-32 h-32 rounded-lg border overflow-hidden bg-muted flex items-center justify-center">
              <img
                src={file.previewUrl}
                alt={file.name}
                className="max-w-full max-h-full object-contain"
              />
            </div>
            <p className="text-xs text-muted-foreground mt-2 text-center truncate max-w-32">
              {file.name}
            </p>
          </div>

          {/* 表单 */}
          <div className="flex-1 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="single-emotion">
                情感标签 <span className="text-destructive">*</span>
              </Label>
              <Input
                id="single-emotion"
                value={file.emotion}
                onChange={(e) =>
                  updateFileInfo(file.id, { emotion: e.target.value })
                }
                placeholder="多个标签用逗号分隔,如:开心,高兴"
                className={!file.emotion.trim() ? 'border-destructive' : ''}
              />
              <p className="text-xs text-muted-foreground">
                用于情感匹配,多个标签用逗号分隔
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="single-description">描述</Label>
              <Input
                id="single-description"
                value={file.description}
                onChange={(e) =>
                  updateFileInfo(file.id, { description: e.target.value })
                }
                placeholder="输入表情包描述..."
              />
            </div>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="single-is-registered"
                checked={file.isRegistered}
                onCheckedChange={(checked) =>
                  updateFileInfo(file.id, { isRegistered: checked === true })
                }
              />
              <Label htmlFor="single-is-registered" className="cursor-pointer">
                上传后立即注册(可被麦麦使用)
              </Label>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            onClick={handleSubmit}
            disabled={!allFilesComplete || uploading}
          >
            {uploading ? '上传中...' : '上传'}
          </Button>
        </DialogFooter>
      </div>
    )
  }

  // 渲染多个文件编辑步骤
  const renderEditMultipleStep = () => {
    const completedCount = uploadedFiles.filter(isFileComplete).length
    const totalCount = uploadedFiles.length

    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Button variant="ghost" size="sm" onClick={handleBack}>
              <ArrowLeft className="h-4 w-4 mr-1" />
              返回
            </Button>
            <span className="text-sm text-muted-foreground">
              编辑表情包信息({completedCount}/{totalCount} 已完成)
            </span>
          </div>
          <Badge variant={allFilesComplete ? 'default' : 'secondary'}>
            {allFilesComplete ? (
              <>
                <Check className="h-3 w-3 mr-1" />
                全部完成
              </>
            ) : (
              <>
                <X className="h-3 w-3 mr-1" />
                未完成
              </>
            )}
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {/* 左侧:文件卡片列表 */}
          <ScrollArea className="h-[350px] pr-2">
            <div className="space-y-2">
              {uploadedFiles.map((file) => {
                const complete = isFileComplete(file)
                const isSelected = selectedFileId === file.id
                return (
                  <div
                    key={file.id}
                    onClick={() => setSelectedFileId(file.id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedFileId(file.id) } }}
                    className={`
                      flex items-center gap-3 p-3 rounded-lg border-2 cursor-pointer transition-all
                      ${isSelected ? 'ring-2 ring-primary' : ''}
                      ${complete ? 'border-green-500 bg-green-50 dark:bg-green-950/20' : 'border-border hover:border-muted-foreground/50'}
                    `}
                  >
                    <div className="w-12 h-12 rounded border overflow-hidden bg-muted flex-shrink-0 flex items-center justify-center">
                      <img
                        src={file.previewUrl}
                        alt={file.name}
                        className="max-w-full max-h-full object-contain"
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{file.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {file.emotion || '未填写情感标签'}
                      </p>
                    </div>
                    {complete ? (
                      <CheckCircle2 className="h-5 w-5 text-green-500 flex-shrink-0" />
                    ) : (
                      <div className="h-5 w-5 rounded-full border-2 border-muted-foreground/30 flex-shrink-0" />
                    )}
                  </div>
                )
              })}
            </div>
          </ScrollArea>

          {/* 右侧:选中文件的编辑表单 */}
          <div className="border rounded-lg p-4">
            {selectedFile ? (
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <div className="w-16 h-16 rounded border overflow-hidden bg-muted flex items-center justify-center">
                    <img
                      src={selectedFile.previewUrl}
                      alt={selectedFile.name}
                      className="max-w-full max-h-full object-contain"
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium truncate">{selectedFile.name}</p>
                    {isFileComplete(selectedFile) && (
                      <Badge
                        variant="outline"
                        className="text-green-600 border-green-600"
                      >
                        <Check className="h-3 w-3 mr-1" />
                        已完成
                      </Badge>
                    )}
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="multi-emotion">
                    情感标签 <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    id="multi-emotion"
                    value={selectedFile.emotion}
                    onChange={(e) =>
                      updateFileInfo(selectedFile.id, {
                        emotion: e.target.value,
                      })
                    }
                    placeholder="多个标签用逗号分隔,如:开心,高兴"
                    className={
                      !selectedFile.emotion.trim() ? 'border-destructive' : ''
                    }
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="multi-description">描述</Label>
                  <Input
                    id="multi-description"
                    value={selectedFile.description}
                    onChange={(e) =>
                      updateFileInfo(selectedFile.id, {
                        description: e.target.value,
                      })
                    }
                    placeholder="输入表情包描述..."
                  />
                </div>

                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="multi-is-registered"
                    checked={selectedFile.isRegistered}
                    onCheckedChange={(checked) =>
                      updateFileInfo(selectedFile.id, {
                        isRegistered: checked === true,
                      })
                    }
                  />
                  <Label
                    htmlFor="multi-is-registered"
                    className="cursor-pointer text-sm"
                  >
                    上传后立即注册
                  </Label>
                </div>
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-muted-foreground">
                <div className="text-center">
                  <ImageIcon className="h-12 w-12 mx-auto mb-2 opacity-50" />
                  <p>点击左侧卡片编辑</p>
                </div>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            onClick={handleSubmit}
            disabled={!allFilesComplete || uploading}
          >
            {uploading ? '上传中...' : `上传全部 (${totalCount})`}
          </Button>
        </DialogFooter>
      </div>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5" />
            {step === 'select' && '上传表情包 - 选择文件'}
            {step === 'edit-single' && '上传表情包 - 填写信息'}
            {step === 'edit-multiple' && '上传表情包 - 批量编辑'}
          </DialogTitle>
          <DialogDescription>
            {step === 'select' &&
              '支持 JPG、PNG、GIF、WebP 格式,单个文件最大 10MB,可同时上传多个文件'}
            {step === 'edit-single' && '请填写表情包的情感标签(必填)和描述'}
            {step === 'edit-multiple' &&
              '点击左侧卡片编辑每个表情包的信息,情感标签为必填项'}
          </DialogDescription>
        </DialogHeader>

        <div className="overflow-y-auto pr-1">
          {step === 'select' && renderSelectStep()}
          {step === 'edit-single' && renderEditSingleStep()}
          {step === 'edit-multiple' && renderEditMultipleStep()}
        </div>
      </DialogContent>
    </Dialog>
  )
}
