import { useEffect, useRef, useState } from 'react'
import { Link, Loader2, Trash2, Upload } from 'lucide-react'

import { useAssetStore } from '@/components/asset-provider'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { addAsset, getAsset } from '@/lib/asset-store'
import { cn } from '@/lib/utils'

type BackgroundUploaderProps = {
  assetId?: string
  onAssetSelect: (id: string | undefined) => void
  className?: string
}

export function BackgroundUploader({ assetId, onAssetSelect, className }: BackgroundUploaderProps) {
  const { getAssetUrl } = useAssetStore()
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | undefined>(undefined)
  const [assetType, setAssetType] = useState<'image' | 'video' | undefined>(undefined)
  const [urlInput, setUrlInput] = useState('')
  
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 加载预览
  useEffect(() => {
    let active = true

    const loadPreview = async () => {
      if (!assetId) {
        setPreviewUrl(undefined)
        setAssetType(undefined)
        return
      }

      try {
        const url = await getAssetUrl(assetId)
        const record = await getAsset(assetId)
        
        if (active) {
          if (url && record) {
            setPreviewUrl(url)
            setAssetType(record.type)
          } else {
            // 如果找不到资源，可能是被删除了
            onAssetSelect(undefined)
          }
        }
      } catch (err) {
        console.error('Failed to load asset preview:', err)
      }
    }

    loadPreview()

    return () => {
      active = false
    }
  }, [assetId, getAssetUrl, onAssetSelect])

  const handleFile = async (file: File) => {
    setError(null)
    setIsLoading(true)

    try {
      // 验证文件类型
      if (!file.type.startsWith('image/') && !file.type.startsWith('video/')) {
        throw new Error('不支持的文件类型。请上传图片或视频。')
      }

      // 验证文件大小 (例如限制 50MB)
      if (file.size > 50 * 1024 * 1024) {
        throw new Error('文件过大。请上传小于 50MB 的文件。')
      }

      const id = await addAsset(file)
      onAssetSelect(id)
      setUrlInput('') // 清空 URL 输入框
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setIsLoading(false)
    }
  }

  const handleUrlUpload = async () => {
    if (!urlInput) return

    setError(null)
    setIsLoading(true)

    try {
      const response = await fetch(urlInput)
      if (!response.ok) {
        throw new Error(`下载失败: ${response.statusText}`)
      }

      const blob = await response.blob()
      
      // 尝试从 Content-Type 或 URL 推断文件名和类型
      const contentType = response.headers.get('content-type') || ''
      const urlFilename = urlInput.split('/').pop() || 'downloaded-file'
      const filename = urlFilename.includes('.') ? urlFilename : `${urlFilename}.${contentType.split('/')[1] || 'bin'}`

      const file = new File([blob], filename, { type: contentType })
      await handleFile(file)
    } catch (err) {
      setError(err instanceof Error ? err.message : '从 URL 上传失败')
    } finally {
      setIsLoading(false)
    }
  }

  // 拖拽处理
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFile(e.dataTransfer.files[0])
    }
  }

  const handleClear = () => {
    onAssetSelect(undefined)
    setPreviewUrl(undefined)
    setAssetType(undefined)
    setError(null)
  }

  return (
    <div className={cn("space-y-4", className)}>
      <div className="grid gap-2">
        <Label>背景资源</Label>
        
        {/* 预览区域 / 上传区域 */}
        <div
          className={cn(
            "relative flex min-h-[200px] flex-col items-center justify-center rounded-lg border-2 border-dashed p-4 transition-colors",
            dragActive ? "border-primary bg-primary/5" : "border-muted-foreground/25",
            error ? "border-destructive/50 bg-destructive/5" : "",
            assetId ? "border-solid" : ""
          )}
          onDragEnter={handleDrag}
          onDragLeave={handleDrag}
          onDragOver={handleDrag}
          onDrop={handleDrop}
        >
          {isLoading ? (
            <div className="flex flex-col items-center gap-2 text-muted-foreground">
              <Loader2 className="h-8 w-8 animate-spin" />
              <p className="text-sm">处理中...</p>
            </div>
          ) : assetId && previewUrl ? (
            <div className="relative h-full w-full">
              {assetType === 'video' ? (
                <video 
                  src={previewUrl} 
                  className="h-full max-h-[300px] w-full rounded-md object-contain"
                  controls={false}
                  muted
                />
              ) : (
                <img 
                  src={previewUrl} 
                  alt="Background preview" 
                  className="h-full max-h-[300px] w-full rounded-md object-contain"
                />
              )}
              
              <div className="absolute right-2 top-2 flex gap-2">
                 <Button
                  variant="destructive"
                  size="icon"
                  className="h-8 w-8 shadow-sm"
                  onClick={handleClear}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
              
              <div className="absolute bottom-2 left-2 rounded bg-black/50 px-2 py-1 text-xs text-white backdrop-blur">
                {assetType === 'video' ? '视频' : '图片'}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4 text-center">
              <div className="rounded-full bg-muted p-4">
                <Upload className="h-8 w-8 text-muted-foreground" />
              </div>
              <div className="space-y-1">
                <p className="font-medium">点击或拖拽上传</p>
                <p className="text-xs text-muted-foreground">
                  支持 JPG, PNG, GIF, MP4, WebM
                </p>
              </div>
              <Button 
                variant="outline" 
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                选择文件
              </Button>
            </div>
          )}

          <Input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept="image/*,video/mp4,video/webm"
            onChange={(e) => {
              if (e.target.files?.[0]) {
                handleFile(e.target.files[0])
              }
              // 重置 value，允许重复选择同一文件
              e.target.value = ''
            }}
          />
        </div>

        {error && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}
      </div>

      {/* URL 上传 */}
      <div className="grid gap-2">
        <Label className="text-xs text-muted-foreground">或从 URL 获取</Label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Link className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="https://example.com/image.jpg"
              className="pl-9"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  handleUrlUpload()
                }
              }}
            />
          </div>
          <Button 
            variant="secondary" 
            onClick={handleUrlUpload}
            disabled={!urlInput || isLoading}
          >
            {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : '获取'}
          </Button>
        </div>
      </div>
    </div>
  )
}
