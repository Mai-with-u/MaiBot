import { Check } from 'lucide-react'
import { Fragment } from 'react'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

/** 只读进度导航：复用按钮的主题材质，不允许跳过尚未保存的步骤。 */
export function StepIndicator({
  steps,
  currentStep,
  label,
}: {
  steps: { id: string; label: string }[]
  currentStep: number
  label: string
}) {
  return (
    <nav aria-label={label}>
      <ol className="flex items-center justify-center gap-3 sm:gap-4">
        {steps.map((step, index) => (
          <Fragment key={step.id}>
            {index > 0 && (
              <li aria-hidden="true" className="max-w-28 min-w-3 flex-1">
                <Separator variant="groove" />
              </li>
            )}
            <li
              aria-current={index === currentStep ? 'step' : undefined}
              className="flex min-w-0 flex-col items-center gap-2 sm:flex-row sm:gap-3"
            >
              <Button
                asChild
                size="icon-sm"
                variant={
                  index === currentStep ? 'default' : index < currentStep ? 'secondary' : 'outline'
                }
                className="pointer-events-none shrink-0"
              >
                <span aria-hidden="true">{index < currentStep ? <Check /> : index + 1}</span>
              </Button>
              <span
                className={cn(
                  'text-center text-xs font-semibold sm:text-base',
                  index !== currentStep && 'text-muted-foreground'
                )}
              >
                {step.label}
              </span>
            </li>
          </Fragment>
        ))}
      </ol>
    </nav>
  )
}
