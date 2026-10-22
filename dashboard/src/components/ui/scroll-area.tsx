import * as React from "react"
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area"

import { cn } from "@/lib/utils"

interface ScrollAreaProps extends React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> {
  viewportRef?: React.RefObject<HTMLDivElement | null>
  viewportClassName?: string
  contentClassName?: string
  scrollbars?: "vertical" | "horizontal" | "both"
}

const ScrollArea = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.Root>,
  ScrollAreaProps
>(({ className, children, viewportRef, viewportClassName, contentClassName, scrollbars = "both", ...props }, ref) => (
  <ScrollAreaPrimitive.Root
    ref={ref}
    className={cn("relative overflow-hidden", className)}
    {...props}
  >
    <ScrollAreaPrimitive.Viewport
      ref={viewportRef}
      data-dashboard-scrollbar-viewport="true"
      className={cn(
        "h-full w-full overscroll-contain rounded-[inherit] [&>div]:!block [&>div]:!min-w-0 [&>div]:w-full",
        viewportClassName
      )}
    >
      <div className={cn("!block w-full !min-w-0", contentClassName)}>{children}</div>
    </ScrollAreaPrimitive.Viewport>
    {scrollbars !== "horizontal" && <ScrollBar />}
    {scrollbars !== "vertical" && <ScrollBar orientation="horizontal" />}
    <ScrollAreaPrimitive.Corner />
  </ScrollAreaPrimitive.Root>
))
ScrollArea.displayName = ScrollAreaPrimitive.Root.displayName

const ScrollBar = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>
>(({ className, orientation = "vertical", ...props }, ref) => (
  <ScrollAreaPrimitive.ScrollAreaScrollbar
    ref={ref}
    orientation={orientation}
    data-dashboard-scrollbar="true"
    className={cn(
      "flex touch-none select-none bg-[var(--dashboard-scrollbar-track-bg,transparent)] transition-colors",
      orientation === "vertical" &&
        "h-full w-[var(--dashboard-scrollbar-size,0.625rem)] border-l border-l-transparent p-px",
      orientation === "horizontal" &&
        "h-[var(--dashboard-scrollbar-size,0.625rem)] flex-col border-t border-t-transparent p-px",
      className
    )}
    {...props}
  >
    <ScrollAreaPrimitive.ScrollAreaThumb
      data-dashboard-scrollbar-thumb="true"
      className="relative flex-1 rounded-full bg-border"
    />
  </ScrollAreaPrimitive.ScrollAreaScrollbar>
))
ScrollBar.displayName = ScrollAreaPrimitive.ScrollAreaScrollbar.displayName

export { ScrollArea, ScrollBar }
