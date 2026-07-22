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
  "group pointer-events-auto relative flex w-full items-center justify-between space-x-2 overflow-hidden rounded-md border p-4 pr-6 shadow-lg transition-all",
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
>(({ children, className, duration, onPause, onResume, variant, ...props }, ref) => {
  const isMobile = useIsMobile()
  // Radix 会在悬停、聚焦或窗口失焦时暂停关闭计时，进度动画需要同步暂停。
  const [isPaused, setIsPaused] = React.useState(false)
  const position = isMobile ? "mobile" : "desktop"
  
  return (
    <ToastPrimitives.Root
      ref={ref}
      data-dashboard-toast="true"
      className={cn(toastVariants({ variant, position }), className)}
      duration={duration}
      onPause={() => {
        setIsPaused(true)
        onPause?.()
      }}
      onResume={() => {
        setIsPaused(false)
        onResume?.()
      }}
      {...props}
    >
      {children}
      {typeof duration === "number" && Number.isFinite(duration) && duration > 0 && (
        <ToastProgress duration={duration} paused={isPaused} />
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
      "absolute right-1 top-1 rounded-md p-1 text-foreground/50 opacity-0 transition-opacity hover:text-foreground focus:opacity-100 focus:outline-none focus:ring-1 group-hover:opacity-100 group-focus-within:opacity-100 group-[.destructive]:text-red-300 group-[.destructive]:hover:text-red-50 group-[.destructive]:focus:ring-red-400 group-[.destructive]:focus:ring-offset-red-600",
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
  paused?: boolean
}

function ToastProgress({ className, duration, paused = false, style, ...props }: ToastProgressProps) {
  return (
    <div
      aria-hidden="true"
      data-dashboard-toast-progress="true"
      className={cn(
        "toast-progress pointer-events-none absolute right-0 bottom-0 left-0 h-1 origin-left bg-primary/70 group-[.destructive]:bg-destructive/70",
        className
      )}
      style={{
        ...style,
        animationDuration: `${duration}ms`,
        animationPlayState: paused ? "paused" : "running",
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
