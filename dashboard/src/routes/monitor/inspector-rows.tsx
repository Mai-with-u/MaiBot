import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'
import '@/styles/data-rows.css'
import '@/styles/inspector-panel.css'

/** 检查器数据屏里的分节标题 */
export function InspectorSection({ title, meta }: { title: ReactNode; meta?: ReactNode }) {
  return (
    <div data-rows-group="true" className="flex items-center gap-2">
      <span>{title}</span>
      {meta !== undefined && <span className="ml-auto font-mono font-normal tracking-normal">{meta}</span>}
    </div>
  )
}

/** 检查器数据屏里的一行，行与行之间是虚线 */
export function InspectorRow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div data-rows-row="true" className={cn('flex min-w-0 items-center gap-2.5 px-3 py-2 text-xs', className)}>
      {children}
    </div>
  )
}
