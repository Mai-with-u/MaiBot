import * as React from "react"
import * as PopoverPrimitive from "@radix-ui/react-popover"

import { MotionFloating } from "@/components/motion"
import { cn } from "@/lib/utils"

const Popover = PopoverPrimitive.Root

const PopoverTrigger = PopoverPrimitive.Trigger

const PopoverAnchor = PopoverPrimitive.Anchor

const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, children, align = "center", sideOffset = 4, collisionPadding = 16, onWheel, onTouchMove, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      align={align}
      sideOffset={sideOffset}
      collisionPadding={collisionPadding}
      asChild
      {...props}
    >
      <MotionFloating
        variant="popover"
        data-dashboard-floating-content="true"
        className={cn(
          "z-[60] w-72 rounded-md border bg-popover p-4 text-popover-foreground shadow-md outline-none origin-[var(--radix-popover-content-transform-origin)]",
          className
        )}
        onWheel={(event) => {
          onWheel?.(event)
          event.stopPropagation()
        }}
        onTouchMove={(event) => {
          onTouchMove?.(event)
          event.stopPropagation()
        }}
      >
        {children}
      </MotionFloating>
    </PopoverPrimitive.Content>
  </PopoverPrimitive.Portal>
))
PopoverContent.displayName = PopoverPrimitive.Content.displayName

export { Popover, PopoverTrigger, PopoverContent, PopoverAnchor }
