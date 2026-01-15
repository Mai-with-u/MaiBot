/**
 * 表情包缩略图组件
 * 
 * 特性：
 * - 自动处理 202 响应（缩略图生成中）
 * - 显示 Skeleton 占位符
 * - 自动重试加载
 * - 加载失败显示占位图标
 */

import { useState, useEffect, useCallback } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { ImageIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface EmojiThumbnailProps {
  src: string
  alt?: string
  className?: string
  /** 最大重试次数 */
  maxRetries?: number
  /** 重试间隔（毫秒） */
  retryInterval?: number
}

type LoadingState = 'loading' | 'loaded' | 'generating' | 'error'

export function EmojiThumbnail({
  src,
  alt = '表情包',
  className,
  maxRetries = 5,
  retryInterval = 1500,
}: EmojiThumbnailProps) {
  const [state, setState] = useState<LoadingState>('loading')
  const [retryCount, setRetryCount] = useState(0)
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [currentSrc, setCurrentSrc] = useState(src)

  // 当 src 变化时重置状态
  if (src !== currentSrc) {
    setState('loading')
    setRetryCount(0)
    setImageSrc(null)
    setCurrentSrc(src)
  }

  const loadImage = useCallback(async () => {
    try {
      const response = await fetch(src, {
        credentials: 'include', // 携带 Cookie
      })

      if (response.status === 202) {
        // 缩略图正在生成中
        setState('generating')
        
        if (retryCount < maxRetries) {
          // 延迟后重试
          setTimeout(() => {
            setRetryCount(prev => prev + 1)
          }, retryInterval)
        } else {
          // 超过最大重试次数，显示错误
          setState('error')
        }
        return
      }

      if (!response.ok) {
        setState('error')
        return
      }

      // 成功获取图片
      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)
      setImageSrc(objectUrl)
      setState('loaded')
    } catch (error) {
      console.error('加载缩略图失败:', error)
      setState('error')
    }
  }, [src, retryCount, maxRetries, retryInterval])

  useEffect(() => {
    loadImage()
  }, [loadImage])

  // 清理 Object URL
  useEffect(() => {
    return () => {
      if (imageSrc) {
        URL.revokeObjectURL(imageSrc)
      }
    }
  }, [imageSrc])

  // 加载中或生成中显示 Skeleton
  if (state === 'loading' || state === 'generating') {
    return (
      <Skeleton className={cn('w-full h-full', className)} />
    )
  }

  // 加载失败显示占位图标
  if (state === 'error' || !imageSrc) {
    return (
      <div className={cn('w-full h-full flex items-center justify-center bg-muted', className)}>
        <ImageIcon className="h-8 w-8 text-muted-foreground" />
      </div>
    )
  }

  // 加载成功显示图片
  return (
    <img
      src={imageSrc}
      alt={alt}
      className={cn('w-full h-full object-contain', className)}
    />
  )
}
