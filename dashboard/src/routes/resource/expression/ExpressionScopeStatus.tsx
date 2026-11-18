import { StatusIndicator } from '@/components/ui/data-browser'

type ScopeStatus = 'on' | 'off' | 'mixed'

export function ExpressionScopeStatus({
  enableLearning,
  useExpression,
}: {
  enableLearning: ScopeStatus
  useExpression: ScopeStatus
}) {
  const statusLabel = (label: string, status: ScopeStatus) =>
    status === 'on' ? label : status === 'mixed' ? `部分${label}` : `${label}关闭`

  return (
    <span className="flex flex-col text-[9px] leading-[10px] whitespace-nowrap">
      <StatusIndicator status={enableLearning} variant="inline">
        {statusLabel('学习', enableLearning)}
      </StatusIndicator>
      <StatusIndicator status={useExpression} variant="inline">
        {statusLabel('使用', useExpression)}
      </StatusIndicator>
    </span>
  )
}
