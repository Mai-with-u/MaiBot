import * as React from "react"
import * as ToastPrimitives from "@radix-ui/react-toast"
import { cva, type VariantProps } from "class-variance-authority"
import { X } from "lucide-react"

import { useIsMobile } from "@/hooks/use-media-query"
import { cn } from "@/lib/utils"

const ToastProvider = ToastPrimitives.Provider

const ToastViewport = React.forwardRef<
  React.ElementRef<typeof ToastPrimitives.Viewport>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitives.Viewport>
>(({ className, ...props }, ref) => {
  const isMobile = useIsMobile()
  
  return (
    <ToastPrimitives.Viewport
      ref={ref}
      data-dashboard-toast-viewport="true"
      className={cn(
        "fixed z-[100] flex max-h-screen w-full gap-2 p-4 pointer-events-none",
        isMobile 
          ? "top-0 left-0 right-0 flex-col items-center" 
          : "top-0 right-0 flex-col sm:max-w-[420px]",
        className
      )}
      {...props}
    />
  )
})
ToastViewport.displayName = ToastPrimitives.Viewport.displayName

const toastVariants = cva(
  "group pointer-events-auto relative flex w-full items-center justify-between gap-2 overflow-hidden rounded-md border py-2 pl-3 pr-14 shadow-lg transition-all",
  {
    variants: {
      variant: {
        default: "border bg-primary/5 text-foreground backdrop-blur-sm",
        destructive:
          "destructive group border-destructive bg-destructive/10 text-destructive-foreground backdrop-blur-sm",
      },
      position: {
        desktop: "data-[swipe=cancel]:translate-x-0 data-[swipe=end]:translate-x-[var(--radix-toast-swipe-end-x)] data-[swipe=move]:translate-x-[var(--radix-toast-swipe-move-x)] data-[swipe=move]:transition-none data-[state=open]:animate-slide-in-from-right data-[state=open]:animate-fade-in data-[state=closed]:animate-slide-out-to-right data-[state=closed]:animate-fade-out data-[swipe=end]:animate-slide-out-to-right",
        mobile: "data-[swipe=cancel]:translate-y-0 data-[swipe=end]:translate-y-[var(--radix-toast-swipe-end-y)] data-[swipe=move]:translate-y-[var(--radix-toast-swipe-move-y)] data-[swipe=move]:transition-none data-[state=open]:animate-slide-in-from-top data-[state=open]:animate-fade-in data-[state=closed]:animate-slide-out-to-top data-[state=closed]:animate-fade-out data-[swipe=end]:animate-slide-out-to-top",
      },
    },
    defaultVariants: {
      variant: "default",
      position: "desktop",
    },
  }
)

const Toast = React.forwardRef<
  React.ElementRef<typeof ToastPrimitives.Root>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitives.Root> &
    VariantProps<typeof toastVariants>
>(({ children, className, duration = 4000, onPause, onResume, onOpenChange, open, defaultOpen = true, variant, ...props }, ref) => {
  const isMobile = useIsMobile()
  // 接管 Radix 的自动关闭计时：悬停、聚焦或窗口失焦时减速，进度动画同步减速。
  const [isSlow, setIsSlow] = React.useState(false)
  const [internalOpen, setInternalOpen] = React.useState(defaultOpen)
  const isOpen = open ?? internalOpen
  const remainingRef = React.useRef(duration)
  const onOpenChangeRef = React.useRef(onOpenChange)
  onOpenChangeRef.current = onOpenChange
  const position = isMobile ? "mobile" : "desktop"

  React.useEffect(() => {
    remainingRef.current = duration
  }, [duration, isOpen])

  React.useEffect(() => {
    if (!isOpen || !Number.isFinite(duration) || duration <= 0) return
    const speed = isSlow ? 1 / 3 : 1
    const startedAt = performance.now()
    const timer = window.setTimeout(() => {
      setInternalOpen(false)
      onOpenChangeRef.current?.(false)
    }, remainingRef.current / speed)
    return () => {
      window.clearTimeout(timer)
      remainingRef.current = Math.max(0, remainingRef.current - (performance.now() - startedAt) * speed)
    }
  }, [duration, isOpen, isSlow])
  
  return (
    <ToastPrimitives.Root
      ref={ref}
      data-dashboard-toast="true"
      className={cn(toastVariants({ variant, position }), className)}
      duration={Infinity}
      open={isOpen}
      onOpenChange={(nextOpen) => {
        setInternalOpen(nextOpen)
        onOpenChange?.(nextOpen)
      }}
      onPause={() => {
        setIsSlow(true)
        onPause?.()
      }}
      onResume={() => {
        setIsSlow(false)
        onResume?.()
      }}
      {...props}
    >
      {children}
      {typeof duration === "number" && Number.isFinite(duration) && duration > 0 && (
        <ToastProgress duration={duration} slow={isSlow} open={isOpen} />
      )}
    </ToastPrimitives.Root>
  )
})
Toast.displayName = ToastPrimitives.Root.displayName

