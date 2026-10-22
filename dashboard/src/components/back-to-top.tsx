import { useRouterState } from '@tanstack/react-router'
import { useDrag } from '@use-gesture/react'
import { ArrowUp } from 'lucide-react'
import { type CSSProperties, useEffect, useRef, useState } from 'react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

const HIDDEN_TRANSLATE_X = 128
const DRAG_THRESHOLD = 4
const VIEWPORT_MARGIN = 16
const BACK_TO_TOP_OFFSET_STORAGE_KEY = 'maibot-back-to-top-offset-y'

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)

function loadStoredDragOffsetY(): number {
  if (typeof window === 'undefined') {
    return 0
  }

  const stored = Number(localStorage.getItem(BACK_TO_TOP_OFFSET_STORAGE_KEY))
  return Number.isFinite(stored) ? stored : 0
}

export function BackToTop() {
  const [dragOffsetY, setDragOffsetY] = useState(loadStoredDragOffsetY)
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [visible, setVisible] = useState(false)
  const buttonRef = useRef<HTMLButtonElement | null>(null)
  const scrollerRef = useRef<HTMLElement | null>(null)
  const suppressClickRef = useRef(false)
  const locationKey = useRouterState({
    select: (state) => state.location.pathname,
  })
  const prefersTouchDrag = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches

  useEffect(() => {
    const handleScroll = (e: Event) => {
      const target = e.target

      // 只跟随布局的主滚动区；表格、列表和弹窗内部的滚动不能接管页面回顶。
      if (!(target instanceof HTMLElement) || !target.matches('[data-dashboard-main="true"]')) return

      scrollerRef.current = target
      const scrollTop = target.scrollTop
      const height = target.scrollHeight - target.clientHeight
      setProgress(height > 0 ? (scrollTop / height) * 100 : 0)
      setVisible(height > 100 && scrollTop > 300)
    }

    const handleNavigation = () => {
      scrollerRef.current = null
      setProgress(0)
      setVisible(false)
    }

    // scroll 不冒泡，在捕获阶段接收事件后只处理主滚动区。
    window.addEventListener('scroll', handleScroll, { capture: true, passive: true })
    window.addEventListener('popstate', handleNavigation)
    window.addEventListener('hashchange', handleNavigation)
    return () => {
      window.removeEventListener('scroll', handleScroll, { capture: true })
      window.removeEventListener('popstate', handleNavigation)
      window.removeEventListener('hashchange', handleNavigation)
    }
  }, [])

  useEffect(() => {
    scrollerRef.current = null
    const frameId = window.requestAnimationFrame(() => {
      setProgress(0)
      setVisible(false)
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [locationKey])

  useEffect(() => {
    localStorage.setItem(BACK_TO_TOP_OFFSET_STORAGE_KEY, String(dragOffsetY))
  }, [dragOffsetY])

  const scrollToTop = () => {
    scrollerRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const bindDrag = useDrag(
    ({ down, first, last, offset: [, nextOffsetY], tap, memo }) => {
      let nextBounds = memo

      if (first) {
        const rect = buttonRef.current?.getBoundingClientRect()
        // 拖拽始终基于当前可见位置计算边界，避免多次拖拽后累计误差。
        nextBounds = rect
          ? {
              minOffsetY: dragOffsetY + VIEWPORT_MARGIN - rect.top,
              maxOffsetY: dragOffsetY + window.innerHeight - VIEWPORT_MARGIN - rect.bottom,
            }
          : {
              minOffsetY: Number.NEGATIVE_INFINITY,
              maxOffsetY: Number.POSITIVE_INFINITY,
            }
        suppressClickRef.current = false
      }

      setDragging(down)

      // tap 仍然保留点击语义，只有真正发生位移时才更新 translateY。
      if (!tap && nextBounds) {
        setDragOffsetY(clamp(nextOffsetY, nextBounds.minOffsetY, nextBounds.maxOffsetY))
      }

      if (last) {
        setDragging(false)
        suppressClickRef.current = !tap
      }

      return nextBounds
    },
    {
      axis: 'y',
      eventOptions: { passive: false },
      filterTaps: true,
      from: () => [0, dragOffsetY],
      pointer: {
        buttons: prefersTouchDrag ? -1 : 1,
        keys: false,
        touch: prefersTouchDrag,
      },
      preventDefault: true,
      threshold: [0, 0],
      tapsThreshold: DRAG_THRESHOLD,
    }
  )

  const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false
      event.preventDefault()
      event.stopPropagation()
      return
    }
    scrollToTop()
  }

  // SVG 环形进度条参数
  const radius = 18
  const circumference = 2 * Math.PI * radius
  const strokeDashoffset = circumference - (progress / 100) * circumference
  const squareProgress = Math.max(0, Math.min(101, progress >= 99.5 ? 101 : progress))
  const squareProgressStyle = {
    '--back-to-top-progress': `${squareProgress}%`,
  } as CSSProperties
  const transformX = visible ? 0 : HIDDEN_TRANSLATE_X
  const wrapperStyle = {
    transform: `translate3d(${transformX}px, ${dragOffsetY}px, 0px)`,
  } as CSSProperties

  return (
    <div 
      className={cn(
        // 手机上底部是标签栏，回到顶部的悬浮按钮会与之重叠，因此只在桌面端显示
        "fixed right-6 bottom-24 z-50 transition-[transform,opacity] ease-in-out max-lg:hidden",
        dragging ? "duration-75" : "duration-500",
        visible ? "opacity-100" : "pointer-events-none opacity-0"
      )}
      style={wrapperStyle}
    >
      <Button
        {...bindDrag()}
        ref={buttonRef}
        variant="outline"
        size="icon"
        data-dashboard-back-to-top="true"
        className={cn(
          "relative h-10 w-10 rounded-full shadow-xl",
          "bg-background/80 backdrop-blur-md border-border/50",
          "hover:bg-accent hover:scale-105 hover:shadow-2xl hover:border-primary/50",
          "touch-none transition-all duration-300",
          dragging ? "cursor-grabbing" : "cursor-grab",
          "group"
        )}
        onClick={handleClick}
        aria-label="回到顶部"
      >
        {/* 进度环背景 */}
        <svg
          data-dashboard-back-to-top-progress="circle"
          className="absolute inset-0 h-full! w-full! -rotate-90 transform p-1"
          viewBox="0 0 44 44"
        >
          <circle
            className="text-muted-foreground/10"
            strokeWidth="3"
            stroke="currentColor"
            fill="transparent"
            r={radius}
            cx="22"
            cy="22"
          />
          {/* 进度环 */}
          <circle
            className="text-primary transition-all duration-100 ease-out"
            strokeWidth="3"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            stroke="currentColor"
            fill="transparent"
            r={radius}
            cx="22"
            cy="22"
          />
        </svg>

        <div
          data-dashboard-back-to-top-progress="square"
          className="pointer-events-none absolute inset-0 hidden"
          style={squareProgressStyle}
        />
        
        {/* 图标 */}
        <ArrowUp 
          className="h-4 w-4 text-primary transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:scale-110" 
          strokeWidth={2.5}
        />
        
        {/* 内部发光效果 (仅在 dark 模式下明显) */}
        <div
          data-dashboard-back-to-top-glow="true"
          className="absolute inset-0 rounded-full bg-primary/5 opacity-0 group-hover:opacity-100 transition-opacity duration-300"
        />
      </Button>
    </div>
  )
}
