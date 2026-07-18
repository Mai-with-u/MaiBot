import { useToast } from "@/hooks/use-toast"
import {
  Toast,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastTitle,
  ToastViewport,
} from "@/components/ui/toast"
import { useIsMobile } from "@/hooks/use-media-query"

const DEFAULT_TOAST_DURATION = 5000
const DESTRUCTIVE_TOAST_DURATION = 10000

export function Toaster() {
  const { toasts } = useToast()
  const isMobile = useIsMobile()

  return (
    <ToastProvider swipeDirection={isMobile ? "up" : "right"}>
      {toasts.map(function ({ id, title, description, action, duration, variant, ...props }) {
        const toastDuration =
          typeof duration === "number"
            ? duration
            : variant === "destructive"
              ? DESTRUCTIVE_TOAST_DURATION
              : DEFAULT_TOAST_DURATION

        return (
          <Toast key={id} duration={toastDuration} variant={variant} {...props}>
            <div className="grid min-w-0 gap-1 select-text">
              {title && <ToastTitle>{title}</ToastTitle>}
              {description && (
                <ToastDescription>{description}</ToastDescription>
              )}
            </div>
            {action}
            <ToastClose />
          </Toast>
        )
      })}
      <ToastViewport />
    </ToastProvider>
  )
}
