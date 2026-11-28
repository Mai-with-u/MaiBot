import { X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import type { ReactNode } from 'react'

import { MotionNumber } from '@/components/motion'
import { Button } from '@/components/ui/button'
import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

export interface ActionBarProps {
  open: boolean
  count?: number
  countLabel?: ReactNode
  leading?: ReactNode
  children: ReactNode
  onClear?: () => void
  clearLabel?: string
  /** floating: 底部居中悬浮弹条（不顶动页面布局）；inline: 随容器底/顶部平滑展开 */
  placement?: 'floating' | 'inline'
  className?: string
  'data-action-bar'?: string
}

/**
 * 悬浮操作弹条（DesignBookmark Morph Island / Dust Delete 风格）。
 * 用于批量选择、编辑态或撤销反馈，以弹性浮入和宽度自形变呈现，避免生硬顶动正文表格。
 */
export function ActionBar({
  open,
  count,
  countLabel,
  leading,
  children,
  onClear,
  clearLabel = '取消选择',
  placement = 'floating',
  className,
  'data-action-bar': dataActionBar,
}: ActionBarProps) {
  const { enabled, style, transitions } = useMotion()
  const isModern = style === 'modern'

  const content = (
    <motion.div
      key="action-bar"
      role="region"
      aria-label="批量操作栏"
      data-action-bar={dataActionBar ?? placement}
      layout={enabled}
      initial={
        enabled
          ? {
              opacity: 0,
              y: placement === 'floating' ? 18 : -6,
              scale: 0.96,
              filter: isModern ? 'blur(4px)' : 'blur(0px)',
            }
          : false
      }
      animate={{ opacity: 1, y: 0, scale: 1, filter: 'blur(0px)' }}
      exit={
        enabled
          ? {
              opacity: 0,
              y: placement === 'floating' ? 14 : -4,
              scale: 0.97,
              filter: isModern ? 'blur(3px)' : 'blur(0px)',
              transition: transitions.exit,
            }
          : undefined
      }
      transition={transitions.snappy}
      className={cn(
        'pointer-events-auto flex max-w-[calc(100vw-2rem)] flex-wrap items-center gap-2',
        placement === 'floating'
          ? isModern
            ? 'rounded-full border border-border/75 bg-card/92 px-3.5 py-2 shadow-xl backdrop-blur-xl'
            : 'rounded-lg border border-border bg-card px-3.5 py-2 shadow-lg'
          : 'rounded-lg border border-border/75 bg-card/95 px-3 py-2 shadow-sm',
        className
      )}
    >
      {leading}
      {(count !== undefined || countLabel) && (
        <div className="inline-flex items-center gap-1.5 pr-1 text-xs font-medium text-foreground">
          {count !== undefined && (
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary/14 px-1.5 text-xs font-semibold text-primary tabular-nums">
              <MotionNumber value={count} />
            </span>
          )}
          {countLabel && <span className="whitespace-nowrap">{countLabel}</span>}
        </div>
      )}
      {(count !== undefined || countLabel || leading) && (
        <span aria-hidden="true" className="h-4 w-px bg-border/80" />
      )}
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
      {onClear && (
        <>
          <span aria-hidden="true" className="h-4 w-px bg-border/80" />
          <Button
            type="button"
            variant="ghost"
            size="xs"
            onClick={onClear}
            className="gap-1 text-muted-foreground hover:text-foreground"
          >
            <X className="size-3.5" aria-hidden="true" />
            <span>{clearLabel}</span>
          </Button>
        </>
      )}
    </motion.div>
  )

  if (placement === 'inline') {
    return <AnimatePresence initial={false}>{open && content}</AnimatePresence>
  }

  return (
    <AnimatePresence initial={false}>
      {open && (
        <div className="pointer-events-none fixed inset-x-0 bottom-5 z-40 flex justify-center px-4 max-lg:bottom-24">
          {content}
        </div>
      )}
    </AnimatePresence>
  )
}
