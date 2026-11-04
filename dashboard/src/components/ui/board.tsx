import type { CSSProperties, HTMLAttributes, ReactNode } from 'react'

import { cn } from '@/lib/utils'
import '@/styles/board.css'

/**
 * 面板：一整块机壳（千禧）/ 纸面（复古）/ 卡片底（现代），里面放 12 列网格。
 * 材质由主题决定，页面只描述每个模块占几列、放什么内容。
 * 默认整块面板就是一段网格；传 sections 时由页面自己用 BoardGrid / BoardRule 分段。
 */
export function Board({
  rows,
  sections = false,
  children,
  className,
}: {
  /** 单段面板的 grid-template-rows，未提供时按内容撑高 */
  rows?: string
  /** 子元素是 BoardGrid / BoardRule 组成的若干段，而不是直接放模块 */
  sections?: boolean
  children: ReactNode
  className?: string
}) {
  // 外层框只用来做容器查询：面板按自身宽度切换宽 / 中 / 窄排法，而不是按窗口宽度
  return (
    <div data-board-frame="true" className={cn('min-h-0', className)}>
      <div data-board="true">
        {sections ? children : <BoardGrid rows={rows} grow>{children}</BoardGrid>}
      </div>
    </div>
  )
}

/** 面板里的一段 12 列网格。行高由页面给出；面板变窄时模块改用 spanNarrow，行高按内容撑开。 */
export function BoardGrid({
  rows,
  grow = false,
  children,
  className,
}: {
  /** grid-template-rows，未提供时按内容撑高 */
  rows?: string
  /** 这一段吃掉面板剩余高度（放图表的那一段） */
  grow?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <div
      data-board-grid="true"
      data-grow={grow ? 'true' : 'false'}
      className={className}
      style={rows ? ({ '--board-rows': rows } as CSSProperties) : undefined}
    >
      {children}
    </div>
  )
}

/** 面板上压的一道刻线：左端印代号，右端可以装一个控件（如统计周期）。不另起一层框。 */
export function BoardRule({ code, children }: { code: string; children?: ReactNode }) {
  return (
    <div data-board-rule="true">
      <span data-board-rule-code="true">{code}</span>
      <span data-board-rule-line="true" aria-hidden="true" />
      {children}
    </div>
  )
}

/** 面板里的一个模块：统一高度的标题条 + 撑满剩余高度的内容区。 */
export function BoardModule({
  code,
  title,
  actions,
  leading,
  span = 3,
  spanNarrow,
  tone = 0,
  children,
  className,
  style,
  'aria-label': ariaLabel,
  ...rest
}: {
  /** 英文丝印代号，如 STATUS；现代风格不显示 */
  code: string
  title: string
  actions?: ReactNode
  /** 标题条最左侧的附加元素（如编辑态的拖动柄） */
  leading?: ReactNode
  span?: number
  /** 面板变窄时占的列数，默认是 span 的两倍（最多 12） */
  spanNarrow?: number
  /** 复古风格里代号色块的配色序号 */
  tone?: number
  children: ReactNode
} & Omit<HTMLAttributes<HTMLElement>, 'title' | 'children'>) {
  return (
    <section
      {...rest}
      data-board-module="true"
      data-board-tone={tone % 3}
      aria-label={ariaLabel ?? title}
      className={cn('min-h-0 min-w-0', className)}
      style={
        {
          ...style,
          '--board-span': span,
          '--board-span-narrow': spanNarrow ?? Math.min(12, span * 2),
        } as CSSProperties
      }
    >
      <header data-board-module-head="true">
        {leading}
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
