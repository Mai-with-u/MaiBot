import { ChevronLeft, ChevronRight, Download, ImageOff, Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ZoomableImage } from '@/components/zoomable-image'
import { useToast } from '@/hooks/use-toast'
import { backendApi } from '@/lib/http'
import { cn } from '@/lib/utils'

/** 图库中的一张图片；src 同时作为它在图库里的身份 */
export interface GalleryImage {
  src: string
  alt: string
  /** 地址需要带登录态读取时为 true：先取回文件再用本地地址显示 */
  authenticated?: boolean
}

interface ImageGalleryContextValue {
  open: (image: GalleryImage) => void
}

const ImageGalleryContext = createContext<ImageGalleryContextValue | null>(null)

/** 打开所在图库的大图预览；不在任何图库内时返回 null */
// eslint-disable-next-line react-refresh/only-export-components
export function useImageGallery() {
  return useContext(ImageGalleryContext)
}

type SourceState = { status: 'ready'; src: string } | { status: 'loading' } | { status: 'error' }

/** 解析可直接交给 img 的地址；需要登录态的图片取回后转成本地地址，切换或关闭时释放。 */
function useGalleryImageSource(image: GalleryImage | null): SourceState {
  const [fetched, setFetched] = useState<{ src: string; url: string } | { src: string; error: true }>()
  const remoteSrc = image?.authenticated ? image.src : null

  useEffect(() => {
    if (!remoteSrc) return
    let cancelled = false
    let objectUrl: string | null = null
    void backendApi
      .get<Blob>(remoteSrc, { parse: 'blob', cache: 'force-cache' })
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setFetched({ src: remoteSrc, url: objectUrl })
      })
      .catch(() => {
        if (!cancelled) setFetched({ src: remoteSrc, error: true })
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      // 本地地址释放后不能再用，重新打开同一张图时要重新读取。
      setFetched(undefined)
    }
  }, [remoteSrc])

  if (!image) return { status: 'loading' }
  if (!image.authenticated) return { status: 'ready', src: image.src }
  if (fetched?.src !== image.src) return { status: 'loading' }
  return 'error' in fetched ? { status: 'error' } : { status: 'ready', src: fetched.url }
}

/**
 * 图库：为其中的图片提供统一的大图预览，可缩放平移，并按 images 的顺序切换上一张/下一张。
 * images 只需包含当前已加载的图片；正在查看的图片被移出列表后仍可继续查看，只是不能再切换。
 */
export function ImageGalleryProvider({
  images,
  children,
}: {
  images: GalleryImage[]
  children: ReactNode
}) {
  const { t } = useTranslation()
  const { toast } = useToast()
  const [isSaving, setIsSaving] = useState(false)
  const [active, setActive] = useState<GalleryImage | null>(null)
  const contextValue = useMemo<ImageGalleryContextValue>(() => ({ open: setActive }), [])
  const source = useGalleryImageSource(active)

  const saveImage = async () => {
    if (source.status !== 'ready' || isSaving) return
    setIsSaving(true)
    try {
      // 使用预览已解析的地址，兼容需登录读取的图片、data URL 和普通图片。
      const response = await fetch(source.src)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const blob = await response.blob()
      const extensions: Record<string, string> = {
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/gif': 'gif',
        'image/webp': 'webp',
        'image/avif': 'avif',
        'image/svg+xml': 'svg',
        'image/bmp': 'bmp',
      }
      const extension = extensions[blob.type.split(';')[0]]
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `maimai-image-${Date.now()}${extension ? `.${extension}` : ''}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      // 给浏览器时间启动下载后再释放文件地址。
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      toast({
        title: t('chat.media.saveFailed', { defaultValue: '保存图片失败' }),
        description: t('chat.media.saveFailedDescription', { defaultValue: '无法下载这张图片，请稍后重试。' }),
        variant: 'destructive',
      })
    } finally {
      setIsSaving(false)
    }
  }

  const index = active ? images.findIndex((image) => image.src === active.src) : -1
  const previous = index > 0 ? images[index - 1] : null
  const next = index >= 0 && index < images.length - 1 ? images[index + 1] : null

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === 'ArrowLeft' && previous) setActive(previous)
      if (event.key === 'ArrowRight' && next) setActive(next)
    },
    [next, previous]
  )

  const navButtonClass =
    'absolute top-1/2 z-10 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-black/30 text-white opacity-30 transition-opacity hover:opacity-100 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-white/70 focus-visible:outline-none'

  return (
    <ImageGalleryContext value={contextValue}>
      {children}
      <Dialog open={active !== null} onOpenChange={(open) => !open && setActive(null)}>
        <DialogContent
          className="border-0 bg-black/95 p-2 text-white shadow-2xl [--dialog-width:72rem]"
          onKeyDown={handleKeyDown}
        >
          <DialogTitle className="sr-only">
            {t('chat.media.previewTitle', {
              type: active?.alt ?? '',
              defaultValue: `${active?.alt ?? ''}预览`,
            })}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {t('chat.media.galleryDescription', {
              defaultValue: '滚轮缩放，拖动平移，双击还原；左右方向键切换上一张、下一张。',
            })}
          </DialogDescription>
          <div className="relative flex h-[calc(100vh-4rem)] w-full items-center justify-center max-sm:h-[70dvh]">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="absolute top-2 left-2 z-10 gap-2"
              disabled={source.status !== 'ready' || isSaving}
              onClick={saveImage}
            >
              {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {t('chat.media.saveImage', { defaultValue: '保存图片' })}
            </Button>
            {source.status === 'ready' && active ? (
              <ZoomableImage
                // 换图后回到未缩放状态
                key={active.src}
                src={source.src}
                alt={active.alt}
                containerClassName="h-full w-full"
                className="max-h-full max-w-full rounded-md object-contain"
              />
            ) : source.status === 'error' ? (
              <span className="flex items-center gap-2 text-sm text-white/70">
                <ImageOff className="h-4 w-4" />
                {t('chat.media.loadFailed', { type: active?.alt ?? '' })}
              </span>
            ) : (
              <Loader2 className="h-6 w-6 animate-spin text-white/70" />
            )}
            {previous && (
              <button
                type="button"
                className={cn(navButtonClass, 'left-2')}
                aria-label={t('chat.media.previousImage', { defaultValue: '上一张' })}
                onClick={() => setActive(previous)}
              >
                <ChevronLeft className="h-6 w-6" />
              </button>
            )}
            {next && (
              <button
                type="button"
                className={cn(navButtonClass, 'right-2')}
                aria-label={t('chat.media.nextImage', { defaultValue: '下一张' })}
                onClick={() => setActive(next)}
              >
                <ChevronRight className="h-6 w-6" />
              </button>
            )}
            {index >= 0 && images.length > 1 && (
              <span className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 rounded bg-black/40 px-2 py-0.5 text-xs text-white/70 tabular-nums">
                {index + 1} / {images.length}
              </span>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </ImageGalleryContext>
  )
}

/** 保证子组件身处图库之内：外层已有图库时直接使用，否则为这一张图片单独建一个。 */
export function EnsureImageGallery({ image, children }: { image: GalleryImage; children: ReactNode }) {
  const gallery = useImageGallery()
  const images = useMemo(() => [image], [image])
  if (gallery) return <>{children}</>
  return <ImageGalleryProvider images={images}>{children}</ImageGalleryProvider>
}
