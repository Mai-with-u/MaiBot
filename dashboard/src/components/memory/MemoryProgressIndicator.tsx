import { Loader2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'

export interface MemoryProgressIndicatorProps {
  /** 0-100 之间的进度百分比 */
  value: number
  /** 任务状态文本（如 “运行中”、“已完成”） */
  statusLabel?: string
  /** 当前步骤文本（如 “分块中”） */
  stepLabel?: string
  /** 状态对应的语义色（用于左侧圆环和徽标） */
  tone?: 'default' | 'success' | 'warning' | 'destructive' | 'muted'
  /** 是否显示加载动画（运行中/取消中场景） */
  busy?: boolean
  /** 紧凑模式：用于队列列表项 */
  compact?: boolean
  /** 额外说明（如 “已完成 36 / 120 分块”） */
  detail?: string
  className?: string
}

const TONE_BADGE_VARIANT: Record<
  NonNullable<MemoryProgressIndicatorProps['tone']>,
  'default' | 'secondary' | 'destructive' | 'outline'
> = {
  default: 'default',
  success: 'secondary',
  warning: 'outline',
  destructive: 'destructive',
  muted: 'outline',
}

/**
 * 长期记忆控制台统一的任务进度展示组件。
 *
 * 设计目标：
 *  - 让用户一眼看清「状态 + 当前步骤 + 单一线性进度条与百分比」。
 *  - 复用 shadcn `Progress` 与 `Badge`，避免引入额外样式来源。
 */
export function MemoryProgressIndicator({
  value,
  statusLabel,
  stepLabel,
  tone = 'default',
  busy = false,
  compact = false,
  detail,
  className,
}: MemoryProgressIndicatorProps) {
  const safeValue = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0

  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex flex-wrap items-center gap-2">
        {busy ? (
          <Loader2
            className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground"
            aria-hidden="true"
          />
        ) : null}
        {statusLabel ? (
          <Badge variant={TONE_BADGE_VARIANT[tone]} className="shrink-0">
            {statusLabel}
          </Badge>
        ) : null}
        {stepLabel ? (
          <span className="truncate text-xs text-muted-foreground">{stepLabel}</span>
        ) : null}
        <span className="ml-auto text-xs font-medium tabular-nums text-muted-foreground">
          {compact ? `${Math.round(safeValue)}%` : `${safeValue.toFixed(1)}%`}
        </span>
      </div>
      <Progress value={safeValue} className={cn(compact ? 'h-1' : 'h-1.5')} />
      {detail ? <div className="truncate text-xs text-muted-foreground">{detail}</div> : null}
    </div>
  )
}
