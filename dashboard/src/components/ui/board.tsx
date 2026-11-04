import type { CSSProperties, ReactNode } from 'react'

import { cn } from '@/lib/utils'
import '@/styles/board.css'

/**
 * 面板：一组模块共用的 12 列网格。材质（千禧机壳、复古纸面、现代卡片）由主题决定，
 * 页面只描述模块占几列、放什么内容。面板变窄时模块改用 spanNarrow，行高按内容撑开。
 */
export function Board({
  rows,
  children,
  className,
}: {
  /** 宽面板的 grid-template-rows，未提供时按内容撑高；窄面板始终按内容撑高 */
  rows?: string
  children: ReactNode
  className?: string
}) {
  // 外层框只用来做容器查询：面板按自身宽度切换宽 / 窄排法，而不是按窗口宽度
  return (
    <div data-board-frame="true" className={cn('min-h-0', className)}>
      <div data-board="true" style={rows ? ({ '--board-rows': rows } as CSSProperties) : undefined}>
        {children}
      </div>
    </div>
  )
}

/** 面板里的一个模块：统一高度的标题条 + 撑满剩余高度的内容区。 */
export function BoardModule({
  code,
  title,
  actions,
  span = 3,
  spanNarrow,
  tone = 0,
  children,
  className,
  'aria-label': ariaLabel,
}: {
  /** 英文丝印代号，如 STATUS；现代风格不显示 */
  code: string
  title: string
  actions?: ReactNode
  span?: number
  /** 面板变窄时占的列数，默认是 span 的两倍（最多 12） */
  spanNarrow?: number
  /** 复古风格里代号色块的配色序号 */
  tone?: number
  children: ReactNode
  className?: string
  'aria-label'?: string
}) {
  return (
    <section
      data-board-module="true"
      data-board-tone={tone % 3}
      aria-label={ariaLabel ?? title}
      className={cn('min-h-0 min-w-0', className)}
      style={{ '--board-span': span, '--board-span-narrow': spanNarrow ?? Math.min(12, span * 2) } as CSSProperties}
    >
      <header data-board-module-head="true">
        <span data-board-module-label="true">
          <span data-board-module-code="true">{code}</span>
          <span data-board-module-title="true">{title}</span>
        </span>
        {actions && <span data-board-module-actions="true">{actions}</span>}
      </header>
      {children}
    </section>
  )
}

/** 模块里的显示屏：千禧风格是下沉屏幕，复古与现代是平面。 */
export function BoardScreen({
  children,
  className,
  flush = false,
}: {
  children: ReactNode
  className?: string
  /** 内容自带排版（如键帽、图表）时不加内边距与底色 */
  flush?: boolean
}) {
  return (
    <div data-board-screen="true" data-flush={flush ? 'true' : 'false'} className={cn('min-h-0 flex-1', className)}>
      {children}
    </div>
  )
}
