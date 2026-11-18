import { cn } from '@/lib/utils'

export function PromptWarningDot({
  message = 'Prompt 模板不兼容或缺少变量，请检查',
  className,
}: {
  message?: string
  className?: string
}) {
  return (
    <span
      role="img"
      aria-label={message}
      title={message}
      className={cn('inline-block h-2 w-2 shrink-0 rounded-full bg-yellow-500', className)}
    />
  )
}
