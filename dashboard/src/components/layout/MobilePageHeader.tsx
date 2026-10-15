import { Link } from '@tanstack/react-router'
import { ChevronLeft } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import { MOBILE_GLASS_CLASS } from './mobile-glass'

interface MobilePageHeaderProps {
  /** 不传标题时只显示返回与操作栏，供自带标题的页面使用。 */
  title?: ReactNode
  /** 标题上方的一行小字，例如首页的日期 */
  eyebrow?: ReactNode
  /** 标题右侧的操作，例如首页头像或观察页的聊天流切换 */
  trailing?: ReactNode
  /** 左上角返回按钮的目标路径与无障碍名称 */
  back?: { to: string; label: string }
  /** 与返回按钮同一行、靠右的操作按钮 */
  backTrailing?: ReactNode
  className?: string
  children?: ReactNode
}

/**
 * 手机端的大标题页头：手机上隐藏了顶栏，由页面自己显示标题与返回按钮；
 * 千禧风格下由 millennium.css 画成像素字丝印与键帽。桌面端不显示。
 */
export function MobilePageHeader({
  title,
  eyebrow,
  trailing,
  back,
  backTrailing,
  className,
  children,
}: MobilePageHeaderProps) {

  return (
    <header
      data-mobile-page-header="true"
      className={cn('flex flex-col gap-3 px-1 lg:hidden', className)}
    >
      {back && (
        <div className="flex items-center justify-between gap-3">
          <Link
            to={back.to}
            aria-label={back.label}
            data-mobile-page-back="true"
            className={cn(
              'text-foreground flex h-11 w-11 items-center justify-center rounded-full',
              MOBILE_GLASS_CLASS
            )}
          >
            <ChevronLeft className="h-[22px] w-[22px]" strokeWidth={1.7} aria-hidden="true" />
          </Link>
          {backTrailing}
        </div>
      )}
      {(title || eyebrow || trailing) && (
        <div className="flex items-end justify-between gap-3">
          <div className="min-w-0">
            {eyebrow && (
              <div className="text-muted-foreground text-[13px] font-medium">{eyebrow}</div>
            )}
            {title && (
              <h1
                data-mobile-large-title="true"
                className="truncate text-[34px] leading-tight font-bold"
              >
                {title}
              </h1>
            )}
          </div>
          {trailing}
        </div>
      )}
      {children}
    </header>
  )
}
