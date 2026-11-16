import { Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export type ModelCreationType = 'llm' | 'embedding'

interface AddModelMenuProps {
  onAdd: (type: ModelCreationType) => void
  disabled: boolean
  className?: string
  dataTour?: string
}

/** Choose an LLM or embedding model and expose the missing-provider hint when creation is disabled. */
export function AddModelMenu({ onAdd, disabled, className, dataTour }: AddModelMenuProps) {
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={className}
            role={disabled ? 'button' : undefined}
            aria-disabled={disabled || undefined}
            aria-label={disabled ? '添加模型：请先添加供应商' : undefined}
            tabIndex={disabled ? 0 : undefined}
          >
            <DropdownMenuTrigger asChild>
              <Button size="sm" disabled={disabled} data-tour={dataTour}>
                <Plus className="h-4 w-4" strokeWidth={2} fill="none" />
                <span data-add-model-label="full">添加模型</span>
                <span data-add-model-label="compact" className="hidden">模型</span>
              </Button>
            </DropdownMenuTrigger>
          </span>
        </TooltipTrigger>
        {disabled && <TooltipContent>请先添加供应商</TooltipContent>}
      </Tooltip>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => onAdd('llm')}>添加 LLM 模型</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onAdd('embedding')}>添加嵌入模型</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
