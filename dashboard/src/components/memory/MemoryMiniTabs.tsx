import { TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

export interface MemoryMiniTabItem<TValue extends string> {
  value: TValue
  label: string
  description?: string
}

export interface MemoryMiniTabsProps<TValue extends string> {
  items: ReadonlyArray<MemoryMiniTabItem<TValue>>
  className?: string
  /** 触发器额外样式 */
  triggerClassName?: string
}

/**
 * 长期记忆控制台统一的迷你标签页样式。
 *
 * - 复用 shadcn `Tabs` 原语，仅替换样式以保留无障碍能力（`role="tab"` 与文案不变）。
 * - 胶囊形圆角轨道，借助 `TabsList` 内置 `ModernTabIndicator` 滑块高亮当前页签。
 */
export function MemoryMiniTabs<TValue extends string>({
  items,
  className,
  triggerClassName,
}: MemoryMiniTabsProps<TValue>) {
  return (
    <TabsList
      data-memory-mini-tabs="true"
      className={cn(
        'h-auto w-full flex-wrap justify-start gap-1 rounded-full border border-border/60',
        'bg-muted/55 p-1 shadow-none [&>[data-modern-tab-indicator="pill"]]:rounded-full',
        className,
      )}
    >
      {items.map((item) => (
        <TabsTrigger
          key={item.value}
          value={item.value}
          title={item.description}
          className={cn(
            'rounded-full px-3.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors',
            'hover:text-foreground data-[state=active]:text-foreground',
            triggerClassName,
          )}
        >
          {item.label}
        </TabsTrigger>
      ))}
    </TabsList>
  )
}
