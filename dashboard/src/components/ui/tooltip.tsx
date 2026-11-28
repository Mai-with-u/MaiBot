import * as React from "react"
import * as TooltipPrimitive from "@radix-ui/react-tooltip"
import { animate } from "motion/react"

import { useMotion } from "@/lib/motion"
import { cn } from "@/lib/utils"

const TooltipProvider = TooltipPrimitive.Provider

const Tooltip = TooltipPrimitive.Root

const TooltipTrigger = TooltipPrimitive.Trigger

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => {
  const motion = useMotion()
  const motionRef = React.useRef(motion)
  React.useLayoutEffect(() => {
    motionRef.current = motion
  }, [motion])

  const animatedNodeRef = React.useRef<HTMLDivElement | null>(null)
  const controlsRef = React.useRef<ReturnType<typeof animate> | null>(null)

  React.useEffect(
    () => () => {
      controlsRef.current?.stop()
      controlsRef.current = null
      animatedNodeRef.current = null
    },
    []
  )

  const composedRef = React.useCallback(
    (node: HTMLDivElement | null) => {
      if (node) {
        // Radix Popper 重算定位时会轮换内联 ref 回调，导致 React 对同一 DOM 节点先传 null 再传 node；
        // 仅在真正挂载新节点时播放入场动画，避免提示气泡连闪两下。
        if (animatedNodeRef.current !== node) {
          animatedNodeRef.current = node
          controlsRef.current?.stop()
          controlsRef.current = null

          if (motionRef.current.enabled) {
            const { overlayScale, overlayY } = motionRef.current.profile.distances
            const offset = Math.max(2, Math.round(overlayY * 0.6))
            controlsRef.current = animate(
              node,
              { opacity: [0, 1], scale: [overlayScale, 1], y: [offset, 0] },
              motionRef.current.transitions.overlay
            )
          }
        }
      } else if (animatedNodeRef.current && !animatedNodeRef.current.isConnected) {
        controlsRef.current?.stop()
        controlsRef.current = null
        animatedNodeRef.current = null
      }

      if (typeof ref === "function") {
        ref(node)
      } else if (ref) {
        ref.current = node
      }
    },
    [ref]
  )

  return (
    <TooltipPrimitive.Portal>
      {/* TooltipPrimitive.Content 内部用 Slottable + VisuallyHidden，不能传 asChild，
          否则会与嵌套 react-slot 的本地 Symbol 冲突并抛出 React.Children.only。
          这里直接用 motion/react 的 animate 驱动同款入场曲线，退场交由 motion.css 衔接。 */}
      <TooltipPrimitive.Content
        ref={composedRef}
        data-dashboard-floating-content="true"
        data-motion-floating="tooltip"
        sideOffset={sideOffset}
        className={cn(
          "z-50 overflow-hidden rounded-md border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-lg origin-[var(--radix-tooltip-content-transform-origin)]",
          className
        )}
        {...props}
      />
    </TooltipPrimitive.Portal>
  )
})
TooltipContent.displayName = TooltipPrimitive.Content.displayName

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
