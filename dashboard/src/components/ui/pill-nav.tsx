import { motion } from 'motion/react'
import { useId } from 'react'
import type { ReactNode } from 'react'

import { useMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'

export interface PillNavOption<T extends string = string> {
  value: T
  label: ReactNode
  icon?: ReactNode
  count?: number
  ariaLabel?: string
  title?: string
  disabled?: boolean
}

export interface PillNavProps<T extends string = string> {
  value: T
  options: PillNavOption<T>[]
  onChange: (value: T) => void
  ariaLabel?: string
  as?: 'div' | 'nav'
  variant?: 'segmented' | 'pill' | 'underline'
  size?: 'xs' | 'sm' | 'default'
  scrollable?: boolean
  className?: string
  itemClassName?: string
  'data-pill-nav'?: string
  'data-stats-switch'?: string
}

const SIZE_CLASSES: Record<'xs' | 'sm' | 'default', string> = {
  xs: 'h-7 px-2.5 text-xs gap-1.5',
  sm: 'h-8 px-3 text-xs gap-1.5',
  default: 'h-9 px-3.5 text-sm gap-2',
}

/**
 * 公共动效导航条（DesignBookmark Tab Pill / Segmented Control 风格）。
 * modern 主题下采用共享 layoutId 弹性滑块；千禧与复古主题自动退化为各自原生表面样式。
 */
export function PillNav<T extends string = string>({
  value,
  options,
  onChange,
  ariaLabel,
  as = 'div',
  variant = 'segmented',
  size = 'sm',
  scrollable = false,
  className,
  itemClassName,
  'data-stats-switch': dataStatsSwitch,
}: PillNavProps<T>) {
  const { enabled, style, transitions } = useMotion()
  const navId = useId()
  const useGlide = style === 'modern' && enabled
  const Wrapper = as

  const trackClassName = cn(
    'relative inline-flex max-w-full items-center',
    scrollable
      ? 'flex-nowrap overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden'
      : 'flex-wrap',
    variant === 'segmented' &&
      'rounded-lg border border-border/70 bg-muted/60 p-[3px] gap-0.5 dark:border-white/[0.08] dark:bg-white/[0.045]',
    variant === 'pill' &&
      'rounded-full border border-border/70 bg-muted/55 p-1 gap-0.5 dark:border-white/[0.08] dark:bg-white/[0.045]',
    variant === 'underline' && 'border-b border-border/70 gap-1 pb-0',
    className
  )

  return (
    <Wrapper
      role={as === 'div' ? 'group' : undefined}
      aria-label={ariaLabel}
      data-pill-nav={variant}
      data-stats-switch={dataStatsSwitch}
      className={trackClassName}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            aria-label={option.ariaLabel}
            title={option.title ?? option.ariaLabel}
            disabled={option.disabled}
            data-active={active ? 'true' : 'false'}
            onClick={() => onChange(option.value)}
            className={cn(
              'relative inline-flex shrink-0 cursor-pointer items-center justify-center whitespace-nowrap font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-45',
              SIZE_CLASSES[size],
              variant === 'segmented' && 'rounded-[5px]',
              variant === 'pill' && 'rounded-full',
              variant === 'underline' && 'rounded-t-md pb-2 pt-1.5',
              active
                ? 'text-foreground'
                : 'text-muted-foreground hover:text-foreground',
              !useGlide &&
                active &&
                (variant === 'underline'
                  ? 'border-b-2 border-primary text-foreground'
                  : 'bg-background text-foreground shadow-xs ring-1 ring-border/70 dark:bg-white/[0.12] dark:ring-white/[0.1] dark:shadow-[0_1px_4px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.09)]'),
              itemClassName
            )}
          >
            {useGlide && active && (
              <motion.span
                layoutId={`pill-nav-indicator-${navId}`}
                aria-hidden="true"
                transition={transitions.snappy}
                className={cn(
                  'pointer-events-none absolute z-0',
                  variant === 'underline'
                    ? 'inset-x-1.5 bottom-0 h-[2px] rounded-full bg-primary'
                    : variant === 'pill'
                      ? 'inset-0 rounded-full bg-background shadow-xs ring-1 ring-border/60 dark:bg-white/[0.12] dark:ring-white/[0.1] dark:shadow-[0_1px_4px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.09)]'
                      : 'inset-0 rounded-[5px] bg-background shadow-xs ring-1 ring-border/60 dark:bg-white/[0.12] dark:ring-white/[0.1] dark:shadow-[0_1px_4px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.09)]'
                )}
              />
            )}
            <span className="relative z-10 inline-flex items-center gap-1.5">
              {option.icon && (
                <span className="inline-flex shrink-0 items-center" aria-hidden="true">
                  {option.icon}
                </span>
              )}
              <span>{option.label}</span>
              {option.count !== undefined && (
                <span
                  className={cn(
                    'tabular-nums text-xs transition-opacity',
                    active ? 'text-foreground/75' : 'text-muted-foreground/80'
                  )}
                >
                  {option.count}
                </span>
              )}
            </span>
          </button>
        )
      })}
    </Wrapper>
  )
}
