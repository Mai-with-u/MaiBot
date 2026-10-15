import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { GripHorizontal, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface FloatingPanelPosition {
  left: number
  top: number
}

interface FloatingPanelProps {
  open?: boolean
  title: string
  subtitle?: string
  children: ReactNode
  actions?: ReactNode
  onClose: () => void
  closeLabel?: string
  className?: string
  initialWidth?: number
  initialHeight?: number
  initialTop?: number
  initialRight?: number
  /** 手机（lg 以下）铺满整屏并改用大标题 */
  mobileFullscreen?: boolean
}

const PANEL_MARGIN = 16

function clampPanelValue(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function getInitialPanelPosition(width: number, top: number, right: number): FloatingPanelPosition {
  if (typeof window === 'undefined') {
    return { left: 320, top }
  }

  return {
    left: Math.max(PANEL_MARGIN, window.innerWidth - width - right),
    top: Math.max(PANEL_MARGIN, top),
  }
}

export function FloatingPanel({
  open = true,
  title,
  subtitle,
  children,
  actions,
  onClose,
  closeLabel = '关闭',
  className,
  initialWidth = 560,
  initialHeight = 620,
  initialTop = 112,
  initialRight = 32,
  mobileFullscreen = false,
}: FloatingPanelProps) {
  const [dragging, setDragging] = useState(false)
  const [position, setPosition] = useState<FloatingPanelPosition>(() =>
    getInitialPanelPosition(initialWidth, initialTop, initialRight)
  )
  const panelRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<{
    pointerId: number
    offsetX: number
    offsetY: number
  } | null>(null)

  const clampPosition = useCallback(
    (left: number, top: number): FloatingPanelPosition => {
      const panelRect = panelRef.current?.getBoundingClientRect()
      const panelWidth = panelRect?.width ?? initialWidth
      const panelHeight = panelRect?.height ?? initialHeight
      const maxLeft = Math.max(PANEL_MARGIN, window.innerWidth - PANEL_MARGIN - panelWidth)
      const maxTop = Math.max(PANEL_MARGIN, window.innerHeight - PANEL_MARGIN - panelHeight)

      return {
        left: clampPanelValue(left, PANEL_MARGIN, maxLeft),
        top: clampPanelValue(top, PANEL_MARGIN, maxTop),
      }
    },
    [initialHeight, initialWidth]
  )

  useEffect(() => {
    if (!open) {
      return
    }

    const handleResize = () => setPosition((current) => clampPosition(current.left, current.top))
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [clampPosition, open])

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || dragRef.current) {
      return
    }
    const rect = panelRef.current?.getBoundingClientRect()
    if (!rect) {
      return
    }

    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    }
    setDragging(true)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const dragState = dragRef.current
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return
    }
    setPosition(clampPosition(event.clientX - dragState.offsetX, event.clientY - dragState.offsetY))
  }

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) {
      return
    }
    dragRef.current = null
    setDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  if (!open) {
    return null
  }

  const panel = (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="false"
      aria-label={title}
      data-dashboard-floating-content="true"
      className={cn(
        'bg-background fixed z-50 overflow-hidden rounded-md border shadow-2xl',
        className,
        // 手机上铺满整屏，像原生应用的独立页面，而不是一块可拖动的小窗
        mobileFullscreen &&
          'max-lg:top-0! max-lg:left-0! max-lg:h-[100dvh]! max-lg:w-full! max-lg:rounded-none max-lg:border-0 max-lg:shadow-none'
      )}
      style={{ left: position.left, top: position.top }}
    >
      <div
        data-floating-panel-header="true"
        className={cn(
          'bg-muted/70 flex touch-none items-center gap-2 border-b px-3 py-2 select-none',
          dragging ? 'cursor-grabbing' : 'cursor-grab',
          // 手机上标题栏改为大标题，不再可拖动
          mobileFullscreen &&
            'max-lg:pointer-events-none max-lg:border-b-0 max-lg:bg-transparent max-lg:px-5 max-lg:pt-[calc(env(safe-area-inset-top)+16px)] max-lg:pb-2'
        )}
        onPointerCancel={endDrag}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
      >
        <GripHorizontal
          className={cn(
            'text-muted-foreground h-4 w-4 shrink-0',
            mobileFullscreen && 'max-lg:hidden'
          )}
        />
        <div className="min-w-0 flex-1">
          <div
            className={cn(
              'truncate text-sm font-semibold',
              mobileFullscreen &&
                'max-lg:text-[34px] max-lg:leading-tight max-lg:font-bold max-lg:tracking-tight'
            )}
          >
            {title}
          </div>
          {subtitle && <div className="text-muted-foreground truncate text-xs">{subtitle}</div>}
        </div>
        <div
          className="pointer-events-auto flex shrink-0 items-center gap-1"
          onPointerDown={(event) => event.stopPropagation()}
        >
          {actions}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={onClose}
            aria-label={closeLabel}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      {children}
    </div>
  )

  if (typeof document === 'undefined') {
    return panel
  }
  return createPortal(panel, document.body)
}
