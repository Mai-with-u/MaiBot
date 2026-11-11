import { Check } from 'lucide-react'
import { Fragment } from 'react'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'

/** 只读进度导航：复用按钮的主题材质，不允许跳过尚未保存的步骤。 */
export function StepIndicator({
  steps,
  currentStep,
  currentSubstep,
  label,
}: {
  steps: { id: string; label: string; substeps?: { id: string; label: string }[] }[]
  currentStep: number
  currentSubstep?: string
  label: string
}) {
  const active = steps[currentStep]
  const activeSubstep = active?.substeps?.findIndex((step) => step.id === currentSubstep) ?? -1
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
              aria-current={index === currentStep && !active?.substeps?.length ? 'step' : undefined}
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
      {!!active?.substeps?.length && (
        <ol aria-label={active.label} className="mt-3 flex flex-wrap justify-center gap-x-4 gap-y-2 text-xs sm:text-sm">
          {active.substeps.map((step, index) => (
            <li
              key={step.id}
              aria-current={step.id === currentSubstep ? 'step' : undefined}
              className={cn(
                'flex items-center gap-1.5 border-b-2 border-transparent px-1 pb-1',
                index === activeSubstep ? 'border-primary font-semibold' : 'text-muted-foreground'
              )}
            >
              {index < activeSubstep ? <Check className="size-3.5" aria-hidden /> : (
                <span className="font-mono tabular-nums" aria-hidden>{currentStep + 1}.{index + 1}</span>
              )}
              {step.label}
            </li>
          ))}
        </ol>
      )}
    </nav>
  )
}
