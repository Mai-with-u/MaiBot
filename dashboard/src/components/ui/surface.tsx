import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'

import { cn } from '@/lib/utils'

type SurfaceProps = React.HTMLAttributes<HTMLDivElement> & { asChild?: boolean }

/** 页面浏览区的外壳；只负责材质，尺寸和滚动区域由页面布局决定。 */
const PageFrame = React.forwardRef<HTMLDivElement, SurfaceProps & { desktopOnly?: boolean }>(
  ({ asChild = false, desktopOnly = false, className, ...props }, ref) => {
    const Comp = asChild ? Slot : 'div'
    return (
      <Comp
        ref={ref}
        data-dashboard-page-frame="true"
        data-desktop-only={desktopOnly}
        className={cn('min-w-0', className)}
        {...props}
      />
    )
  }
)
PageFrame.displayName = 'PageFrame'

/** 只读数据、表格和列表共用的内层显示区，不承担输入控件的交互。 */
const DataPanel = React.forwardRef<
  HTMLDivElement,
  SurfaceProps & { padding?: 'default' | 'none' | 'list'; inset?: boolean }
>(
  ({ asChild = false, padding = 'default', inset = false, className, ...props }, ref) => {
    const Comp = asChild ? Slot : 'div'
    return (
      <Comp
        ref={ref}
        data-dashboard-data-panel="true"
        data-padding={padding}
        data-inset={inset}
        className={cn('min-w-0', className)}
        {...props}
      />
    )
  }
)
DataPanel.displayName = 'DataPanel'

export { DataPanel, PageFrame }