const ToastAction = React.forwardRef<
  React.ElementRef<typeof ToastPrimitives.Action>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitives.Action>
>(({ className, ...props }, ref) => (
  <ToastPrimitives.Action
    ref={ref}
    data-dashboard-toast-action="true"
    className={cn(
      "inline-flex h-8 shrink-0 items-center justify-center rounded-md border bg-transparent px-3 text-sm font-medium transition-colors hover:bg-secondary focus:outline-none focus:ring-1 focus:ring-ring disabled:pointer-events-none disabled:opacity-50 group-[.destructive]:border-muted/40 group-[.destructive]:hover:border-destructive/30 group-[.destructive]:hover:bg-destructive group-[.destructive]:hover:text-destructive-foreground group-[.destructive]:focus:ring-destructive",
      className
    )}
    {...props}
  />
))
ToastAction.displayName = ToastPrimitives.Action.displayName

const ToastClose = React.forwardRef<
  React.ElementRef<typeof ToastPrimitives.Close>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitives.Close>
>(({ className, ...props }, ref) => (
  <ToastPrimitives.Close
    ref={ref}
    data-dashboard-toast-close="true"
    className={cn(
      "absolute inset-y-0 right-0 flex w-11 cursor-pointer items-center justify-center border-l border-current/15 text-foreground/60 transition-colors hover:bg-foreground/10 hover:text-foreground focus-visible:bg-foreground/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring group-[.destructive]:text-red-300 group-[.destructive]:hover:bg-destructive/20 group-[.destructive]:hover:text-red-50 group-[.destructive]:focus-visible:ring-red-400",
      className
    )}
    aria-label="关闭提示"
    toast-close=""
    {...props}
  >
    <X className="h-4 w-4" />
  </ToastPrimitives.Close>
))
ToastClose.displayName = ToastPrimitives.Close.displayName

const ToastTitle = React.forwardRef<
  React.ElementRef<typeof ToastPrimitives.Title>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitives.Title>
>(({ className, ...props }, ref) => (
  <ToastPrimitives.Title
    ref={ref}
    data-dashboard-toast-title="true"
    className={cn("text-sm font-semibold [&+div]:text-xs", className)}
    {...props}
  />
))
ToastTitle.displayName = ToastPrimitives.Title.displayName

const ToastDescription = React.forwardRef<
  React.ElementRef<typeof ToastPrimitives.Description>,
  React.ComponentPropsWithoutRef<typeof ToastPrimitives.Description>
>(({ className, ...props }, ref) => (
  <ToastPrimitives.Description
    ref={ref}
    data-dashboard-toast-description="true"
    className={cn("select-text text-sm opacity-90", className)}
    {...props}
  />
))
ToastDescription.displayName = ToastPrimitives.Description.displayName

interface ToastProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  duration: number
  slow: boolean
  open: boolean
}

function ToastProgress({ className, duration, slow, open, style, ...props }: ToastProgressProps) {
  const progressRef = React.useRef<HTMLDivElement>(null)
  const animationRef = React.useRef<Animation | null>(null)

  React.useEffect(() => {
    if (!open) return
    const animation = progressRef.current!.animate(
      [{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }],
      { duration, easing: "linear", fill: "forwards" }
    )
    animationRef.current = animation
    return () => {
      animation.cancel()
      animationRef.current = null
    }
  }, [duration, open])

  React.useEffect(() => {
    animationRef.current?.updatePlaybackRate(slow ? 1 / 3 : 1)
  }, [slow, duration, open])

  return (
    <div
      ref={progressRef}
      aria-hidden="true"
      data-dashboard-toast-progress="true"
      className={cn(
        "pointer-events-none absolute right-0 bottom-0 left-0 h-1 origin-left bg-primary/70 group-[.destructive]:bg-destructive/70",
        className
      )}
      style={{
        ...style,
      }}
      {...props}
    />
  )
}

type ToastProps = React.ComponentPropsWithoutRef<typeof Toast>

type ToastActionElement = React.ReactElement<typeof ToastAction>

export {
  type ToastProps,
  type ToastActionElement,
  ToastProvider,
  ToastViewport,
  Toast,
  ToastTitle,
  ToastDescription,
  ToastClose,
  ToastAction,
}
